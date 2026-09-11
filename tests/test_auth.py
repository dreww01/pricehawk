"""
Authentication endpoint tests.
"""

import time
import jwt
import pytest
from app.core.config import get_settings


def _create_test_token() -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": "test-user-1234",
        "email": "test@example.com",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, settings.sb_jwt_secret, algorithm="HS256")


@pytest.mark.parametrize(
    "method,endpoint,json_payload",
    [
        ("post", "/api/scraper/scrape/manual/123", None),
        ("put", "/api/alerts/settings", {"price_drop_enabled": True}),
        ("post", "/api/account/change-password", {"current_password": "p1", "new_password": "p2"}),
        ("post", "/api/stores/discover", {"url": "https://example.com"}),
        ("get", "/api/auth/me", None),
    ],
)
def test_ambient_cookie_rejected_on_general_api_endpoints(client, method, endpoint, json_payload):
    """Regression test: mutation and general API routes reject ambient access_token cookie."""
    token = _create_test_token()
    client_method = getattr(client, method)
    kwargs = {"cookies": {"access_token": token}}
    if json_payload is not None:
        kwargs["json"] = json_payload

    response = client_method(endpoint, **kwargs)
    assert response.status_code in (401, 403)
    assert response.json()["detail"] == "Not authenticated"


def test_me_endpoint_never_serializes_token(client):
    """Confirm user JSON from /api/auth/me never contains an access token or raw credentials."""
    token = _create_test_token()
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "test-user-1234"
    assert data["email"] == "test@example.com"
    assert "token" not in data
    assert "access_token" not in data
    assert token not in response.text



def test_login_missing_fields(client):
    """Test login with missing fields returns 422."""
    response = client.post("/api/auth/login", json={})
    assert response.status_code == 422


def test_login_invalid_email(client):
    """Test login with invalid email format returns 422."""
    response = client.post("/api/auth/login", json={
        "email": "not-an-email",
        "password": "password123"
    })
    assert response.status_code == 422


def test_signup_missing_fields(client):
    """Test signup with missing fields returns 422."""
    response = client.post("/api/auth/signup", json={})
    assert response.status_code == 422


def test_signup_invalid_email(client):
    """Test signup with invalid email format returns 422."""
    response = client.post("/api/auth/signup", json={
        "email": "not-an-email",
        "password": "password123"
    })
    assert response.status_code == 422


def test_forgot_password_missing_email(client):
    """Test forgot password with missing email returns 422."""
    response = client.post("/api/auth/forgot-password", json={})
    assert response.status_code == 422


def test_forgot_password_invalid_email(client):
    """Test forgot password with invalid email returns 422."""
    response = client.post("/api/auth/forgot-password", json={
        "email": "not-an-email"
    })
    assert response.status_code == 422


def test_reset_password_missing_fields(client):
    """Test reset password with missing fields returns 422."""
    response = client.post("/api/auth/reset-password", json={})
    assert response.status_code == 422


def test_me_endpoint_requires_auth(client):
    """Test /me endpoint requires authentication."""
    response = client.get("/api/auth/me")
    assert response.status_code == 403
