"""
Pytest fixtures for PriceHawk tests.
"""

import os
import pytest

os.environ.setdefault("SB_URL", "https://dummy.supabase.co")
os.environ.setdefault("SB_ANON_KEY", "dummy")
os.environ.setdefault("SB_SERVICE_KEY", "dummy")
os.environ.setdefault("SB_JWT_SECRET", "dummy")

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
