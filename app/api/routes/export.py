import csv
import io
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import get_current_user, CurrentUser
from app.db.database import get_supabase_client


def _extract_domain(url: str) -> str:
    """Extract domain from URL as fallback for missing retailer_name."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or "Unknown"
    except Exception:
        return "Unknown"


router = APIRouter(prefix="/export", tags=["export"])
security = HTTPBearer()


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filename."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def _format_datetime(iso_timestamp: str) -> tuple[str, str]:
    """Parse ISO timestamp and return (date, time) tuple."""
    if not iso_timestamp:
        return "", ""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except (ValueError, AttributeError):
        return iso_timestamp, ""


def _generate_csv(rows: list[dict], product_name: str) -> io.StringIO:
    """Generate CSV content from price history rows."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Date", "Time", "Competitor", "Price", "Currency", "Status", "Error"])

    for row in rows:
        date_str, time_str = _format_datetime(row.get("scraped_at", ""))
        writer.writerow([
            date_str,
            time_str,
            row.get("retailer_name", "Unknown"),
            row.get("price", "N/A"),
            row.get("currency", "USD"),
            row.get("scrape_status", ""),
            row.get("error_message", ""),
        ])

    output.seek(0)
    return output


@router.get(
    "/{product_id}/csv",
    summary="Export price history to CSV",
    description="Download price history for a product as a CSV file.",
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV file download",
        },
        404: {"description": "Product not found"},
    },
)
def export_price_history_csv(
    product_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Export price history for a product as CSV."""
    client = get_supabase_client(credentials.credentials)

    product_result = (
        client.table("products")
        .select("id, product_name")
        .eq("id", product_id)
        .eq("user_id", current_user.id)
        .execute()
    )
    if not product_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product = product_result.data[0]
    product_name = product["product_name"]

    competitors_result = (
        client.table("competitors")
        .select("id, retailer_name, url")
        .eq("product_id", product_id)
        .execute()
    )
    # Use retailer_name if set, otherwise extract domain from URL
    competitors = {}
    for c in (competitors_result.data or []):
        name = c.get("retailer_name") or _extract_domain(c.get("url", ""))
        competitors[c["id"]] = name
    competitor_ids = list(competitors.keys())

    if not competitor_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No competitors found for this product"
        )

    price_history_result = (
        client.table("price_history")
        .select("competitor_id, price, currency, scraped_at, scrape_status, error_message")
        .in_("competitor_id", competitor_ids)
        .order("scraped_at", desc=True)
        .execute()
    )

    rows = []
    for ph in (price_history_result.data or []):
        rows.append({
            "scraped_at": ph["scraped_at"],
            "retailer_name": competitors.get(ph["competitor_id"], "Unknown"),
            "price": ph["price"],
            "currency": ph["currency"],
            "scrape_status": ph["scrape_status"],
            "error_message": ph["error_message"],
        })

    csv_output = _generate_csv(rows, product_name)

    safe_name = _sanitize_filename(product_name)
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{safe_name}_price_history_{date_str}.csv"

    return StreamingResponse(
        iter([csv_output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
