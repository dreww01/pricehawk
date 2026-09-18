from decimal import Decimal
from urllib.parse import urljoin, urlparse

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct, is_commerce_subdomain


class WooCommerceHandler(BaseStoreHandler):
    """Handler for WooCommerce stores using Store API or REST API."""

    platform_name = "woocommerce"
    platform_label = "WooCommerce"

    # API endpoints in order of preference
    API_ENDPOINTS = [
        "/wp-json/wc/store/products",
        "/wp-json/wc/store/v1/products",
        "/wp-json/wc/v3/products",
        "/wp-json/wc/v2/products",
        "/?rest_route=/wc/store/products",
        "/?rest_route=/wc/v3/products",
        "/index.php?rest_route=/wc/store/products",
        "/wp-json/cocart/v2/products",
    ]

    async def detect(self, url: str, html: str | None = None) -> bool:
        """
        Check if store is WooCommerce via API, URL patterns, or HTML inspection.
        Recognizes classic WooCommerce, custom subdomains, alternative URL paths,
        and headless setups (CoCart, WPGraphQL for WooCommerce).
        """
        url = (url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        hostname = (parsed.hostname or "").lower()
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        query = (parsed.query or "").lower()

        self.matched_signals = []
        self.is_headless = False

        # Signal check: custom commerce subdomain
        if is_commerce_subdomain(hostname) or any(hostname.startswith(prefix) for prefix in ("wp.", "cms.")):
            self.matched_signals.append("custom_subdomain")

        # Signal check: alternative WooCommerce URL patterns
        woo_path_patterns = (
            "/product/", "/product-category/", "/product-tag/", "/shop/",
            "/produkt/", "/produit/", "/producto/",
        )
        if (
            any(p in path for p in woo_path_patterns)
            or path.endswith(("/product", "/shop", "/cart", "/checkout"))
            or "post_type=product" in query
            or "product=" in query
            or "product_id=" in query
            or "rest_route=/wc" in query
            or "wc-api=" in query
            or "wc-ajax=" in query
            or "add-to-cart=" in query
        ):
            self.matched_signals.append("woocommerce_url_pattern")

        # If HTML is provided directly, inspect it first without network I/O
        if html:
            if self._inspect_woocommerce_html(html):
                return True
            return False

        client = await self._get_client()

        # Build candidate base URLs (support subpath WP installs e.g. /shop, /store, /wp)
        base_candidates = [base_url]
        if path and not path.startswith(("/product", "/wp-json")):
            subpath = path.split("/product")[0] if "/product" in path else path
            if subpath and subpath != "/":
                base_candidates.append(f"{base_url}{subpath}")

        # Signal check 1: Probe WooCommerce API endpoints
        for base in base_candidates:
            for endpoint in self.API_ENDPOINTS:
                try:
                    sep = "&" if "?" in endpoint else "?"
                    test_url = f"{base}{endpoint}{sep}per_page=1"
                    response = await client.get(test_url)

                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, list) and len(data) > 0:
                            is_cocart = "cocart" in endpoint
                            self.is_headless = is_cocart
                            self.matched_signals.append(f"api_{endpoint}")
                            self.confidence = 0.98 if not is_cocart else 0.92
                            self.platform_label = "WooCommerce (Headless)" if is_cocart else "WooCommerce"
                            return True

                except Exception:
                    continue

        # Signal check 2: Probe WP REST API root index for WooCommerce namespaces
        for base in base_candidates:
            for root_url in [f"{base}/wp-json/", f"{base}/?rest_route=/"]:
                try:
                    res = await client.get(root_url)
                    if res.status_code == 200:
                        data = res.json()
                        namespaces = data.get("namespaces", [])
                        if any("wc" in ns for ns in namespaces):
                            self.matched_signals.append("wp_json_wc_namespace")
                            self.confidence = 0.95
                            self.platform_label = "WooCommerce"
                            return True
                except Exception:
                    pass

        # Signal check 3: HTML inspection (direct httpx fetch or passed html)
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
            if self._inspect_woocommerce_html(page_html):
                return True

        # Slow path with Playwright for JS-rendered frontends or protected sites
        try:
            from app.services.scraper_service import fetch_with_playwright
            pw_html = await fetch_with_playwright(base_url)
            if pw_html and self._inspect_woocommerce_html(pw_html):
                return True
        except Exception:
            pass

        return False

    def _inspect_woocommerce_html(self, html: str) -> bool:
        """Inspect HTML content for WooCommerce and headless WooCommerce signatures."""
        if not html:
            return False

        html_lower = html.lower()

        # Headless WooCommerce indicators (WPGraphQL, CoCart, Next.js WooCommerce, Faust.js, Frontity, etc.)
        headless_signals = []
        headless_indicators = [
            ("woographql", "woographql"),
            ("wpgraphql", "wpgraphql"),
            ("wpgraphql", "wp-graphql"),
            ("wpgraphql", "wp-graphql-woocommerce"),
            ("cocart", "cocart"),
            ("cocart", "cocart-api"),
            ("wc_store_api", "/wp-json/wc/store"),
            ("wc_store_api", "wc-store-api"),
            ("faustjs", "faustjs"),
            ("faustjs", "@faustwp"),
            ("frontity", "frontity"),
            ("frontity", "wp-frontity"),
            ("woocommerce_store_client", "@woocommerce/store-client"),
            ("gatsby_woocommerce", "gatsby-source-woocommerce"),
            ("gatsby_woocommerce", "gatsby-source-wordpress"),
            ("nuxt_woocommerce", "nuxt-woocommerce"),
            ("nuxt_woocommerce", "@nuxtjs/woocommerce"),
        ]
        for sig_name, marker in headless_indicators:
            if marker in html_lower and sig_name not in headless_signals:
                headless_signals.append(sig_name)

        # Check hydration data
        hydration_contexts = ('__next_data__', '__nuxt_data__', '__nuxt__', '__remix_context__', 'window.__initial_state__', 'window.__preloaded_state__')
        has_hydration_script = any(ctx in html_lower for ctx in hydration_contexts)
        if has_hydration_script and any(k in html_lower for k in ("woographql", "wpgraphql", "cocart", "woocommerce", "wc-store-api", "wp-graphql")):
            if "nextjs_woocommerce" not in headless_signals and '__next_data__' in html_lower:
                headless_signals.append("nextjs_woocommerce")
            elif "headless_hydration_woocommerce" not in headless_signals:
                headless_signals.append("headless_hydration_woocommerce")

        # Standard / Classic WooCommerce signatures
        signals = []
        if "/wp-content/plugins/woocommerce/" in html_lower or "wp-content/plugins/woocommerce" in html_lower or "/wp-content/themes/" in html_lower:
            signals.append("wp_woocommerce_assets")
        if 'name="generator" content="woocommerce' in html_lower or "generator\" content=\"woocommerce" in html_lower:
            signals.append("woocommerce_generator_meta")
        if (
            "woocommerce-price-amount" in html_lower
            or "woocommerce-price-currencysymbol" in html_lower
            or "wc-block" in html_lower
            or "wc-block-components" in html_lower
            or "woocommerce-product-gallery" in html_lower
        ):
            signals.append("woocommerce_css_classes")
        if "wc_add_to_cart_params" in html_lower or "woocommerce_params" in html_lower or "wc_cart_fragments_params" in html_lower or "wcsettings" in html_lower:
            signals.append("woocommerce_js_params")
        if 'rel="https://api.w.org/"' in html_lower or "rel='https://api.w.org/'" in html_lower or 'href="https://api.w.org/"' in html_lower:
            signals.append("wp_api_link")
        if "woocommerce" in html_lower and ("add_to_cart_button" in html_lower or "product_type_simple" in html_lower or "woocommerce-page" in html_lower or "single_add_to_cart_button" in html_lower):
            signals.append("woocommerce_cart_markup")

        all_signals = list(dict.fromkeys(headless_signals + signals))

        if all_signals:
            for s in all_signals:
                if s not in self.matched_signals:
                    self.matched_signals.append(s)

            is_headless = bool(headless_signals)
            self.is_headless = is_headless
            if is_headless:
                self.platform_label = "WooCommerce (Headless)"
                self.confidence = min(0.95, 0.85 + len(all_signals) * 0.03)
            else:
                self.platform_label = "WooCommerce"
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

        # Fallback to generalized web heuristics if API fetch returned no products
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
