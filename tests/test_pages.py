"""
Page route tests.
"""


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


def test_logout_clears_cookie(client):
    """Test logout clears access_token cookie."""
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
