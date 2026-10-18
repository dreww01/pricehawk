"""Read models for user-scoped pending alerts and delivery history."""

from typing import Any

from supabase import Client


class AlertQueryService:
    def __init__(self, client: Client, user_id: str) -> None:
        self.client = client
        self.user_id = user_id

    def pending(self) -> dict[str, Any]:
        response = (
            self.client.table("pending_alerts")
            .select(
                "id, alert_type, old_price, new_price, price_change_percent, detected_at, "
                "old_currency, new_currency, product_id, competitor_id, "
                "products(product_name), competitors(retailer_name, url)"
            )
            .eq("user_id", self.user_id)
            .eq("included_in_digest", False)
            .order("detected_at", desc=True)
            .execute()
        )
        alerts = [
            dict(
                id=row["id"],
                product_id=row["product_id"],
                product_name=row["products"]["product_name"],
                competitor_id=row["competitor_id"],
                competitor_url=row["competitors"]["url"],
                alert_type=row["alert_type"],
                old_price=row["old_price"],
                new_price=row["new_price"],
                price_change_percent=row["price_change_percent"],
                old_currency=row.get("old_currency"),
                new_currency=row.get("new_currency"),
                created_at=row["detected_at"],
            )
            for row in response.data or []
        ]
        return dict(alerts=alerts, total=len(alerts))

    def history(self, limit: int) -> dict[str, Any]:
        response = (
            self.client.table("alert_history")
            .select("*")
            .eq("user_id", self.user_id)
            .order("digest_sent_at", desc=True)
            .limit(limit)
            .execute()
        )
        alerts = []
        for row in response.data or []:
            email = row.get("email_status", "disabled")
            webhook = row.get("webhook_status", "disabled")
            delivered = "sent" in (email, webhook)
            delivery_status = (
                "delivered"
                if delivered
                else ("failed" if "failed" in (email, webhook) else "disabled")
            )
            alerts.append(
                dict(
                    id=row["id"],
                    digest_sent_at=row["digest_sent_at"],
                    timestamp=row["digest_sent_at"],
                    alerts_count=row["alerts_count"],
                    price_drops=row.get("price_drops", 0),
                    price_increases=row.get("price_increases", 0),
                    currency_changes=row.get("currency_changes", 0),
                    email_status=email,
                    webhook_status=webhook,
                    delivered=delivered,
                    delivered_status=delivery_status,
                    response_code=row.get("response_code"),
                    status_code=row.get("response_code"),
                    error_message=row.get("error_message"),
                )
            )
        return dict(alerts=alerts, total=len(alerts))
