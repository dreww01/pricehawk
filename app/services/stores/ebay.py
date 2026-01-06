import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

from bs4 import BeautifulSoup

from app.services.stores.base import BaseStoreHandler, DiscoveredProduct


class EbayHandler(BaseStoreHandler):
    """Handler for eBay store and search pages."""

    platform_name = "ebay"

    # eBay domain patterns
    EBAY_DOMAINS = {
        "ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr",
        "ebay.it", "ebay.es", "ebay.com.au", "ebay.ca",
    }

    # Store/search patterns
    STORE_PATTERNS = [
        r"/str/",
        r"/sch/",
        r"/b/",
        r"/usr/",
    ]

    async def detect(self, url: str) -> bool:
        """Check if URL is an eBay store/search page."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Check domain
        is_ebay = any(
            hostname == d or hostname.endswith(f".{d}")
            for d in self.EBAY_DOMAINS
        )
        if not is_ebay:
            return False

        # Check if it's a store/search page
        return any(re.search(pattern, parsed.path) for pattern in self.STORE_PATTERNS)

    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """Fetch products from eBay store/search page."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        fetch_url = self._build_search_url(url, keyword)

        products: list[DiscoveredProduct] = []
        page = 1
        max_pages = (limit // 50) + 1

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
        """Add keyword to search URL."""
        if not keyword:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)

        query_params["_nkw"] = [keyword]

        new_query = urlencode(query_params, doseq=True)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

    def _add_page_param(self, url: str, page: int) -> str:
        """Add pagination parameter."""
        if page == 1:
            return url

        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        query_params["_pgn"] = [str(page)]

        new_query = urlencode(query_params, doseq=True)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

    def _parse_search_results(
        self,
        html: str,
        base_url: str,
    ) -> list[DiscoveredProduct]:
        """Parse eBay search/store page HTML."""
        soup = BeautifulSoup(html, "lxml")
        products: list[DiscoveredProduct] = []

        # Product listing selectors
        card_selectors = [
            ".s-item",
            ".srp-results .s-item__wrapper",
            "[data-view='mi:1686|iid:1']",
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
        """Parse single eBay listing card."""
        try:
            # Title
            title_el = card.select_one(".s-item__title, .s-item__title span")
            name = title_el.get_text(strip=True) if title_el else ""

            # Skip placeholder items
            if not name or name.lower() == "shop on ebay":
                return None

            # Product URL
            link_el = card.select_one(".s-item__link, a.s-item__link")
            product_url = link_el.get("href", "") if link_el else ""
            if not product_url:
                return None

            # Extract item ID from URL
            item_id = None
            item_match = re.search(r"/itm/(\d+)", product_url)
            if item_match:
                item_id = item_match.group(1)

            # Image
            img_el = card.select_one(".s-item__image-img, img.s-item__image-img")
            image_url = img_el.get("src") if img_el else None

            # Price
            price, currency = self._extract_price(card)

            # Shipping
            shipping_el = card.select_one(".s-item__shipping, .s-item__freeXDays")
            shipping_info = shipping_el.get_text(strip=True) if shipping_el else ""

            return DiscoveredProduct(
                name=name,
                price=price,
                currency=currency,
                image_url=image_url,
                product_url=product_url,
                platform=self.platform_name,
                variant_id=item_id,
                raw_data={"shipping": shipping_info},
            )

        except Exception:
            return None

    def _extract_price(self, card: BeautifulSoup) -> tuple[Decimal | None, str]:
        """Extract price from listing card."""
        price_selectors = [
            ".s-item__price",
            ".s-item__price span.POSITIVE",
            "[itemprop='price']",
        ]

        for selector in price_selectors:
            el = card.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                # Handle price ranges (take lower price)
                if " to " in text:
                    text = text.split(" to ")[0]

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
        elif "C $" in text or "C$" in text:
            currency = "CAD"
        elif "AU $" in text:
            currency = "AUD"

        # Clean price
        cleaned = re.sub(r"[£€$,\s]", "", text)
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
