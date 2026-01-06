from decimal import Decimal
from urllib.parse import urljoin, urlparse

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class WooCommerceHandler(BaseStoreHandler):
    """Handler for WooCommerce stores using Store API or REST API."""

    platform_name = "woocommerce"

    # API endpoints in order of preference
    API_ENDPOINTS = [
        "/wp-json/wc/store/products",
        "/wp-json/wc/v3/products",
        "/wp-json/wc/v2/products",
    ]

    async def detect(self, url: str) -> bool:
        """Check if store is WooCommerce by testing API endpoints."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        client = await self._get_client()

        for endpoint in self.API_ENDPOINTS:
            try:
                test_url = f"{base_url}{endpoint}?per_page=1"
                response = await client.get(test_url)

                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and len(data) > 0:
                        return True

            except Exception:
                continue

        return False

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """
        Fetch products from WooCommerce API.

        Strategy: Fetch ALL products from store (up to max_products_fetch),
        then filter by keyword, then apply limit. This ensures products
        are found regardless of their position in the catalog.
        """
        from app.core.config import get_settings

        settings = get_settings()
        max_fetch = settings.max_products_fetch

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Find working endpoint
        working_endpoint = await self._find_working_endpoint(base_url)
        if not working_endpoint:
            return []

        products: list[DiscoveredProduct] = []
        page = 1
        per_page = 100  # Use max page size for efficiency

        client = await self._get_client()

        # Fetch ALL products (up to max_fetch)
        while len(products) < max_fetch:
            params = f"?per_page={per_page}&page={page}"

            try:
                response = await client.get(f"{base_url}{working_endpoint}{params}")
                if response.status_code != 200:
                    break

                data = response.json()
                if not isinstance(data, list) or not data:
                    break

                for p in data:
                    product = self._parse_product(p, base_url, working_endpoint)
                    if product:
                        products.append(product)

                page += 1

            except Exception:
                break

        # Filter by keyword AFTER fetching all products
        filtered = self.filter_by_keyword(products, keyword)

        # Apply limit to filtered results
        return filtered[:limit]

    async def _find_working_endpoint(self, base_url: str) -> str | None:
        """Find the first working WooCommerce API endpoint."""
        client = await self._get_client()

        for endpoint in self.API_ENDPOINTS:
            try:
                response = await client.get(f"{base_url}{endpoint}?per_page=1")
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return endpoint
            except Exception:
                continue

        return None

    def _parse_product(
        self,
        data: dict,
        base_url: str,
        endpoint: str,
    ) -> DiscoveredProduct | None:
        """Parse WooCommerce product JSON into DiscoveredProduct."""
        try:
            # Store API format
            if "store" in endpoint:
                return self._parse_store_api_product(data, base_url)

            # REST API v3/v2 format
            return self._parse_rest_api_product(data, base_url)

        except Exception:
            return None

    def _parse_store_api_product(
        self,
        data: dict,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse Store API format."""
        try:
            name = data.get("name", "")
            permalink = data.get("permalink", "")
            product_url = permalink or urljoin(base_url, f"/product/{data.get('slug', '')}")

            # Images
            images = data.get("images", [])
            image_url = images[0].get("src") if images else None

            # Price
            prices = data.get("prices", {})
            price = None
            currency = prices.get("currency_code", "USD")

            price_str = prices.get("price")
            if price_str:
                # Store API returns price in cents
                decimal_places = prices.get("currency_minor_unit", 2)
                price = Decimal(price_str) / (10 ** decimal_places)

            in_stock = data.get("is_in_stock", True)

            # Extract searchable fields
            description = data.get("description", "") or data.get("short_description", "")
            categories = data.get("categories", [])
            tags = [cat.get("name", "") for cat in categories if cat.get("name")]

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=str(data.get("id", "")),
                sku=data.get("sku"),
                in_stock=in_stock,
                description=description,
                tags=tags,
                raw_data=data,
            )

        except Exception:
            return None

    def _parse_rest_api_product(
        self,
        data: dict,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse REST API v3/v2 format."""
        try:
            name = data.get("name", "")
            permalink = data.get("permalink", "")
            product_url = permalink or urljoin(base_url, f"/product/{data.get('slug', '')}")

            # Images
            images = data.get("images", [])
            image_url = images[0].get("src") if images else None

            # Price
            price = None
            price_str = data.get("price")
            if price_str:
                price = Decimal(str(price_str))

            in_stock = data.get("in_stock", True)

            # Extract searchable fields
            description = data.get("description", "") or data.get("short_description", "")
            categories = data.get("categories", [])
            product_tags = data.get("tags", [])
            tags = [cat.get("name", "") for cat in categories if cat.get("name")]
            tags.extend([tag.get("name", "") for tag in product_tags if tag.get("name")])

            return DiscoveredProduct(
                name=name,
                price=price,
                currency="USD",
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=str(data.get("id", "")),
                sku=data.get("sku"),
                in_stock=in_stock,
                description=description,
                tags=tags,
                raw_data=data,
            )

        except Exception:
            return None
