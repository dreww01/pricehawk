from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.db.database import get_supabase_client
from app.db.models import ChartDataResponse, CompetitorChartData, ChartDataPoint


class ChartService:
    """Service for formatting price history data for chart visualization."""

    async def get_chart_data(self, product_id: str, user_token: str, days: int = 30) -> ChartDataResponse:
        """
        Get formatted chart data for a product.

        Returns structured data ready for frontend chart libraries (Chart.js, Plotly, etc.)
        """
        sb = get_supabase_client(user_token)

        # Get product details
        product_response = sb.table("products").select("id, product_name, competitors(*)").eq("id", product_id).single().execute()

        if not product_response.data:
            raise ValueError("Product not found")

        product = product_response.data
        product_name = product["product_name"]
        competitors = product.get("competitors", [])

        if not competitors:
            raise ValueError("No competitors found for this product")

        # Fetch price history for each competitor
        cutoff_date = datetime.now() - timedelta(days=days)
        competitor_chart_data = []
        total_data_points = 0
        earliest_date = None
        latest_date = None

        for competitor in competitors:
            price_response = (
                sb.table("price_history")
                .select("*")
                .eq("competitor_id", competitor["id"])
                .gte("scraped_at", cutoff_date.isoformat())
                .order("scraped_at", desc=False)
                .execute()
            )

            prices = price_response.data
            total_data_points += len(prices)

            # Build data points
            data_points = []
            successful_prices = []

            for price_entry in prices:
                timestamp = datetime.fromisoformat(price_entry["scraped_at"].replace("Z", "+00:00"))

                # Track date range
                if earliest_date is None or timestamp < earliest_date:
                    earliest_date = timestamp
                if latest_date is None or timestamp > latest_date:
                    latest_date = timestamp

                price = Decimal(price_entry["price"]) if price_entry.get("price") else None

                data_points.append(
                    ChartDataPoint(
                        timestamp=timestamp,
                        price=price,
                        currency=price_entry.get("currency", "USD"),
                        status=price_entry["scrape_status"]
                    )
                )

                if price is not None and price_entry["scrape_status"] == "success":
                    successful_prices.append(price)

            # Calculate statistics
            avg_price = None
            min_price = None
            max_price = None
            current_price = None
            price_change_percent = None

            if successful_prices:
                avg_price = sum(successful_prices) / len(successful_prices)
                min_price = min(successful_prices)
                max_price = max(successful_prices)
                current_price = successful_prices[-1]

                # Calculate price change from first to current
                first_price = successful_prices[0]
                if first_price > 0:
                    price_change_percent = ((current_price - first_price) / first_price) * 100

            # Extract retailer name from URL or use domain
            competitor_name = competitor.get("retailer_name") or self._extract_domain(competitor["url"])

            competitor_chart_data.append(
                CompetitorChartData(
                    competitor_id=competitor["id"],
                    competitor_name=competitor_name,
                    url=competitor["url"],
                    data_points=data_points,
                    average_price=avg_price,
                    min_price=min_price,
                    max_price=max_price,
                    current_price=current_price,
                    price_change_percent=price_change_percent
                )
            )

        return ChartDataResponse(
            product_id=product_id,
            product_name=product_name,
            competitors=competitor_chart_data,
            date_range_start=earliest_date,
            date_range_end=latest_date,
            total_data_points=total_data_points
        )

    def _extract_domain(self, url: str) -> str:
        """Extract clean domain name from URL."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc

        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]

        return domain
