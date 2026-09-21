"""
Tests for native cryptographically signed flash notifications (ORC-42).

Verifies:
1. Cryptographic tamper-proofing via itsdangerous (tampered payload, invalid signature, wrong secret, expired token).
2. Display exactly once lifecycle (renders on next page view and clears).
3. Clearing on subsequent navigation.
4. Real route integration (password update, alert settings, auth expiry redirect, web login, logout).
5. HTMX dismissal endpoint (/api/dismiss-flash).
6. Environment-aware cookie security attributes.
"""

import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi.testclient import TestClient
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings
from app.core.flash import (
    FLASH_COOKIE_NAME,
    FLASH_COOKIE_SALT,
    FlashCategory,
    FlashMessage,
    clear_flash_cookie,
    decode_flash_messages,
    encode_flash_messages,
    flash,
    get_flash_cookie_options,
    get_flash_serializer,
    get_flashes,
    normalize_category,
    pop_flashes,
    safe_decode_flash_messages,
    set_flash_cookie,
)
from app.db.database import get_user_supabase_client
from main import app


def create_token(
    sub: str = "test-user-1234",
    email: str = "test@example.com",
    expires_in: int = 3600,
) -> str:
    """Helper to generate HS256 JWT tokens with the test secret."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, settings.sb_jwt_secret, algorithm="HS256")


@pytest.fixture
def valid_token() -> str:
    return create_token(expires_in=3600)


@pytest.fixture
def expired_token() -> str:
    return create_token(expires_in=-3600)


@pytest.fixture
def production_env(monkeypatch):
    """Temporarily configure application in production mode."""
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
# 1. Cryptographic Tamper-Proofing Tests
# ============================================================================


def test_flash_message_encoding_and_decoding():
    """Verify flash messages encode to signed tokens and decode accurately."""
    messages = [
        FlashMessage(text="Settings saved successfully.", category="success"),
        {"type": "warning", "text": "Low disk space."},
    ]
    token = encode_flash_messages(messages, secret_key="test-secret")
    assert isinstance(token, str)
    assert len(token) > 0

    decoded = decode_flash_messages(token, secret_key="test-secret")
    assert len(decoded) == 2
    assert decoded[0].text == "Settings saved successfully."
    assert decoded[0].type == "success"
    assert decoded[0].category == "success"
    assert decoded[1].text == "Low disk space."
    assert decoded[1].type == "warning"


def test_tampered_flash_token_raises_bad_signature():
    """Verify tampering with token payload or signature raises BadSignature and is rejected."""
    messages = [{"type": "error", "text": "Unauthorized access"}]
    token = encode_flash_messages(messages, secret_key="test-secret")

    # Modify signature
    tampered_sig = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "ZZZZ")
    with pytest.raises(BadSignature):
        decode_flash_messages(tampered_sig, secret_key="test-secret")

    # Modify payload prefix
    tampered_payload = "X" + token[1:]
    with pytest.raises(BadSignature):
        decode_flash_messages(tampered_payload, secret_key="test-secret")

    # safe_decode_flash_messages safely returns empty list for tampered tokens
    assert safe_decode_flash_messages(tampered_sig, secret_key="test-secret") == []
    assert safe_decode_flash_messages(tampered_payload, secret_key="test-secret") == []


def test_token_signed_with_wrong_secret_is_rejected():
    """Verify tokens signed with an unknown or attacker key cannot be verified."""
    messages = [{"type": "info", "text": "Legitimate notice"}]
    attacker_token = encode_flash_messages(messages, secret_key="attacker-secret-key")

    with pytest.raises(BadSignature):
        decode_flash_messages(attacker_token, secret_key="real-server-secret-key")

    assert safe_decode_flash_messages(attacker_token, secret_key="real-server-secret-key") == []


def test_expired_flash_token_is_rejected():
    """Verify flash tokens older than max_age are rejected as expired."""
    messages = [{"type": "warning", "text": "Old session warning"}]
    serializer = URLSafeTimedSerializer(secret_key="test-secret", salt=FLASH_COOKIE_SALT)
    token = serializer.dumps(messages)

    # Decode with max_age=-1 to simulate token past expiry
    with pytest.raises(SignatureExpired):
        decode_flash_messages(token, secret_key="test-secret", max_age=-1)

    assert safe_decode_flash_messages(token, secret_key="test-secret", max_age=-1) == []


def test_malformed_and_empty_tokens_handled_gracefully():
    """Verify empty, garbage, and non-list payloads are discarded without crash."""
    assert safe_decode_flash_messages("") == []
    assert safe_decode_flash_messages("!!!not-a-token!!!") == []

    serializer = URLSafeTimedSerializer(secret_key="test-secret", salt=FLASH_COOKIE_SALT)
    dict_token = serializer.dumps({"invalid": "structure"})
    assert safe_decode_flash_messages(dict_token, secret_key="test-secret") == []


# ============================================================================
# 2. Flash Message Object & Category Normalization Tests
# ============================================================================


def test_flash_message_dict_and_attribute_access():
    """Verify FlashMessage works seamlessly with Jinja2 attribute and dict access."""
    msg = FlashMessage("Profile updated", category="success")
    assert msg.type == "success"
    assert msg.text == "Profile updated"
    assert msg.category == "success"
    assert msg.message == "Profile updated"
    assert msg["type"] == "success"
    assert msg["text"] == "Profile updated"
    assert msg.to_dict() == {"type": "success", "text": "Profile updated"}


@pytest.mark.parametrize(
    "input_cat,expected_cat",
    [
        ("success", "success"),
        ("SUCCESS", "success"),
        ("warning", "warning"),
        ("warn", "warning"),
        ("alert", "warning"),
        ("error", "error"),
        ("danger", "error"),
        ("err", "error"),
        ("info", "info"),
        ("notice", "info"),
        ("unknown_custom", "info"),
        (None, "info"),
    ],
)
def test_normalize_category(input_cat, expected_cat):
    """Confirm category aliases and edge cases normalize to standard types."""
    assert normalize_category(input_cat) == expected_cat


# ============================================================================
# 3. Display Exactly Once & Clear on Subsequent Navigation Tests
# ============================================================================


def test_flash_cookie_displays_exactly_once_and_clears(client):
    """
    Test the complete display-once lifecycle:
    1. A signed flash cookie is sent to a page view (/login).
    2. The page renders the flash notification in the HTML.
    3. The response instructs the browser to clear the cookie (Max-Age=0).
    4. Subsequent navigation sends the updated cookie jar, and no flash renders.
    """
    settings = get_settings()
    messages = [
        {"type": "success", "text": "Password updated successfully."},
    ]
    token = encode_flash_messages(messages, settings=settings)

    # Step 1 & 2: Visit page with signed flash cookie
    resp1 = client.get("/login", cookies={FLASH_COOKIE_NAME: token})
    assert resp1.status_code == 200
    assert "Password updated successfully." in resp1.text
    assert 'id="flash-notices"' in resp1.text

    # Step 3: Response contains deletion header for flash cookie
    set_cookie_raw = resp1.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw
    assert "max-age=0" in set_cookie_raw.lower()

    # Step 4: Subsequent request with test client's updated cookies
    # Note: TestClient updates its cookie jar from the response Set-Cookie headers
    resp2 = client.get("/login")
    assert resp2.status_code == 200
    assert "Password updated successfully." not in resp2.text
    assert 'id="flash-notices"' not in resp2.text


def test_authenticated_base_template_renders_flash_banner(client, valid_token):
    """Verify pages extending base.html (e.g. /dashboard) render flash banner from signed cookie."""
    settings = get_settings()
    messages = [
        {"type": "info", "text": "Welcome to your new PriceHawk dashboard."},
    ]
    token = encode_flash_messages(messages, settings=settings)

    resp = client.get(
        "/dashboard",
        cookies={"access_token": valid_token, FLASH_COOKIE_NAME: token},
    )
    assert resp.status_code == 200
    assert "Welcome to your new PriceHawk dashboard." in resp.text
    assert 'id="flash-messages"' in resp.text
    assert "flash-banner" in resp.text

    # Verify cookie was cleared on this page view
    set_cookie_raw = resp.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw
    assert "max-age=0" in set_cookie_raw.lower()

    # Subsequent request to /dashboard has no flash message
    resp_subsequent = client.get("/dashboard", cookies={"access_token": valid_token})
    assert resp_subsequent.status_code == 200
    assert "Welcome to your new PriceHawk dashboard." not in resp_subsequent.text
    # flash-banner should not be rendered when no messages exist
    assert "flash-banner" not in resp_subsequent.text


def test_multiple_flash_messages_rendered_simultaneously(client, valid_token):
    """Verify multiple queued messages (e.g. success and info) render in the banner container."""
    settings = get_settings()
    messages = [
        {"type": "success", "text": "Alert settings saved."},
        {"type": "info", "text": "Daily digest scheduled for 09:00 UTC."},
    ]
    token = encode_flash_messages(messages, settings=settings)

    resp = client.get(
        "/alerts/settings",
        cookies={"access_token": valid_token, FLASH_COOKIE_NAME: token},
    )
    assert resp.status_code == 200
    assert "Alert settings saved." in resp.text
    assert "Daily digest scheduled for 09:00 UTC." in resp.text


# ============================================================================
# 4. Real Route Integration Tests
# ============================================================================


def test_account_change_password_sets_flash_cookie(client, valid_token):
    """Test POST /api/account/change-password queues a signed success flash message."""
    mock_sb = MagicMock()
    mock_sb.auth.update_user.return_value = {"user": {"id": "user-123"}}

    with patch("app.api.routes.account.get_supabase_client_with_session", return_value=mock_sb):
        response = client.post(
            "/api/account/change-password",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={"current_password": "oldpassword123", "new_password": "newpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Password updated successfully"

        set_cookie_raw = response.headers.get("set-cookie", "")
        assert FLASH_COOKIE_NAME in set_cookie_raw

        # Verify the cookie value decrypts to success message
        cookie_val = client.cookies.get(FLASH_COOKIE_NAME)
        flashes = safe_decode_flash_messages(cookie_val)
        assert len(flashes) == 1
        assert flashes[0].type == "success"
        assert "Password updated successfully." in flashes[0].text


def test_alerts_update_settings_sets_flash_cookie(client, valid_token):
    """Test PUT /api/alerts/settings queues a signed success flash notification."""
    mock_sb = MagicMock()
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[
            {
                "user_id": "user-123",
                "email_enabled": True,
                "digest_frequency_hours": 12,
                "alert_price_drop": True,
                "alert_price_increase": False,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    )

    app.dependency_overrides[get_user_supabase_client] = lambda: mock_sb
    try:
        response = client.put(
            "/api/alerts/settings",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={"digest_frequency_hours": 12, "alert_price_increase": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["digest_frequency_hours"] == 12

        set_cookie_raw = response.headers.get("set-cookie", "")
        assert FLASH_COOKIE_NAME in set_cookie_raw

        cookie_val = client.cookies.get(FLASH_COOKIE_NAME)
        flashes = safe_decode_flash_messages(cookie_val)
        assert len(flashes) == 1
        assert flashes[0].type == "success"
        assert "Alert settings saved successfully." in flashes[0].text
    finally:
        app.dependency_overrides.pop(get_user_supabase_client, None)


def test_session_expiration_redirect_sets_flash_cookie_and_displays_on_login(client, expired_token):
    """
    Test session expiry flow:
    1. Access protected /dashboard with expired session.
    2. Response redirects (303) to /login with signed warning flash cookie.
    3. Navigating to /login renders warning notice and clears the cookie.
    4. Subsequent visit to /login shows no notice.
    """
    resp_redirect = client.get(
        "/dashboard",
        cookies={"access_token": expired_token},
        follow_redirects=False,
    )
    assert resp_redirect.status_code == 303
    set_cookie_raw = resp_redirect.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw

    # Follow redirect to login page
    resp_login = client.get(resp_redirect.headers["location"])
    assert resp_login.status_code == 200
    assert "Your session has expired. Please log in again." in resp_login.text
    assert 'id="flash-notices"' in resp_login.text

    # Confirm cookie was cleared by login page render
    set_cookie_login = resp_login.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_login
    assert "max-age=0" in set_cookie_login.lower()

    # Subsequent visit has no flash notice
    resp_after = client.get("/login")
    assert resp_after.status_code == 200
    assert 'id="flash-notices"' not in resp_after.text


def test_web_login_success_sets_flash_and_displays_on_dashboard(client, valid_token):
    """Test POST /login form submission flashes welcome message on dashboard."""
    mock_sb = MagicMock()
    mock_sb.auth.sign_in_with_password.return_value = MagicMock(
        session=MagicMock(access_token=valid_token),
        user=MagicMock(id="user-123", email="user@example.com"),
    )

    with patch("app.api.routes.pages.get_supabase_client", return_value=mock_sb):
        resp_post = client.post(
            "/login",
            data={"email": "user@example.com", "password": "securepassword"},
            follow_redirects=False,
        )
        assert resp_post.status_code == 303
        assert resp_post.headers["location"] == "/dashboard"
        assert FLASH_COOKIE_NAME in resp_post.headers.get("set-cookie", "")

        # Render dashboard
        resp_dash = client.get("/dashboard", follow_redirects=False)
        assert resp_dash.status_code == 200
        assert "Welcome back! Successfully logged in." in resp_dash.text


def test_logout_sets_flash_and_displays_on_login(client):
    """Test GET /logout sets signed info flash cookie and displays on /login."""
    resp_logout = client.get("/logout", follow_redirects=False)
    assert resp_logout.status_code == 303
    assert resp_logout.headers["location"] == "/login"
    assert FLASH_COOKIE_NAME in resp_logout.headers.get("set-cookie", "")

    # Follow to login
    resp_login = client.get("/login")
    assert resp_login.status_code == 200
    assert "You have been logged out successfully." in resp_login.text
    assert 'id="flash-notices"' in resp_login.text

    # Subsequent navigation clears notice
    resp_next = client.get("/login")
    assert resp_next.status_code == 200
    assert "You have been logged out successfully." not in resp_next.text


def test_dismiss_flash_endpoint(client, valid_token):
    """Test /api/dismiss-flash HTMX endpoint returns 200 and clears flash cookie."""
    settings = get_settings()
    token = encode_flash_messages([{"type": "info", "text": "Dismiss me"}], settings=settings)

    resp = client.get("/api/dismiss-flash", cookies={FLASH_COOKIE_NAME: token})
    assert resp.status_code == 200
    set_cookie_raw = resp.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw
    assert "max-age=0" in set_cookie_raw.lower()


# ============================================================================
# 5. Environment-Aware Cookie Security Tests
# ============================================================================


def test_flash_cookie_security_attributes_in_production(production_env, client):
    """Verify flash cookie in production mandates Secure, HttpOnly, and SameSite=lax."""
    opts = get_flash_cookie_options()
    assert opts["secure"] is True
    assert opts["httponly"] is True
    assert opts["samesite"] == "lax"

    resp = client.get("/logout", follow_redirects=False)
    set_cookie_raw = resp.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw
    assert "httponly" in set_cookie_raw.lower()
    assert "samesite=lax" in set_cookie_raw.lower()
    assert "secure" in set_cookie_raw.lower()


def test_flash_cookie_security_attributes_in_development(client):
    """Verify flash cookie in development/test allows HTTP without Secure flag."""
    opts = get_flash_cookie_options()
    assert opts["secure"] is False
    assert opts["httponly"] is True
    assert opts["samesite"] == "lax"

    resp = client.get("/logout", follow_redirects=False)
    set_cookie_raw = resp.headers.get("set-cookie", "")
    assert FLASH_COOKIE_NAME in set_cookie_raw
    assert "httponly" in set_cookie_raw.lower()
    assert "samesite=lax" in set_cookie_raw.lower()
    # Development must not mandate secure flag
    assert "secure" not in set_cookie_raw.lower()
