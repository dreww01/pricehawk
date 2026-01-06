import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

from bs4 import BeautifulSoup

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class AmazonHandler(BaseStoreHandler):
    """Handler for Amazon store/brand pages and search results."""

    platform_name = "amazon"

    # Amazon domain patterns
    AMAZON_DOMAINS = {
        "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
        "amazon.ca", "amazon.it", "amazon.es", "amazon.com.au",
        "amazon.co.jp", "amazon.in", "amazon.com.mx", "amazon.com.br",
    }

    # Patterns indicating store/brand/search pages
    STORE_PATTERNS = [
        r"/stores/",
        r"/s\?",
        r"/s/",
        r"/brand/",
        r"/gp/browse",
    ]

    async def detect(self, url: str) -> bool:
        """Check if URL is an Amazon store/brand/search page."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Check domain
        is_amazon = any(
            hostname == d or hostname.endswith(f".{d}")
            for d in self.AMAZON_DOMAINS
        )
        if not is_amazon:
            return False

        # Check if it's a store/brand/search page (not single product)
        full_path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        return any(re.search(pattern, full_path) for pattern in self.STORE_PATTERNS)

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """Fetch products from Amazon store/search page."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Modify URL to include keyword if provided
        fetch_url = self._build_search_url(url, keyword)

        products: list[DiscoveredProduct] = []
        page = 1
        max_pages = (limit // 20) + 1

        client = await self._get_client()

        while len(products) < limit and page <= max_pages:
            try:
                page_url = self._add_page_param(fetch_url, page)
                response = await client.get(page_url)

                if response.status_code != 200:
                    break

                html = response.text
                page_products = self._parse_search_results(html, base_url)

                if not page_products:
                    break

                products.extend(page_products)
                page += 1

            except Exception:
                break

        return products[:limit]

    def _build_search_url(self, url: str, keyword: str | None) -> str:
        """Add keyword to search URL if provided."""
        if not keyword:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)

        # Add or update keyword parameter
        query_params["k"] = [keyword]

        new_query = urlencode(query_params, doseq=True)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

    def _add_page_param(self, url: str, page: int) -> str:
        """Add pagination parameter to URL."""
        if page == 1:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        query_params["page"] = [str(page)]

        new_query = urlencode(query_params, doseq=True)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

    def _parse_search_results(
        self,
        html: str,
        base_url: str,
    ) -> list[DiscoveredProduct]:
        """Parse Amazon search/store page HTML."""
        soup = BeautifulSoup(html, "lxml")
        products: list[DiscoveredProduct] = []

        # Product card selectors
        card_selectors = [
            "[data-component-type='s-search-result']",
            ".s-result-item[data-asin]",
            ".sg-col-inner .s-widget-container",
        ]

        for selector in card_selectors:
            cards = soup.select(selector)
            if cards:
                for card in cards:
                    product = self._parse_product_card(card, base_url)
                    if product:
                        products.append(product)
                break

        return products

    def _parse_product_card(
        self,
        card: BeautifulSoup,
        base_url: str,
    ) -> DiscoveredProduct | None:
        """Parse single product card."""
        try:
            # Get ASIN
            asin = card.get("data-asin", "")
            if not asin:
                return None

            # Title
            title_el = card.select_one("h2 a span, .a-text-normal")
            name = title_el.get_text(strip=True) if title_el else ""
            if not name:
                return None

            # Product URL
            link_el = card.select_one("h2 a, a.a-link-normal")
            href = link_el.get("href", "") if link_el else ""
            product_url = urljoin(base_url, href) if href else f"{base_url}/dp/{asin}"

            # Image
            img_el = card.select_one("img.s-image, .s-product-image-container img")
            image_url = img_el.get("src") if img_el else None

            # Price
            price, currency = self._extract_price(card)

            # Stock status
            in_stock = True
            stock_el = card.select_one(".a-color-price")
            if stock_el and "unavailable" in stock_el.get_text(strip=True).lower():
                in_stock = False

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=asin,
                in_stock=in_stock,
                raw_data={"asin": asin},
            )

        except Exception:
            return None

    def _extract_price(self, card: BeautifulSoup) -> tuple[Decimal | None, str]:
        """Extract price from product card."""
        price_selectors = [
            ".a-price .a-offscreen",
            ".a-price-whole",
            "[data-a-color='price'] .a-offscreen",
            ".a-color-price",
        ]

        for selector in price_selectors:
            el = card.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                price, currency = self._parse_price_text(text)
                if price:
                    return price, currency

        return None, "USD"

    def _parse_price_text(self, text: str) -> tuple[Decimal | None, str]:
        """Parse price text into Decimal and currency."""
        if not text:
            return None, "USD"

        # Detect currency
        currency = "USD"
        if "£" in text:
            currency = "GBP"
        elif "€" in text:
            currency = "EUR"
        elif "CAD" in text or "C$" in text:
            currency = "CAD"

        # Clean price string
        cleaned = re.sub(r"[£€$,\s]", "", text)
        cleaned = re.sub(r"[A-Za-z]", "", cleaned)

        # Handle European format (1.234,56)
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
