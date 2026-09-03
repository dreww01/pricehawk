"""
Page route tests.
"""

import pytest

from app.api.routes import pages
from app.core.security import CurrentUser


@pytest.fixture
def authenticated_cookie(monkeypatch):
    """Patch cookie auth and return a valid session cookie."""
    async def fake_verify_token_string(token: str) -> CurrentUser:
        assert token == "test-cookie-token"
        return CurrentUser(id="user-123", email="user@example.com", role="authenticated")

    monkeypatch.setattr(pages, "verify_token_string", fake_verify_token_string)
    return {"access_token": "test-cookie-token"}


def test_login_page_renders(client):
    """Test login page returns HTML."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PriceHawk" in response.text


def test_signup_page_renders(client):
    """Test signup page returns HTML."""
    response = client.get("/signup")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_forgot_password_page_renders(client):
    """Test forgot password page returns HTML."""
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Forgot Password" in response.text


def test_reset_password_page_renders(client):
    """Test reset password page returns HTML."""
    response = client.get("/reset-password")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_requires_auth(client):
    """Test dashboard redirects unauthenticated users."""
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_tracked_requires_auth(client):
    """Test tracked page redirects unauthenticated users."""
    response = client.get("/tracked", follow_redirects=False)
    assert response.status_code == 303


def test_discover_requires_auth(client):
    """Test discover page redirects unauthenticated users."""
    response = client.get("/discover", follow_redirects=False)
    assert response.status_code == 303


def test_insights_requires_auth(client):
    """Test insights page redirects unauthenticated users."""
    response = client.get("/insights", follow_redirects=False)
    assert response.status_code == 303


def test_account_settings_requires_auth(client):
    """Test account settings page redirects unauthenticated users."""
    response = client.get("/account/settings", follow_redirects=False)
    assert response.status_code == 303


def test_dashboard_page_renders_for_authenticated_user(client, authenticated_cookie):
    """Test a representative authenticated page returns HTML."""
    response = client.get("/dashboard", cookies=authenticated_cookie)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "PriceHawk" in response.text


def test_logout_clears_cookie(client):
    """Test logout clears access_token cookie."""
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
