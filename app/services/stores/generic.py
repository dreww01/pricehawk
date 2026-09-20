import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class GenericHandler(BaseStoreHandler):
    """Fallback handler for unknown store types using common HTML patterns and web heuristics."""

    platform_name = "custom"
    platform_label = "Custom / Web Heuristics"
    confidence = 0.50

    # Common product card selectors
    PRODUCT_SELECTORS = [
        ".product",
        ".product-card",
        ".product-item",
        "[data-product]",
        ".products .item",
        ".product-list .item",
        "article.product",
        ".grid-item.product",
        ".collection-product",
    ]

    # Common price selectors
    PRICE_SELECTORS = [
        "[itemprop='price']",
        ".price",
        ".product-price",
        ".current-price",
        ".sale-price",
        ".regular-price",
        "[data-price]",
        ".money",
    ]

    # Common title selectors
    TITLE_SELECTORS = [
        "[itemprop='name']",
        ".product-title",
        ".product-name",
        "h2.title",
        "h3.title",
        ".product-card__title",
        ".product-item__title",
    ]

    # Common image selectors
    IMAGE_SELECTORS = [
        "[itemprop='image']",
        ".product-image img",
        ".product-img img",
        ".product-card__image img",
        "img.product-image",
        "picture img",
    ]

    async def detect(self, url: str) -> bool:
        """Generic handler accepts any valid URL as fallback."""
        # Check if URL is valid HTTPS
        parsed = urlparse(url)
        is_valid = parsed.scheme == "https" and bool(parsed.netloc)
        if is_valid:
            self.platform_label = "Custom / Web Heuristics"
            self.confidence = 0.50
            self.signatures = ["fallback:generalized_heuristics"]
        return is_valid

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """Attempt to extract products using common HTML patterns."""
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
        """Parse products from HTML using common patterns."""
        soup = BeautifulSoup(html, "lxml")
        products: list[DiscoveredProduct] = []

        # Try each product selector
        for selector in self.PRODUCT_SELECTORS:
            cards = soup.select(selector)
            if len(cards) >= 1:
                for card in cards:
                    product = self._parse_product_card(card, base_url)
                    if product:
                        products.append(product)

                if products:
                    break

        # If no products found with selectors, try schema.org markup
        if not products:
            products = self._parse_schema_products(soup, base_url)

        return products

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
                    name = el.get_text(strip=True)
                    break

            if not name:
                # Fallback to first heading or link text
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
                platform_label=self.platform_label,
                confidence=self.confidence,
            )

        except Exception:
            return None

    def _extract_price(self, card: BeautifulSoup) -> tuple[Decimal | None, str]:
        """Extract price from product card."""
        for selector in self.PRICE_SELECTORS:
            el = card.select_one(selector)
            if el:
                # Check for content attribute (schema.org)
                content = el.get("content")
                if content:
                    try:
                        return Decimal(content), self._detect_currency(el)
                    except Exception:
                        pass

                # Parse text content
                text = el.get_text(strip=True)
                price, currency = self._parse_price_text(text)
                if price:
                    return price, currency

        return None, "USD"

    def _detect_currency(self, el: BeautifulSoup) -> str:
        """Detect currency from element attributes or parent."""
        # Check itemprop currency
        currency_el = el.find_parent().select_one("[itemprop='priceCurrency']") if el.find_parent() else None
        if currency_el:
            return currency_el.get("content", "USD")

        return "USD"

    def _parse_price_text(self, text: str) -> tuple[Decimal | None, str]:
        """Parse price text into Decimal and currency."""
        if not text:
            return None, "USD"

        # Detect currency
        currency = "USD"
        if "₦" in text or "NGN" in text.upper():
            currency = "NGN"
        elif "£" in text or "GBP" in text.upper():
            currency = "GBP"
        elif "€" in text or "EUR" in text.upper():
            currency = "EUR"
        elif "CAD" in text.upper() or "C$" in text:
            currency = "CAD"
        elif "AUD" in text.upper() or "A$" in text:
            currency = "AUD"
        elif "¥" in text or "JPY" in text.upper():
            currency = "JPY"
        elif "₹" in text or "INR" in text.upper():
            currency = "INR"

        # Clean price
        cleaned = re.sub(r"[£€$¥₹₦,\s]", "", text)
        cleaned = re.sub(r"[A-Za-z]", "", cleaned)

        # Handle decimal formats
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")

        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts[-1]) == 2:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")

        try:
            return Decimal(cleaned), currency
        except Exception:
            return None, currency

    def _extract_items_from_json_ld(self, data: object) -> list[dict]:
        """Recursively extract Product and ItemList elements from JSON-LD, supporting @graph arrays."""
        items: list[dict] = []
        if isinstance(data, list):
            for item in data:
                items.extend(self._extract_items_from_json_ld(item))
        elif isinstance(data, dict):
            # Support Schema.org @graph
            if "@graph" in data:
                items.extend(self._extract_items_from_json_ld(data["@graph"]))

            t = data.get("@type")
            types: list[str] = []
            if isinstance(t, str):
                types = [t.lower()]
            elif isinstance(t, list):
                types = [str(x).lower() for x in t]

            if any(x == "product" or x.endswith("/product") for x in types):
                items.append(data)
            elif any(x == "itemlist" or x.endswith("/itemlist") for x in types):
                items.extend(self._extract_items_from_json_ld(data.get("itemListElement", [])))

            if "item" in data and isinstance(data["item"], dict):
                items.extend(self._extract_items_from_json_ld(data["item"]))

        return items

    def _parse_schema_products(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> list[DiscoveredProduct]:
        """Parse products from schema.org JSON-LD markup, supporting @graph and modern schemas."""
        products: list[DiscoveredProduct] = []

        # Find JSON-LD scripts
        scripts = soup.select('script[type="application/ld+json"]')

        for script in scripts:
            try:
                import json
                if not script.string:
                    continue
                data = json.loads(script.string)
                items = self._extract_items_from_json_ld(data)

                for item in items:
                    product = self._parse_schema_product(item, base_url)
                    if product:
                        products.append(product)

            except Exception:
                continue

        return products

    def _parse_schema_product(
        self,
        data: dict,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse a single schema.org Product with Offer or AggregateOffer."""
        try:
            # Handle ItemList wrapper
            if "item" in data and isinstance(data["item"], dict):
                data = data["item"]

            name = data.get("name", "")
            if not name:
                return None

            product_url = data.get("url", "")
            if product_url:
                product_url = urljoin(base_url, product_url)

            image = data.get("image", "")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url", "")

            # Price from offers (supports Offer and AggregateOffer)
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            price = None
            currency = offers.get("priceCurrency", "USD")
            price_val = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
            if price_val:
                try:
                    price = Decimal(str(price_val))
                except Exception:
                    pass

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image if image else None,
                product_url=product_url,
                platform=self.platform_name,
                platform_label=self.platform_label,
                confidence=self.confidence,
                sku=data.get("sku"),
                raw_data=data,
            )

        except Exception:
            return None
