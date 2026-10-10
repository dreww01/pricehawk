"""HTML parsing heuristics, price cleaning, and currency normalization."""
from decimal import Decimal
import json
import re
from bs4 import BeautifulSoup
from app.services.scraper.detector import detect_platform_from_html

PRICE_SELECTORS = {
    "shopify": [
        ".price__current .money", ".product__price .money", ".product-price .money", "[data-product-price]",
        ".price-item--regular", ".price-item--sale", ".price__sale .price-item", ".price__regular .price-item",
        ".ProductMeta__Price", ".product-single__price", ".price-item",
    ],
    "woocommerce": [
        ".woocommerce-Price-amount bdi", ".woocommerce-Price-amount", ".price ins .amount", ".price .amount",
        ".summary .price", "p.price span.amount", ".entry-summary .price", ".wc-block-components-product-price",
    ],
    "generic": [
        "[itemprop='price']", "[itemprop='lowPrice']", "[data-price]", "[data-product-price]", "[data-sale-price]",
        "[data-regular-price]", "meta[property='product:price:amount']", "meta[property='og:price:amount']",
        ".price", ".product-price", ".product__price", ".current-price", ".sale-price", ".regular-price",
        ".special-price", ".final-price", ".offer-price", "#product-price", ".price-value", ".amount", ".money",
        ".pdp-price", ".price-item",
    ],
}


def parse_price(text: str) -> tuple[Decimal | None, str]:
    """Extract price and currency from text, handling international symbols and formats."""
    if not text:
        return None, "USD"
    text = text.strip()
    currency = "USD"
    curr_map = [("₦", "NGN"), ("NGN", "NGN"), ("£", "GBP"), ("GBP", "GBP"), ("€", "EUR"), ("EUR", "EUR"),
                ("CAD", "CAD"), ("C$", "CAD"), ("AUD", "AUD"), ("A$", "AUD"), ("¥", "JPY"), ("JPY", "JPY"), ("₹", "INR"), ("INR", "INR")]
    for sym, code in curr_map:
        if sym in text or sym in text.upper():
            currency = code
            break

    cleaned = re.sub(r"[£€$¥₹₦\sA-Za-z]", "", text)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".") if cleaned.rfind(",") > cleaned.rfind(".") else cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = cleaned.replace(",", ".") if len(parts[-1]) == 2 else cleaned.replace(",", "")

    try:
        return Decimal(cleaned), currency
    except Exception:
        return None, currency


def _detect_meta_currency(soup: BeautifulSoup) -> str | None:
    """Detect currency from metadata tags in page head."""
    for sel in ["meta[property='product:price:currency']", "meta[property='og:price:currency']", "meta[itemprop='priceCurrency']", "meta[name='twitter:data2']"]:
        el = soup.select_one(sel)
        if el and el.get("content"):
            return el.get("content").strip()
    return None


def _extract_price_from_json_ld(soup: BeautifulSoup) -> tuple[Decimal | None, str | None]:
    """Extract price and currency from schema.org JSON-LD scripts."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            raw_text = script.get_text(strip=True) if script else ""
            if not raw_text:
                continue
            data, items = json.loads(raw_text), []
            queue = [data]
            while queue:
                curr = queue.pop(0)
                if isinstance(curr, list):
                    queue.extend(curr)
                elif isinstance(curr, dict):
                    if "@graph" in curr and isinstance(curr["@graph"], list):
                        queue.extend(curr["@graph"])
                    curr_type = curr.get("@type", "")
                    types = [curr_type.lower()] if isinstance(curr_type, str) else [str(t).lower() for t in curr_type]
                    if any(t == "product" or t.endswith("/product") for t in types):
                        items.append(curr)
                    if "item" in curr and isinstance(curr["item"], dict):
                        items.append(curr["item"])

            for item in items:
                offers = item.get("offers", {})
                offers_list = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
                for offer in offers_list:
                    if isinstance(offer, dict):
                        price_val = offer.get("price") or offer.get("lowPrice") or offer.get("highPrice")
                        currency = offer.get("priceCurrency", "USD")
                        if price_val:
                            price_dec, cur = parse_price(str(price_val))
                            if price_dec and price_dec > 0:
                                return price_dec, currency or cur
        except Exception:
            continue
    return None, None


def extract_price_from_html(html: str, retailer: str = "unknown") -> tuple[Decimal | None, str]:
    """Extract price and currency using CSS selectors, JSON-LD, meta tags, and heuristics."""
    soup = BeautifulSoup(html, "lxml")
    selector_groups = []
    if retailer in PRICE_SELECTORS:
        selector_groups.append(PRICE_SELECTORS[retailer])
    if retailer == "unknown":
        detected = detect_platform_from_html(html)
        if detected and detected in PRICE_SELECTORS:
            selector_groups.append(PRICE_SELECTORS[detected])
    selector_groups.append(PRICE_SELECTORS["generic"])

    for selectors in selector_groups:
        for selector in selectors:
            try:
                for el in soup.select(selector):
                    content = (el.get("content", "") if selector.startswith("meta[")
                               else el.get("content") or el.get("data-price") or el.get("value") or el.get_text(strip=True))
                    if content:
                        price, currency = parse_price(str(content))
                        if price and price > 0:
                            meta_cur = _detect_meta_currency(soup)
                            return price, (meta_cur if meta_cur and (currency == "USD" or not currency) else currency)
            except Exception:
                continue

    try:
        p, c = _extract_price_from_json_ld(soup)
        if p and p > 0:
            return p, c or "USD"
    except Exception:
        pass

    meta_tags = [
        ("meta[property='product:price:amount']", "meta[property='product:price:currency']"),
        ("meta[property='og:price:amount']", "meta[property='og:price:currency']"),
        ("meta[property='product:sale_price:amount']", "meta[property='product:price:currency']"),
        ("meta[itemprop='price']", "meta[itemprop='priceCurrency']"),
        ("meta[itemprop='lowPrice']", "meta[itemprop='priceCurrency']"),
        ("meta[name='twitter:data1']", None),
    ]
    for p_tag, c_tag in meta_tags:
        try:
            p_el = soup.select_one(p_tag)
            if p_el:
                content = p_el.get("content") or p_el.get("value") or p_el.get_text(strip=True)
                if content:
                    price, cur = parse_price(str(content))
                    if price and price > 0:
                        if c_tag and soup.select_one(c_tag) and soup.select_one(c_tag).get("content"):
                            cur = soup.select_one(c_tag).get("content")
                        return price, cur
        except Exception:
            continue

    data_attrs = ["[data-price]", "[data-product-price]", "[data-sale-price]", "[data-regular-price]", "[data-price-amount]", "[data-amount]", "[data-clean-price]"]
    for sel in data_attrs:
        try:
            for el in soup.select(sel):
                val = el.get("data-price") or el.get("data-product-price") or el.get("data-sale-price") or el.get("data-regular-price") or el.get("data-price-amount") or el.get("data-amount") or el.get("data-clean-price")
                if val:
                    price, cur = parse_price(str(val))
                    if price and price > 0:
                        return price, cur
        except Exception:
            continue

    try:
        for container in soup.select("[class*='price'], [id*='price'], [class*='pdp'], [data-testid*='price']"):
            if container.name in ("script", "style", "noscript"):
                continue
            text = container.get_text(separator=" ", strip=True)
            matches = re.findall(r"([$£€¥₹₦]|CAD|USD|EUR|GBP|AUD)\s*(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?|\d+(?:,\d{2}))", text, re.IGNORECASE)
            if matches:
                price, cur = parse_price(f"{matches[0][0]}{matches[0][1]}")
                if price and price > 0:
                    return price, cur
    except Exception:
        pass

    return None, "USD"


class ScraperParser:
    """Parser component interface for HTML and price parsing."""
    parse_price = staticmethod(parse_price)
    extract_price_from_html = staticmethod(extract_price_from_html)
