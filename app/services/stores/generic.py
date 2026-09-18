import json
import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class GenericHandler(BaseStoreHandler):
    """
    Fallback handler for unknown store types using generalized web heuristics.
    Supports single product pages, catalog pages, Schema.org JSON-LD (including @graph),
    OpenGraph meta tags, microdata, Next.js hydration data, and common HTML patterns.
    """

    platform_name = "custom"
    platform_label = "Custom (Generalized Heuristics)"
    confidence = 0.50

    # Common product card selectors
    PRODUCT_SELECTORS = [
        ".product",
        ".product-card",
        ".product-item",
        "[data-product]",
        "[data-product-id]",
        "[data-testid*='product-card']",
        "[data-component='ProductCard']",
        ".products .item",
        ".product-list .item",
        "article.product",
        ".grid-item.product",
        ".collection-product",
        ".product-tile",
        "article[class*='product']",
        "div[class*='ProductCard']",
        "div[class*='ProductItem']",
        "li[class*='product']",
    ]

    # Common price selectors
    PRICE_SELECTORS = [
        "[itemprop='price']",
        "meta[property='product:price:amount']",
        "meta[name='product:price:amount']",
        "meta[property='og:price:amount']",
        "meta[name='og:price:amount']",
        "meta[property='product:sale_price:amount']",
        "[data-price]",
        "[data-product-price]",
        "[data-test*='price']",
        "[data-testid*='price']",
        ".price",
        ".product-price",
        ".current-price",
        ".sale-price",
        ".regular-price",
        ".special-price",
        ".our-price",
        "#product-price",
        ".price-value",
        ".amount",
        ".money",
        "[aria-label*='price' i]",
    ]

    # Common title selectors
    TITLE_SELECTORS = [
        "[itemprop='name']",
        "meta[property='og:title']",
        ".product-title",
        ".product-name",
        ".product-single__title",
        "h1.title",
        "h2.title",
        "h3.title",
        ".product-card__title",
        ".product-item__title",
        "h1",
    ]

    # Common image selectors
    IMAGE_SELECTORS = [
        "[itemprop='image']",
        "meta[property='og:image']",
        ".product-image img",
        ".product-img img",
        ".product-card__image img",
        ".product-featured-media img",
        "img.product-image",
        "picture img",
    ]

    async def detect(self, url: str, html: str | None = None) -> bool:
        """
        Generic handler accepts any valid HTTPS URL as fallback.
        Assesses confidence based on detected eCommerce metadata.
        """
        url = (url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False

        self.matched_signals = ["generic_fallback"]
        self.is_headless = False
        self.platform_label = "Custom (Generalized Heuristics)"
        self.confidence = 0.50

        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/").lower()
        if any(netloc.startswith(prefix) for prefix in ("shop.", "store.", "buy.", "checkout.", "products.")):
            self.matched_signals.append("custom_subdomain")
            self.confidence = max(self.confidence, 0.55)

        if any(p in path for p in ("/product", "/p/", "/item/", "/products", "/shop")):
            self.matched_signals.append("product_url_pattern")
            self.confidence = max(self.confidence, 0.55)

        # If HTML is provided or can be fetched, refine confidence
        page_html = html
        if not page_html:
            try:
                client = await self._get_client()
                res = await client.get(url)
                if res.status_code == 200:
                    page_html = res.text
            except Exception:
                pass

        if page_html:
            soup = BeautifulSoup(page_html[:100000], "lxml")
            has_schema = bool(soup.select('script[type="application/ld+json"]'))
            has_og = bool(soup.select('meta[property*="og:price"], meta[property*="product:price"], meta[name*="product:price"], meta[name*="og:price"]'))
            has_microdata = bool(soup.select('[itemprop="price"]'))
            has_hydration = bool(soup.select('script[id="__NEXT_DATA__"], script[id="__NUXT_DATA__"], script[data-remix-context]'))
            has_selectors = any(bool(soup.select(sel)) for sel in self.PRICE_SELECTORS[:10])

            if has_schema:
                self.matched_signals.append("schema_org_json_ld")
                self.confidence = max(self.confidence, 0.70)
            if has_og:
                self.matched_signals.append("opengraph_price_metadata")
                self.confidence = max(self.confidence, 0.65)
            if has_microdata:
                self.matched_signals.append("microdata_price")
                self.confidence = max(self.confidence, 0.60)
            if has_hydration:
                self.matched_signals.append("hydration_data")
                self.confidence = max(self.confidence, 0.65)
            if has_selectors:
                self.matched_signals.append("heuristic_price_selectors")
                self.confidence = max(self.confidence, 0.55)

            rich_signals = [s for s in ("schema_org_json_ld", "opengraph_price_metadata", "microdata_price", "hydration_data") if s in self.matched_signals]
            if len(rich_signals) >= 2:
                self.confidence = min(0.80, self.confidence + 0.04 * len(rich_signals))

        return True

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """Attempt to extract products using generalized web heuristics."""
        try:
            client = await self._get_client()
            response = await client.get(url)

            if response.status_code != 200:
                return []

            html = response.text
            products = self._parse_products(html, url)

            return self.filter_by_keyword(products, keyword)[:limit]

        except Exception:
            return []

    def _parse_products(self, html: str, base_url: str) -> list[DiscoveredProduct]:
        """
        Parse products from HTML using generalized heuristics.
        Handles catalog pages, JSON-LD Schema.org (@graph/ItemList/Product),
        and single-product pages.
        """
        soup = BeautifulSoup(html, "lxml")
        products: list[DiscoveredProduct] = []

        # 1. Try product card selectors for catalog/collection pages
        for selector in self.PRODUCT_SELECTORS:
            cards = soup.select(selector)
            if len(cards) >= 2:
                for card in cards:
                    product = self._parse_product_card(card, base_url)
                    if product:
                        products.append(product)

                if products:
                    break

        # 2. If no products found via cards, try Schema.org markup (catalog or single product)
        if not products:
            products = self._parse_schema_products(soup, base_url)

        # 3. If still no products, try single product page heuristics
        if not products:
            single = self._parse_single_product(soup, base_url)
            if single:
                products.append(single)

        return products

    def _parse_single_product(self, soup: BeautifulSoup, base_url: str) -> DiscoveredProduct | None:
        """Extract a single product from a product detail page."""
        try:
            # 1. Title
            name = None
            for sel in self.TITLE_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    if el.name == "meta":
                        name = el.get("content", "").strip()
                    else:
                        name = el.get_text(strip=True)
                    if name:
                        break

            if not name and soup.title:
                name = soup.title.get_text(strip=True)
                # Strip common store suffix e.g. "Product Name - StoreName"
                if " - " in name:
                    name = name.split(" - ")[0].strip()
                elif " | " in name:
                    name = name.split(" | ")[0].strip()

            if not name:
                return None

            # 2. Price
            price, currency = self._extract_price(soup)

            # 3. Image
            image_url = None
            for sel in self.IMAGE_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    if el.name == "meta":
                        src = el.get("content")
                    else:
                        src = el.get("src") or el.get("data-src")
                    if src:
                        image_url = urljoin(base_url, src)
                        break

            # 4. Next.js / Hydration state fallback
            if price is None:
                hydrated_price, hydrated_curr = self._extract_hydration_price(soup)
                if hydrated_price:
                    price = hydrated_price
                    currency = hydrated_curr

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=base_url,
                platform=self.platform_name,
            )
        except Exception:
            return None

    def _parse_product_card(
        self,
        card: BeautifulSoup,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse a single product card."""
        try:
            # Title
            name = None
            for selector in self.TITLE_SELECTORS:
                el = card.select_one(selector)
                if el:
                    if el.name == "meta":
                        name = el.get("content", "").strip()
                    else:
                        name = el.get_text(strip=True)
                    if name:
                        break

            if not name:
                heading = card.select_one("h1, h2, h3, h4, a")
                if heading:
                    name = heading.get_text(strip=True)

            if not name:
                return None

            # Product URL
            link = card.select_one("a[href]")
            product_url = ""
            if link:
                href = link.get("href", "")
                product_url = urljoin(base_url, href)

            # Image
            image_url = None
            for selector in self.IMAGE_SELECTORS:
                img = card.select_one(selector)
                if img:
                    src = img.get("src") or img.get("data-src")
                    if src:
                        image_url = urljoin(base_url, src)
                        break

            if not image_url:
                img = card.select_one("img")
                if img:
                    src = img.get("src") or img.get("data-src")
                    if src:
                        image_url = urljoin(base_url, src)

            # Price
            price, currency = self._extract_price(card)

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
            )

        except Exception:
            return None

    def _extract_price(self, container: BeautifulSoup) -> tuple[Decimal | None, str]:
        """Extract price from a container (card or page) using generalized heuristics."""
        for selector in self.PRICE_SELECTORS:
            el = container.select_one(selector)
            if el:
                # 1. Check content or value attributes
                for attr in ("content", "value", "data-price", "data-product-price"):
                    val = el.get(attr)
                    if val:
                        price, currency = self._parse_price_text(str(val))
                        if price is not None:
                            detected_curr = self._detect_currency(el) or currency
                            return price, detected_curr

                # 2. Parse text content
                text = el.get_text(strip=True)
                if text:
                    price, currency = self._parse_price_text(text)
                    if price is not None:
                        detected_curr = self._detect_currency(el) or currency
                        return price, detected_curr

        # Check meta tags
        for price_sel, curr_sel in [
            ("meta[property='product:price:amount']", "meta[property='product:price:currency']"),
            ("meta[name='product:price:amount']", "meta[name='product:price:currency']"),
            ("meta[property='og:price:amount']", "meta[property='og:price:currency']"),
            ("meta[name='og:price:amount']", "meta[name='og:price:currency']"),
            ("meta[property='product:sale_price:amount']", None),
            ("meta[itemprop='price']", "meta[itemprop='priceCurrency']"),
        ]:
            meta_p = container.select_one(price_sel)
            if meta_p and meta_p.get("content"):
                p, c = self._parse_price_text(str(meta_p.get("content")))
                if p and p > 0:
                    if curr_sel:
                        meta_c = container.select_one(curr_sel)
                        if meta_c and meta_c.get("content"):
                            c = meta_c.get("content").strip().upper()
                    return p, c

        # Schema.org JSON-LD fallback within container
        for script in container.select('script[type="application/ld+json"]'):
            script_text = script.string or script.get_text()
            if not script_text:
                continue
            try:
                data = json.loads(script_text)
                from app.services.scraper_service import _find_schema_price
                price, curr = _find_schema_price(data)
                if price and price > 0:
                    return price, curr
            except Exception:
                continue

        return None, "USD"

    def _detect_currency(self, el: BeautifulSoup) -> str:
        """Detect currency from element attributes or parent tree."""
        curr_el = el.select_one("[itemprop='priceCurrency']") or (
            el.find_parent().select_one("[itemprop='priceCurrency']") if el.find_parent() else None
        )
        if curr_el:
            val = curr_el.get("content") or curr_el.get_text(strip=True)
            if val:
                return val.strip().upper()

        parent = el.find_parent()
        if parent:
            meta_curr = parent.select_one("meta[property*='price:currency'], meta[name*='price:currency']")
            if meta_curr and meta_curr.get("content"):
                return meta_curr.get("content").strip().upper()

        return "USD"

    def _parse_price_text(self, text: str) -> tuple[Decimal | None, str]:
        """Parse price text into Decimal and currency with international symbol support."""
        if not text:
            return None, "USD"

        text = text.strip()

        # Detect currency
        currency = "USD"
        if "₦" in text or "NGN" in text.upper():
            currency = "NGN"
        elif "£" in text or "GBP" in text.upper():
            currency = "GBP"
        elif "€" in text or "EUR" in text.upper():
            currency = "EUR"
        elif "¥" in text or "JPY" in text.upper():
            currency = "JPY"
        elif "₹" in text or "INR" in text.upper():
            currency = "INR"
        elif "CAD" in text.upper() or "C$" in text:
            currency = "CAD"
        elif "AUD" in text.upper() or "A$" in text:
            currency = "AUD"
        elif "CHF" in text.upper():
            currency = "CHF"

        # Clean price string (keep commas for European format detection)
        cleaned = re.sub(r"[£€$¥₹₦\s]", "", text)
        cleaned = re.sub(r"[A-Za-z]", "", cleaned)

        # Handle decimal formats
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts[-1]) == 2:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")

        try:
            val = Decimal(cleaned)
            return val, currency
        except Exception:
            return None, currency

    def _extract_hydration_price(self, soup: BeautifulSoup) -> tuple[Decimal | None, str]:
        """Extract price from __NEXT_DATA__ or embedded hydration JSON."""
        script = soup.select_one('script[id="__NEXT_DATA__"]')
        if script and script.string:
            try:
                data = json.loads(script.string)
                # Search for price recursively
                found = self._find_key_recursive(data, ["price", "amount", "minPrice", "regularPrice"])
                if found is not None:
                    price, curr = self._parse_price_text(str(found))
                    if price:
                        return price, curr
            except Exception:
                pass
        return None, "USD"

    def _find_key_recursive(self, obj: any, keys: list[str]) -> any:
        """Search recursively for any matching key in a dictionary or list."""
        if isinstance(obj, dict):
            for k in keys:
                if k in obj and obj[k] is not None:
                    return obj[k]
            for v in obj.values():
                res = self._find_key_recursive(v, keys)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = self._find_key_recursive(item, keys)
                if res is not None:
                    return res
        return None

    def _parse_schema_products(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> list[DiscoveredProduct]:
        """
        Parse products from Schema.org JSON-LD markup.
        Handles single Product, ItemList, arrays, and @graph representations.
        """
        products: list[DiscoveredProduct] = []
        scripts = soup.select('script[type="application/ld+json"]')

        for script in scripts:
            script_text = script.string or script.get_text()
            if not script_text:
                continue
            try:
                data = json.loads(script_text)
                items = self._extract_schema_items(data)

                for item in items:
                    product = self._parse_schema_product(item, base_url)
                    if product:
                        products.append(product)

            except Exception:
                continue

        return products

    def _extract_schema_items(self, data: any) -> list[dict]:
        """Extract product dictionaries from varied JSON-LD structures."""
        items: list[dict] = []
        if isinstance(data, list):
            for el in data:
                items.extend(self._extract_schema_items(el))
        elif isinstance(data, dict):
            # Handle @graph
            if "@graph" in data and isinstance(data["@graph"], list):
                for el in data["@graph"]:
                    items.extend(self._extract_schema_items(el))
            # Handle ItemList
            elif data.get("@type") == "ItemList":
                elements = data.get("itemListElement", [])
                for el in elements:
                    if isinstance(el, dict):
                        items.append(el.get("item", el))
            # Handle Product
            elif self._is_product_type(data.get("@type")):
                items.append(data)

        return items

    def _is_product_type(self, type_val: any) -> bool:
        """Check if @type value indicates a Product."""
        if not type_val:
            return False
        if isinstance(type_val, str):
            return "product" in type_val.lower()
        if isinstance(type_val, list):
            return any("product" in str(t).lower() for t in type_val)
        return False

    def _parse_schema_product(
        self,
        data: dict,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse a single Schema.org Product dictionary."""
        try:
            if "item" in data and isinstance(data["item"], dict):
                data = data["item"]

            name = data.get("name", "")
            if not name:
                return None

            product_url = data.get("url", "")
            if product_url:
                product_url = urljoin(base_url, product_url)
            else:
                product_url = base_url

            image = data.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url", "")

            # Price from offers
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            price = None
            currency = offers.get("priceCurrency", "USD")
            price_val = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
            if not price_val and "priceSpecification" in offers and isinstance(offers["priceSpecification"], dict):
                spec = offers["priceSpecification"]
                price_val = spec.get("price") or spec.get("minPrice") or spec.get("maxPrice")
                if spec.get("priceCurrency"):
                    currency = spec.get("priceCurrency")
            if not price_val and "price" in data:
                price_val = data.get("price")
                if data.get("priceCurrency"):
                    currency = data.get("priceCurrency")

            if price_val:
                try:
                    price = Decimal(str(price_val))
                except Exception:
                    p, c = self._parse_price_text(str(price_val))
                    if p is not None:
                        price = p
                        currency = c

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image if image else None,
                product_url=product_url,
                platform=self.platform_name,
                sku=data.get("sku"),
                raw_data=data,
            )

        except Exception:
            return None
