import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from groq import Groq

from app.core.config import get_settings
from app.db.database import get_supabase_client


class AIService:
    """Service for AI-powered price analysis using Groq."""

    def __init__(self):
        self._client = None
        self.model = "llama-3.3-70b-versatile"

    @property
    def client(self):
        """Lazy initialization of Groq client."""
        if self._client is None:
            settings = get_settings()
            if not settings.groq_api_key:
                raise ValueError("GROQ_API_KEY not configured in environment")
            self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    async def generate_insights(self, product_id: str, user_token: str) -> list[dict[str, Any]]:
        """
        Generate AI insights for a product based on 30 days of price history.

        Returns list of insights with structure:
        [
            {
                "type": "pattern" | "alert" | "recommendation",
                "text": "Insight description",
                "confidence": 0.85
            }
        ]
        """
        # Check if insights were generated today (rate limiting)
        if await self._insights_generated_today(product_id, user_token):
            raise ValueError("Insights already generated today for this product. Please try again tomorrow.")

        # Fetch price history (30 days)
        price_data = await self._fetch_price_history(product_id, user_token, days=30)

        if not price_data or len(price_data) == 0:
            raise ValueError("Insufficient price history. Need at least 1 day of data to generate insights.")

        # Format data for AI
        formatted_data = self._format_price_data(price_data)

        # Generate insights using Groq
        insights = await self._call_groq_api(formatted_data)

        # Store insights in database
        await self._store_insights(product_id, insights)

        return insights

    async def _insights_generated_today(self, product_id: str, user_token: str) -> bool:
        """Check if insights were already generated today for rate limiting."""
        sb = get_supabase_client(user_token)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        response = sb.table("insights").select("id").eq("product_id", product_id).gte("generated_at", today_start.isoformat()).execute()

        return len(response.data) > 0

    async def _fetch_price_history(self, product_id: str, user_token: str, days: int = 30) -> list[dict]:
        """Fetch price history for all competitors of a product."""
        sb = get_supabase_client(user_token)
        cutoff_date = datetime.now() - timedelta(days=days)

        # Get product details
        product_response = sb.table("products").select("*, competitors(*)").eq("id", product_id).single().execute()

        if not product_response.data:
            raise ValueError("Product not found")

        product = product_response.data
        competitors = product.get("competitors", [])

        if not competitors:
            raise ValueError("No competitors found for this product")

        # Fetch price history for each competitor
        all_prices = []
        for competitor in competitors:
            price_response = (
                sb.table("price_history")
                .select("*")
                .eq("competitor_id", competitor["id"])
                .gte("scraped_at", cutoff_date.isoformat())
                .order("scraped_at", desc=False)
                .execute()
            )

            for price_entry in price_response.data:
                all_prices.append({
                    "competitor_id": competitor["id"],
                    "competitor_name": competitor.get("retailer_name", "Unknown"),
                    "competitor_url": competitor["url"],
                    "price": price_entry.get("price"),
                    "currency": price_entry.get("currency", "USD"),
                    "scraped_at": price_entry["scraped_at"],
                    "status": price_entry["scrape_status"]
                })

        return all_prices

    def _format_price_data(self, price_data: list[dict]) -> dict:
        """Format price history data for AI consumption."""
        # Group by competitor
        competitors_data = {}

        for entry in price_data:
            comp_id = entry["competitor_id"]
            if comp_id not in competitors_data:
                competitors_data[comp_id] = {
                    "name": entry["competitor_name"],
                    "url_domain": self._extract_domain(entry["competitor_url"]),
                    "prices": []
                }

            if entry["status"] == "success" and entry["price"]:
                competitors_data[comp_id]["prices"].append({
                    "date": entry["scraped_at"],
                    "price": float(entry["price"]),
                    "currency": entry["currency"]
                })

        # Calculate statistics per competitor
        formatted = []
        for comp_id, comp_data in competitors_data.items():
            prices = [p["price"] for p in comp_data["prices"]]
            if prices:
                formatted.append({
                    "competitor": comp_data["name"],
                    "domain": comp_data["url_domain"],
                    "price_count": len(prices),
                    "average_price": round(sum(prices) / len(prices), 2),
                    "min_price": min(prices),
                    "max_price": max(prices),
                    "current_price": prices[-1] if prices else None,
                    "first_price": prices[0] if prices else None,
                    "price_history": comp_data["prices"][-10:]  # Last 10 data points
                })

        return {
            "competitors": formatted,
            "analysis_period_days": 30,
            "total_competitors": len(formatted)
        }

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL for anonymization."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc

    async def _call_groq_api(self, formatted_data: dict) -> list[dict[str, Any]]:
        """Call Groq API to generate insights."""
        prompt = self._build_prompt(formatted_data)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a price analysis expert. Analyze competitor pricing data and provide actionable insights in valid JSON format only. Do not include markdown formatting or any text outside the JSON structure."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=500,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            parsed = json.loads(content)

            # Validate structure
            insights = parsed.get("insights", [])
            validated_insights = self._validate_insights(insights)

            return validated_insights

        except Exception as e:
            raise ValueError(f"Failed to generate insights from AI: {str(e)}")

    def _build_prompt(self, data: dict) -> str:
        """Build AI prompt with price data."""
        return f"""
Analyze the following competitor pricing data and generate 3-5 actionable insights.

Data:
{json.dumps(data, indent=2)}

Instructions:
1. Identify pricing patterns (weekly cycles, trends, anomalies)
2. Compare competitor pricing strategies
3. Detect significant price changes or alerts
4. Provide actionable recommendations

Output must be valid JSON with this exact structure:
{{
    "insights": [
        {{
            "type": "pattern" | "alert" | "recommendation",
            "text": "Clear, actionable insight (max 200 chars)",
            "confidence": 0.0 to 1.0
        }}
    ]
}}

Rules:
- Generate 3-5 insights
- Each insight text must be under 200 characters
- Use competitor names, not URLs
- Confidence score must be between 0.00 and 1.00
- Types: "pattern" (pricing trends), "alert" (urgent price changes), "recommendation" (suggested actions)
"""

    def _validate_insights(self, insights: list[dict]) -> list[dict[str, Any]]:
        """Validate and sanitize AI-generated insights."""
        validated = []
        allowed_types = {"pattern", "alert", "recommendation"}

        for insight in insights[:5]:  # Max 5 insights
            insight_type = insight.get("type", "").lower()
            text = insight.get("text", "").strip()
            confidence = insight.get("confidence", 0.0)

            # Validate type
            if insight_type not in allowed_types:
                insight_type = "pattern"

            # Sanitize text
            text = self._sanitize_text(text)
            if len(text) > 500:
                text = text[:497] + "..."

            # Validate confidence
            try:
                confidence = float(confidence)
                if confidence < 0.0 or confidence > 1.0:
                    confidence = 0.5
                confidence = round(confidence, 2)
            except (ValueError, TypeError):
                confidence = 0.5

            validated.append({
                "type": insight_type,
                "text": text,
                "confidence": confidence
            })

        return validated

    def _sanitize_text(self, text: str) -> str:
        """Remove potentially harmful content from AI output."""
        # Remove HTML tags
        text = text.replace("<", "&lt;").replace(">", "&gt;")
        # Remove script tags and SQL keywords
        dangerous_patterns = ["<script", "javascript:", "SELECT ", "DROP ", "INSERT ", "UPDATE ", "DELETE "]
        for pattern in dangerous_patterns:
            text = text.replace(pattern, "")
        return text.strip()

    async def _store_insights(self, product_id: str, insights: list[dict]) -> None:
        """Store insights in database using service key."""
        sb = get_supabase_client()  # No token = uses service key, bypasses RLS

        for insight in insights:
            sb.table("insights").insert({
                "product_id": product_id,
                "insight_text": insight["text"],
                "insight_type": insight["type"],
                "confidence_score": str(insight["confidence"])
            }).execute()
