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
    platform_label = "Shopify"

    async def detect(self, url: str, html: str | None = None) -> bool:
        """
        Check if store is Shopify via API, URL patterns, or HTML inspection.
        Recognizes classic Shopify, custom subdomains, alternative URL paths,
        and modern headless setups (Hydrogen, Next.js Commerce, Storefront API).
        """
        url = (url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")

        self.matched_signals = []
        self.is_headless = False

        # Signal check 1: myshopify.com domain
        if netloc.endswith("myshopify.com"):
            self.matched_signals.append("myshopify_domain")
            self.platform_label = "Shopify"
            self.confidence = 0.95

        # Signal check: custom commerce subdomain
        if any(netloc.startswith(prefix) for prefix in ("shop.", "store.", "buy.", "checkout.", "products.")):
            self.matched_signals.append("custom_subdomain")

        # Signal check: alternative / classic Shopify URL patterns
        if any(p in path for p in ("/products/", "/collections/")) or path.endswith(("/products", "/collections")):
            self.matched_signals.append("shopify_url_pattern")

        # If HTML is provided directly, inspect it first without network I/O
        if html:
            if self._inspect_shopify_html(html):
                if "myshopify_domain" in self.matched_signals:
                    self.confidence = max(self.confidence, 0.95)
                return True
            if "myshopify_domain" in self.matched_signals:
                self.platform_label = "Shopify"
                self.confidence = 0.95
                return True
            return False

        # Fast path via /products.json API (classic Shopify)
        client = await self._get_client()
        urls_to_try = [f"{base_url}/products.json?limit=1"]
        # If there's a subpath (e.g. /uk, /shop, /en-us), also probe subpath
        if path and not path.startswith("/products"):
            subpath_candidate = path.split("/products")[0] if "/products" in path else path
            if subpath_candidate and subpath_candidate != "/":
                urls_to_try.append(f"{base_url}{subpath_candidate}/products.json?limit=1")

        for test_url in urls_to_try:
            try:
                response = await client.get(test_url)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and "products" in data:
                        self.matched_signals.append("products_json_api")
                        self.confidence = 0.98
                        self.platform_label = "Shopify"
                        self.is_headless = False
                        return True
            except Exception:
                pass

        # Storefront GraphQL API probe (Hydrogen and headless Shopify)
        storefront_versions = ["2024-01", "unstable", "2023-10"]
        for version in storefront_versions:
            try:
                api_url = f"{base_url}/api/{version}/graphql.json"
                sf_res = await client.post(
                    api_url,
                    json={"query": "{ shop { name } }"},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
                if sf_res.status_code in (200, 400, 401):
                    # Check if GraphQL response structure or shopify header exists
                    res_text = sf_res.text.lower()
                    if "data" in res_text or "errors" in res_text or "shopify" in sf_res.headers.get("server", "").lower():
                        self.matched_signals.append(f"storefront_api_{version}")
                        self.is_headless = True
                        self.platform_label = "Shopify (Headless)"
                        self.confidence = 0.92
                        return True
            except Exception:
                pass

        # HTML inspection (direct httpx fetch first, then Playwright fallback)
        page_html = None
        try:
            html_res = await client.get(url)
            if html_res.status_code == 200:
                page_html = html_res.text
        except Exception:
            pass

        if not page_html and base_url != url:
            try:
                html_res = await client.get(base_url)
                if html_res.status_code == 200:
                    page_html = html_res.text
            except Exception:
                pass

        if page_html:
            if self._inspect_shopify_html(page_html):
                return True

        # Slow path with Playwright for Cloudflare/bot protected sites
        try:
            from app.services.scraper_service import fetch_with_playwright
            pw_html = await fetch_with_playwright(base_url)
            if pw_html and self._inspect_shopify_html(pw_html):
                return True
        except Exception:
            pass

        # If domain was myshopify.com, return True even if API/HTML was blocked
        if "myshopify_domain" in self.matched_signals:
            return True

        return False

    def _inspect_shopify_html(self, html: str) -> bool:
        """Inspect HTML content for Shopify and headless Shopify signatures."""
        if not html:
            return False

        html_lower = html.lower()

        # Headless frontends indicators (Hydrogen, Next.js Commerce with Shopify, Storefront API)
        headless_signals = []
        headless_indicators = [
            ("hydrogen", "@shopify/hydrogen"),
            ("hydrogen", "remix-oxygen"),
            ("hydrogen", "oxygen-v1"),
            ("hydrogen", "<!-- hydrogen -->"),
            ("shopify_buy_sdk", "shopifybuy"),
            ("shopify_buy_sdk", "shopify-buy"),
            ("storefront_api", "storefront.shopify.com"),
            ("storefront_api", "x-shopify-storefront-access-token"),
            ("storefront_api", "shopify-storefront-access-token"),
            ("storefront_api", "shopify-storefront-api"),
            ("storefront_api", "@shopify/storefront-api-client"),
            ("storefront_api", "shopifystorefront"),
            ("nextjs_shopify", "nextjs-commerce"),
            ("nextjs_shopify", "@vercel/commerce-shopify"),
            ("shopify_dev_host", "__shopify_dev_host__"),
            ("hydrogen_state", "__hydrogen_state__"),
            ("gatsby_shopify", "gatsby-source-shopify"),
        ]
        for sig_name, marker in headless_indicators:
            if marker in html_lower and sig_name not in headless_signals:
                headless_signals.append(sig_name)

        # Standard Shopify signatures
        signals = []
        if "cdn.shopify.com" in html_lower or "cdn.shopify" in html_lower:
            signals.append("cdn.shopify.com")
        if "shopify.theme" in html_lower or "shopify.routes" in html_lower or "window.shopify" in html_lower or "shopify = window.shopify" in html_lower:
            signals.append("shopify_js_globals")
        if "shopifyanalytics" in html_lower or "monorail-edge.shopifysvc.com" in html_lower or "trekkie" in html_lower:
            signals.append("shopify_analytics")
        if "data-shopify" in html_lower or "shopify-features" in html_lower or "shopify-digital-wallet" in html_lower or "shopify-checkout-api-token" in html_lower:
            signals.append("shopify_meta_attributes")
        if "shopify-payment-button" in html_lower or "shopify-section" in html_lower or "shopify-section-" in html_lower:
            signals.append("shopify_theme_markup")
        if "checkout.shopify.com" in html_lower or "myshopify.com" in html_lower:
            signals.append("shopify_checkout_ref")
        if 'name="generator" content="shopify' in html_lower or 'content="shopify' in html_lower:
            signals.append("shopify_generator_meta")

        # Check if Next.js hydration data contains Shopify references
        if '__next_data__' in html_lower and any(k in html_lower for k in ("myshopify.com", "shopify", "storefront")):
            if "nextjs_shopify" not in headless_signals:
                headless_signals.append("nextjs_shopify")

        all_signals = list(dict.fromkeys(headless_signals + signals))

        if all_signals:
            for s in all_signals:
                if s not in self.matched_signals:
                    self.matched_signals.append(s)

            is_headless = bool(headless_signals)
            self.is_headless = is_headless
            if is_headless:
                self.platform_label = "Shopify (Headless)"
                self.confidence = min(0.95, 0.85 + len(all_signals) * 0.03)
            else:
                self.platform_label = "Shopify"
                self.confidence = min(0.98, 0.88 + len(all_signals) * 0.02)
            return True

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

        # Fallback to generalized web heuristics if both structured APIs yielded no products
        if not products:
            try:
                from app.services.stores.generic import GenericHandler
                generic = GenericHandler()
                fallback_products = await generic.fetch_products(url, keyword=keyword, limit=limit)
                if fallback_products:
                    for p in fallback_products:
                        p.platform = self.platform_name
                    return fallback_products
            except Exception:
                pass

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
