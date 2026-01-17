"""
Authentication endpoint tests.
"""

import pytest


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
