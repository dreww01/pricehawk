"""Pytest fixtures for PriceHawk tests."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from datetime import datetime
from decimal import Decimal

from main import app
from app.core.security import CurrentUser


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_user():
    """Create a mock authenticated user."""
    return CurrentUser(
        id="test-user-uuid-1234",
        email="test@example.com",
        role="authenticated",
    )


@pytest.fixture
def mock_supabase_client():
    """Create a mock Supabase client."""
    mock = MagicMock()
    return mock


@pytest.fixture
def auth_headers():
    """Return mock auth headers for testing."""
    return {"Authorization": "Bearer mock-jwt-token"}


@pytest.fixture
def sample_product():
    """Sample product data."""
    return {
        "id": "prod-uuid-1234",
        "user_id": "test-user-uuid-1234",
        "product_name": "Test Product",
        "is_active": True,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }


@pytest.fixture
def sample_competitor():
    """Sample competitor data."""
    return {
        "id": "comp-uuid-1234",
        "product_id": "prod-uuid-1234",
        "url": "https://example.com/product/1",
        "retailer_name": "Example Store",
        "alert_threshold_percent": Decimal("10.00"),
        "created_at": datetime.now().isoformat(),
    }


@pytest.fixture
def sample_price_history():
    """Sample price history data."""
    return [
        {
            "id": "ph-uuid-1",
            "competitor_id": "comp-uuid-1234",
            "price": Decimal("99.99"),
            "currency": "USD",
            "scraped_at": "2024-01-15T10:00:00",
            "scrape_status": "success",
            "error_message": None,
        },
        {
            "id": "ph-uuid-2",
            "competitor_id": "comp-uuid-1234",
            "price": Decimal("89.99"),
            "currency": "USD",
            "scraped_at": "2024-01-16T10:00:00",
            "scrape_status": "success",
            "error_message": None,
        },
    ]


@pytest.fixture
def override_auth(mock_user):
    """Override auth dependency with mock user."""
    from app.core.security import get_current_user

    def mock_get_current_user():
        return mock_user

    app.dependency_overrides[get_current_user] = mock_get_current_user
    yield
    app.dependency_overrides.clear()
