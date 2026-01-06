"""
PriceHawk API Test Suite

Run with: pytest tests/test_api.py -v
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from main import app
from app.services.stores.base import DiscoveredProduct
from app.services.store_discovery import DiscoveryResult


client = TestClient(app)


# ---------------------------------------------------------------------------
# Mock JWT Token for Authentication
# ---------------------------------------------------------------------------
MOCK_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0LXVzZXItaWQiLCJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ.test"
AUTH_HEADERS = {"Authorization": f"Bearer {MOCK_TOKEN}"}


@pytest.fixture
def mock_auth():
    """Mock authentication to bypass JWT verification."""
    with patch("app.core.security.verify_token") as mock:
        mock.return_value = MagicMock(id="test-user-id", email="test@example.com")
        yield mock


@pytest.fixture
def mock_supabase():
    """Mock Supabase client."""
    with patch("app.db.database.get_supabase_client") as mock:
        mock_client = MagicMock()
        mock.return_value = mock_client
        yield mock_client


# ---------------------------------------------------------------------------
# Health Check Tests
# ---------------------------------------------------------------------------
class TestHealthCheck:
    def test_health_check(self):
        """Test /api/health returns healthy status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_root_redirects_to_docs(self):
        """Test root URL redirects to API docs."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/api/docs"


# ---------------------------------------------------------------------------
# Store Discovery Tests
# ---------------------------------------------------------------------------
class TestStoreDiscovery:
    @pytest.fixture
    def mock_discover_products(self):
        """Mock the discover_products function."""
        with patch("app.api.routes.discovery.discover_products") as mock:
            yield mock

    def test_discover_shopify_store(self, mock_auth, mock_discover_products):
        """Test discovering products from a Shopify store."""
        mock_discover_products.return_value = DiscoveryResult(
            platform="shopify",
            store_url="https://example-store.myshopify.com",
            total_found=2,
            products=[
                DiscoveredProduct(
                    name="Product 1",
                    price=Decimal("29.99"),
                    currency="USD",
                    image_url="https://example.com/img1.jpg",
                    product_url="https://example-store.myshopify.com/products/product-1",
                    platform="shopify",
                    variant_id="12345",
                    in_stock=True,
                ),
                DiscoveredProduct(
                    name="Product 2",
                    price=Decimal("49.99"),
                    currency="USD",
                    image_url="https://example.com/img2.jpg",
                    product_url="https://example-store.myshopify.com/products/product-2",
                    platform="shopify",
                    variant_id="12346",
                    in_stock=True,
                ),
            ],
        )

        response = client.post(
            "/api/stores/discover",
            json={"url": "https://example-store.myshopify.com", "limit": 50},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["platform"] == "shopify"
        assert data["total_found"] == 2
        assert len(data["products"]) == 2
        assert data["products"][0]["name"] == "Product 1"

    def test_discover_with_keyword_filter(self, mock_auth, mock_discover_products):
        """Test discovering products with keyword filter."""
        mock_discover_products.return_value = DiscoveryResult(
            platform="shopify",
            store_url="https://example.com",
            total_found=1,
            products=[
                DiscoveredProduct(
                    name="Laptop Pro",
                    price=Decimal("999.99"),
                    currency="USD",
                    image_url=None,
                    product_url="https://example.com/products/laptop-pro",
                    platform="shopify",
                ),
            ],
        )

        response = client.post(
            "/api/stores/discover",
            json={"url": "https://example.com", "keyword": "laptop", "limit": 10},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_found"] == 1
        mock_discover_products.assert_called_once_with(
            url="https://example.com",
            keyword="laptop",
            limit=10,
        )

    def test_discover_invalid_url(self, mock_auth):
        """Test discovery with invalid URL."""
        response = client.post(
            "/api/stores/discover",
            json={"url": "http://example.com"},  # HTTP not HTTPS
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 422  # Validation error

    def test_discover_unauthorized(self):
        """Test discovery without auth token."""
        response = client.post(
            "/api/stores/discover",
            json={"url": "https://example.com"},
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Track Products Tests
# ---------------------------------------------------------------------------
class TestTrackProducts:
    def test_track_products_success(self, mock_auth, mock_supabase):
        """Test tracking discovered products."""
        # Mock product insert
        mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "product-group-id", "product_name": "My Laptops"}]
        )

        response = client.post(
            "/api/stores/track",
            json={
                "group_name": "My Laptops",
                "product_urls": [
                    "https://example.com/product1",
                    "https://example.com/product2",
                ],
                "alert_threshold_percent": 10,
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["group_name"] == "My Laptops"
        assert data["products_added"] >= 0

    def test_track_products_empty_urls(self, mock_auth):
        """Test tracking with empty product URLs."""
        response = client.post(
            "/api/stores/track",
            json={
                "group_name": "My Group",
                "product_urls": [],
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 422  # Validation error


# ---------------------------------------------------------------------------
# Products CRUD Tests
# ---------------------------------------------------------------------------
class TestProducts:
    def test_list_products(self, mock_auth, mock_supabase):
        """Test listing tracked products."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "prod-1",
                    "product_name": "Test Product",
                    "is_active": True,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ],
            count=1,
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        response = client.get("/api/products", headers=AUTH_HEADERS)

        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert "total" in data

    def test_get_product(self, mock_auth, mock_supabase):
        """Test getting a single product."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "prod-1",
                    "product_name": "Test Product",
                    "is_active": True,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        )

        response = client.get("/api/products/prod-1", headers=AUTH_HEADERS)

        assert response.status_code == 200

    def test_get_product_not_found(self, mock_auth, mock_supabase):
        """Test getting a non-existent product."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        response = client.get("/api/products/non-existent", headers=AUTH_HEADERS)

        assert response.status_code == 404

    def test_update_product(self, mock_auth, mock_supabase):
        """Test updating a product."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "prod-1"}]
        )
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "prod-1",
                    "product_name": "Updated Name",
                    "is_active": True,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                }
            ]
        )

        response = client.put(
            "/api/products/prod-1",
            json={"product_name": "Updated Name"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200

    def test_delete_product(self, mock_auth, mock_supabase):
        """Test soft deleting a product."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "prod-1"}]
        )
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "prod-1", "is_active": False}]
        )

        response = client.delete("/api/products/prod-1", headers=AUTH_HEADERS)

        assert response.status_code == 204


# ---------------------------------------------------------------------------
# Price History Tests
# ---------------------------------------------------------------------------
class TestPriceHistory:
    def test_get_price_history(self, mock_auth, mock_supabase):
        """Test getting price history for a product."""
        # Mock product exists
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "prod-1"}]
        )

        response = client.get("/api/prices/prod-1/history", headers=AUTH_HEADERS)

        # Will return empty if no competitors found
        assert response.status_code == 200

    def test_get_latest_price(self, mock_auth, mock_supabase):
        """Test getting latest price for a competitor."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "comp-1"}]
        )

        response = client.get("/api/prices/latest/comp-1", headers=AUTH_HEADERS)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Store Handler Unit Tests
# ---------------------------------------------------------------------------
class TestShopifyHandler:
    @pytest.mark.asyncio
    async def test_detect_shopify_store(self):
        """Test Shopify detection."""
        from app.services.stores.shopify import ShopifyHandler

        handler = ShopifyHandler()

        with patch.object(handler, "_get_client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"products": []}

            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_client.return_value = mock_http

            result = await handler.detect("https://example-store.myshopify.com")

            assert result is True
            await handler.close()

    @pytest.mark.asyncio
    async def test_detect_non_shopify_store(self):
        """Test non-Shopify detection returns False."""
        from app.services.stores.shopify import ShopifyHandler

        handler = ShopifyHandler()

        with patch.object(handler, "_get_client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_client.return_value = mock_http

            result = await handler.detect("https://example.com")

            assert result is False
            await handler.close()


class TestAmazonHandler:
    def test_amazon_url_detection(self):
        """Test Amazon URL pattern detection."""
        from app.services.stores.amazon import AmazonHandler

        handler = AmazonHandler()

        # Store/search pages should be detected
        assert handler.STORE_PATTERNS

        # Check domain detection
        assert "amazon.com" in handler.AMAZON_DOMAINS
        assert "amazon.co.uk" in handler.AMAZON_DOMAINS


class TestEbayHandler:
    def test_ebay_url_detection(self):
        """Test eBay URL pattern detection."""
        from app.services.stores.ebay import EbayHandler

        handler = EbayHandler()

        assert "ebay.com" in handler.EBAY_DOMAINS
        assert "/str/" in str(handler.STORE_PATTERNS)


# ---------------------------------------------------------------------------
# Price Parsing Tests
# ---------------------------------------------------------------------------
class TestPriceParsing:
    def test_parse_usd_price(self):
        """Test parsing USD price."""
        from app.services.scraper_service import parse_price

        price, currency = parse_price("$29.99")
        assert price == Decimal("29.99")
        assert currency == "USD"

    def test_parse_gbp_price(self):
        """Test parsing GBP price."""
        from app.services.scraper_service import parse_price

        price, currency = parse_price("£49.99")
        assert price == Decimal("49.99")
        assert currency == "GBP"

    def test_parse_eur_price(self):
        """Test parsing EUR price."""
        from app.services.scraper_service import parse_price

        price, currency = parse_price("€19.99")
        assert price == Decimal("19.99")
        assert currency == "EUR"

    def test_parse_price_with_commas(self):
        """Test parsing price with thousand separators."""
        from app.services.scraper_service import parse_price

        price, currency = parse_price("$1,299.99")
        assert price == Decimal("1299.99")

    def test_parse_european_format(self):
        """Test parsing European number format (1.234,56)."""
        from app.services.scraper_service import parse_price

        price, currency = parse_price("€1.234,56")
        assert price == Decimal("1234.56")


# ---------------------------------------------------------------------------
# URL Validation Tests
# ---------------------------------------------------------------------------
class TestUrlValidation:
    def test_valid_amazon_url(self):
        """Test valid Amazon URL passes validation."""
        from app.services.scraper_service import validate_url

        is_valid, error = validate_url("https://www.amazon.com/dp/B08N5WRWNW")
        assert is_valid is True
        assert error is None

    def test_http_url_rejected(self):
        """Test HTTP URL is rejected."""
        from app.services.scraper_service import validate_url

        is_valid, error = validate_url("http://www.amazon.com/dp/B08N5WRWNW")
        assert is_valid is False
        assert "HTTPS" in error

    def test_localhost_rejected(self):
        """Test localhost is rejected."""
        from app.services.scraper_service import validate_url

        is_valid, error = validate_url("https://localhost/test")
        assert is_valid is False
        assert "Private" in error

    def test_private_ip_rejected(self):
        """Test private IP is rejected."""
        from app.services.scraper_service import validate_url

        is_valid, error = validate_url("https://192.168.1.1/test")
        assert is_valid is False

    def test_non_whitelisted_domain(self):
        """Test non-whitelisted domain is rejected."""
        from app.services.scraper_service import validate_url

        is_valid, error = validate_url("https://example.com/test")
        assert is_valid is False
        assert "whitelist" in error.lower()


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
