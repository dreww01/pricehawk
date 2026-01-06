import json
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class ShopifyHandler(BaseStoreHandler):
    """
    Handler for Shopify stores with hybrid API approach.

    Tries /products.json API first (classic Shopify), then falls back to
    Storefront GraphQL API (for Hydrogen stores like Fashion Nova).
    """

    platform_name = "shopify"

    async def detect(self, url: str) -> bool:
        """Check if store is Shopify by testing /products.json endpoint."""
        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            products_url = f"{base_url}/products.json?limit=1"

            client = await self._get_client()
            response = await client.get(products_url)

            if response.status_code != 200:
                return False

            data = response.json()
            return "products" in data

        except Exception:
            return False

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """
        Fetch products from Shopify store with fallback strategy.

        Strategy:
        1. Try /products.json API (classic Shopify)
        2. If fails, try Storefront API (Hydrogen stores like Fashion Nova)
        3. Fetch ALL products (up to max_products_fetch), then filter by keyword
        """
        from app.core.config import get_settings

        settings = get_settings()
        max_fetch = settings.max_products_fetch

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Try /products.json first (fast path)
        products = await self._fetch_via_products_json(base_url, max_fetch)

        # Fallback to Storefront API if products.json failed
        if not products:
            products = await self._fetch_via_storefront_api(base_url, max_fetch)

        # Filter by keyword AFTER fetching all products
        filtered = self.filter_by_keyword(products, keyword)

        # Apply limit to filtered results
        return filtered[:limit]

    async def _fetch_via_products_json(
        self, base_url: str, max_fetch: int
    ) -> list[DiscoveredProduct]:
        """Fetch products using /products.json API (classic Shopify)."""
        products: list[DiscoveredProduct] = []
        page = 1
        page_size = 250

        client = await self._get_client()

        while len(products) < max_fetch:
            products_url = f"{base_url}/products.json?limit={page_size}&page={page}"

            try:
                response = await client.get(products_url)
                if response.status_code != 200:
                    break

                data = response.json()
                page_products = data.get("products", [])

                if not page_products:
                    break

                for p in page_products:
                    product = self._parse_product(p, base_url)
                    if product:
                        products.append(product)

                page += 1

            except Exception:
                break

        return products

    async def _fetch_via_storefront_api(
        self, base_url: str, max_fetch: int
    ) -> list[DiscoveredProduct]:
        """
        Fetch products using Shopify Storefront GraphQL API (Hydrogen stores).

        Tries multiple API versions for compatibility:
        - unstable: Works for Fashion Nova, other Hydrogen stores
        - 2024-01, 2023-10, 2023-07: Versioned APIs
        """
        # Try different API versions (unstable first - works on more stores)
        api_versions = ["unstable", "2024-01", "2023-10", "2023-07"]

        for version in api_versions:
            api_url = f"{base_url}/api/{version}/graphql.json"
            products = await self._fetch_storefront_version(
                api_url, base_url, max_fetch
            )

            if products:
                return products

        # All versions failed
        return []

    async def _fetch_storefront_version(
        self, api_url: str, base_url: str, max_fetch: int
    ) -> list[DiscoveredProduct]:
        """Fetch products from a specific Storefront API version."""
        products: list[DiscoveredProduct] = []
        cursor = None
        page_size = 250

        client = await self._get_client()

        while len(products) < max_fetch:
            query = self._build_storefront_query(page_size, cursor)

            try:
                response = await client.post(
                    api_url,
                    json={"query": query},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )

                # If 403/404, try next version
                if response.status_code in [403, 404]:
                    break

                if response.status_code != 200:
                    break

                data = response.json()

                # Check for GraphQL errors
                if "errors" in data:
                    break

                # Parse products from GraphQL response
                edges = data.get("data", {}).get("products", {}).get("edges", [])
                if not edges:
                    break

                for edge in edges:
                    node = edge.get("node", {})
                    product = self._parse_storefront_product(node, base_url)
                    if product:
                        products.append(product)

                # Check for next page
                page_info = data.get("data", {}).get("products", {}).get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break

                cursor = page_info.get("endCursor")
                if not cursor:
                    break

            except Exception:
                break

        return products

    def _build_storefront_query(self, page_size: int, cursor: str | None = None) -> str:
        """Build GraphQL query for Shopify Storefront API."""
        after_clause = f', after: "{cursor}"' if cursor else ""

        return """
        {
          products(first: %d%s) {
            edges {
              node {
                id
                title
                handle
                description
                productType
                tags
                priceRange {
                  minVariantPrice {
                    amount
                    currencyCode
                  }
                }
                images(first: 1) {
                  edges {
                    node {
                      url
                    }
                  }
                }
                variants(first: 1) {
                  edges {
                    node {
                      id
                      availableForSale
                      sku
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """ % (
            page_size,
            after_clause,
        )

    def _parse_storefront_product(
        self, node: dict, base_url: str
    ) -> DiscoveredProduct | None:
        """Parse Shopify Storefront API GraphQL response into DiscoveredProduct."""
        try:
            title = node.get("title", "")
            handle = node.get("handle", "")
            product_url = urljoin(base_url, f"/products/{handle}")

            # Get first image
            image_edges = node.get("images", {}).get("edges", [])
            image_url = None
            if image_edges:
                image_url = image_edges[0].get("node", {}).get("url")

            # Get price from priceRange
            price_range = node.get("priceRange", {})
            min_price = price_range.get("minVariantPrice", {})
            price_str = min_price.get("amount")
            currency = min_price.get("currencyCode", "USD")

            price = None
            if price_str:
                price = Decimal(str(price_str))

            # Get variant info
            variant_edges = node.get("variants", {}).get("edges", [])
            variant_id = None
            sku = None
            in_stock = False

            if variant_edges:
                first_variant = variant_edges[0].get("node", {})
                variant_id = first_variant.get("id")
                sku = first_variant.get("sku")
                in_stock = first_variant.get("availableForSale", False)

            # Extract searchable fields
            product_type = node.get("productType", "")
            tags = node.get("tags", [])
            description = node.get("description", "")

            return DiscoveredProduct(
                name=title,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=variant_id,
                sku=sku,
                in_stock=in_stock,
                product_type=product_type,
                tags=tags,
                description=description,
                raw_data=node,
            )

        except Exception:
            return None

    def _parse_product(self, data: dict, base_url: str) -> DiscoveredProduct | None:
        """Parse Shopify product JSON into DiscoveredProduct."""
        try:
            title = data.get("title", "")
            handle = data.get("handle", "")
            product_url = urljoin(base_url, f"/products/{handle}")

            # Get first image
            images = data.get("images", [])
            image_url = images[0].get("src") if images else None

            # Get price from first variant
            variants = data.get("variants", [])
            price = None
            currency = "USD"
            variant_id = None
            in_stock = False

            if variants:
                first_variant = variants[0]
                price_str = first_variant.get("price")
                if price_str:
                    price = Decimal(str(price_str))

                variant_id = str(first_variant.get("id", ""))
                in_stock = first_variant.get("available", False)

            # Extract searchable fields
            product_type = data.get("product_type", "")
            tags = data.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            description = data.get("body_html", "")

            return DiscoveredProduct(
                name=title,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=variant_id,
                sku=data.get("variants", [{}])[0].get("sku"),
                in_stock=in_stock,
                product_type=product_type,
                tags=tags,
                description=description,
                raw_data=data,
            )

        except Exception:
            return None
