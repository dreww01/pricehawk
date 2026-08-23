"""
Pytest fixtures for PriceHawk tests.
"""

import os

# Set fallback environment variables for test execution
os.environ.setdefault("SB_URL", "https://test.supabase.co")
os.environ.setdefault("SB_ANON_KEY", "test-anon-key")
os.environ.setdefault("SB_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SB_JWT_SECRET", "test-jwt-secret")

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """
    Mock auth headers for testing.
    In real tests, this would use a test user token.
    """
    return {"Authorization": "Bearer test_token"}
