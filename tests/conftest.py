"""Pytest fixtures for PriceHawk tests."""

import os
import sys
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

if "/tmp/pip_packages" not in sys.path:
    if os.path.exists("/tmp/pip_packages"):
        sys.path.insert(0, "/tmp/pip_packages")
if os.path.exists("/tmp/pip_packages"):
    current_pp = os.environ.get("PYTHONPATH", "")
    paths = [p for p in current_pp.split(os.pathsep) if p]
    if "/tmp/pip_packages" not in paths:
        paths.insert(0, "/tmp/pip_packages")
    cwd = os.getcwd()
    if "." not in paths and cwd not in paths:
        paths.append(cwd)
    os.environ["PYTHONPATH"] = os.pathsep.join(paths)

# Application settings are validated during module import. Use inert local values so
# the test suite never depends on a developer's .env file or external credentials.
os.environ.setdefault("SB_URL", "https://example.supabase.co")
os.environ.setdefault("SB_ANON_KEY", "test-anon-key")
os.environ.setdefault("SB_SERVICE_KEY", "test-service-key")
os.environ.setdefault("SB_JWT_SECRET", "test-jwt-secret")

from app.core.security import CurrentUser
from main import app


@pytest.fixture
def client():
    """Create test client for the FastAPI app."""
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


@pytest.fixture(autouse=True)
def reset_dashboard_cache():
    """Ensure dashboard cache, session revocations, and task states are cleared before and after each test."""
    from app.core.security import clear_revoked_users
    from app.services.account_service import clear_account_deletion_state
    from app.services.dashboard_cache import get_dashboard_cache

    cache = get_dashboard_cache()
    cache.clear_all()
    clear_revoked_users()
    clear_account_deletion_state()
    yield
    cache.clear_all()
    clear_revoked_users()
    clear_account_deletion_state()


@pytest.fixture
def auth_headers():
    """Mock auth headers for testing."""
    return {"Authorization": "Bearer test_token"}


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
    """Override auth dependencies with mock user."""
    from app.core.security import get_current_user, verify_token

    def mock_get_current_user():
        return mock_user

    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[verify_token] = mock_get_current_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_auth(override_auth):
    """Alias fixture for override_auth to support existing tests."""
    yield
