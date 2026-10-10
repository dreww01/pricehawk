"""HTML parsing heuristics, price cleaning, and currency normalization."""
import json
import re
from decimal import Decimal
from bs4 import BeautifulSoup

from app.services.scraper.detector import detect_platform_from_html

PRICE_SELECTORS: dict[str, list[str]] = {
    "shopify": [
        ".price__current .money", ".product__price .money", ".product-price .money",
        "[data-product-price]", ".price-item--regular", ".price-item--sale",
        ".price__sale .price-item", ".price__regular .price-item",
        ".ProductMeta__Price", ".product-single__price", ".price-item",
    ],
    "woocommerce": [
        ".woocommerce-Price-amount bdi", ".woocommerce-Price-amount", ".price ins .amount",
        ".price .amount", ".summary .price", "p.price span.amount", ".entry-summary .price",
        ".wc-block-components-product-price",
    ],
    "generic": [
        "[itemprop='price']", "[itemprop='lowPrice']", "[data-price]", "[data-product-price]",
        "[data-sale-price]", "[data-regular-price]", "meta[property='product:price:amount']",
        "meta[property='og:price:amount']", ".price", ".product-price", ".product__price",
        ".current-price", ".sale-price", ".regular-price", ".special-price", ".final-price",
        ".offer-price", "#product-price", ".price-value", ".amount", ".money", ".pdp-price", ".price-item",
    ],
}
META_PRICE_TAGS = [
    ("meta[property='product:price:amount']", "meta[property='product:price:currency']"),
    ("meta[property='og:price:amount']", "meta[property='og:price:currency']"),
    ("meta[property='product:sale_price:amount']", "meta[property='product:price:currency']"),
    ("meta[itemprop='price']", "meta[itemprop='priceCurrency']"),
    ("meta[itemprop='lowPrice']", "meta[itemprop='priceCurrency']"),
    ("meta[name='twitter:data1']", None),
]
DATA_ATTRS = (
    "data-price", "data-product-price", "data-sale-price", "data-regular-price",
    "data-price-amount", "data-amount", "data-clean-price",
)


def parse_price(text: str) -> tuple[Decimal | None, str]:
    """Extract price and currency from text, handling international currency symbols."""
    if not text:
        return None, "USD"
    text = text.strip()
    currency = "USD"
    for sym, code in [
        ("₦", "NGN"), ("NGN", "NGN"), ("£", "GBP"), ("GBP", "GBP"), ("€", "EUR"), ("EUR", "EUR"),
        ("CAD", "CAD"), ("C$", "CAD"), ("AUD", "AUD"), ("A$", "AUD"), ("¥", "JPY"), ("JPY", "JPY"),
        ("₹", "INR"), ("INR", "INR"),
    ]:
        if sym in text or sym in text.upper():
            currency = code
            break

    cleaned = re.sub(r"[£€$¥₹₦\s]", "", text)
    cleaned = re.sub(r"[A-Za-z]", "", cleaned)

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
    for sel in ("meta[property='product:price:currency']", "meta[property='og:price:currency']", "meta[itemprop='priceCurrency']", "meta[name='twitter:data2']"):
        el = soup.select_one(sel)
        if el and el.get("content"):
            return el.get("content").strip()
    return None


def _extract_price_from_json_ld(soup: BeautifulSoup) -> tuple[Decimal | None, str | None]:
    """Extract price and currency from schema.org JSON-LD scripts with @graph support."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            raw = script.get_text(strip=True) if script else ""
            if not raw:
                continue
            queue, items = [json.loads(raw)], []
            while queue:
                curr = queue.pop(0)
                if isinstance(curr, list):
                    queue.extend(curr)
                elif isinstance(curr, dict):
                    if isinstance(curr.get("@graph"), list):
                        queue.extend(curr["@graph"])
                    c_type = curr.get("@type", "")
                    types = [c_type.lower()] if isinstance(c_type, str) else [str(t).lower() for t in c_type]
                    if any(t == "product" or t.endswith("/product") for t in types):
                        items.append(curr)
                    if isinstance(curr.get("item"), dict):
                        items.append(curr["item"])
            for item in items:
                offers = item.get("offers", {})
                offers_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
                for offer in offers_list:
                    if isinstance(offer, dict):
                        pval = offer.get("price") or offer.get("lowPrice") or offer.get("highPrice")
                        if pval:
                            p_dec, c = parse_price(str(pval))
                            if p_dec and p_dec > 0:
                                return p_dec, offer.get("priceCurrency", "USD") or c
        except Exception:
            continue
    return None, None


def extract_price_from_html(html: str, retailer: str) -> tuple[Decimal | None, str]:
    """Extract price using CSS selectors, platform detection, and generalized heuristics."""
    soup = BeautifulSoup(html, "lxml")
    groups: list[list[str]] = []
    if retailer in PRICE_SELECTORS:
        groups.append(PRICE_SELECTORS[retailer])
    if retailer == "unknown":
        detected = detect_platform_from_html(html)
        if detected and detected in PRICE_SELECTORS:
            groups.append(PRICE_SELECTORS[detected])
    groups.append(PRICE_SELECTORS["generic"])

    for selectors in groups:
        for sel in selectors:
            try:
                for el in soup.select(sel):
                    content = el.get("content") or el.get("data-price") or el.get("value") or el.get_text(strip=True)
                    if content:
                        price, cur = parse_price(str(content))
                        if price and price > 0:
                            meta_cur = _detect_meta_currency(soup)
                            return price, (meta_cur if meta_cur and (cur == "USD" or not cur) else cur)
            except Exception:
                continue

    try:
        j_price, j_cur = _extract_price_from_json_ld(soup)
        if j_price and j_price > 0:
            return j_price, j_cur or "USD"
    except Exception:
        pass

    for p_tag, c_tag in META_PRICE_TAGS:
        try:
            p_el = soup.select_one(p_tag)
            if p_el:
                content = p_el.get("content") or p_el.get("value") or p_el.get_text(strip=True)
                if content:
                    price, cur = parse_price(str(content))
                    if price and price > 0:
                        c_el = soup.select_one(c_tag) if c_tag else None
                        return price, (c_el.get("content") if c_el and c_el.get("content") else cur)
        except Exception:
            continue

    for sel in [f"[{a}]" for a in DATA_ATTRS]:
        try:
            for el in soup.select(sel):
                val = next((el.get(a) for a in DATA_ATTRS if el.get(a)), None)
                if val:
                    price, cur = parse_price(str(val))
                    if price and price > 0:
                        return price, cur
        except Exception:
            continue

    try:
        for cont in soup.select("[class*='price'], [id*='price'], [class*='pdp'], [data-testid*='price']"):
            if cont.name in ("script", "style", "noscript"):
                continue
            text = cont.get_text(separator=" ", strip=True)
            matches = re.findall(r"([$£€¥₹₦]|CAD|USD|EUR|GBP|AUD)\s*(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?|\d+(?:,\d{2}))", text, re.IGNORECASE)
            if matches:
                price, cur = parse_price(f"{matches[0][0]}{matches[0][1]}")
                if price and price > 0:
                    return price, cur
    except Exception:
        pass

    return None, "USD"


class ScraperParser:
    """Class parser facade for price parsing and DOM extraction."""
    parse_price = staticmethod(parse_price)
    extract_price = staticmethod(extract_price_from_html)
