"""Pure email content builders; no SMTP or application configuration dependencies."""

from datetime import datetime
from html import escape
from typing import Any


def sanitize_text(text: str, max_length: int = 500) -> str:
    """Escape untrusted content and retain the existing digest content limits."""
    if not text:
        return ""
    text = escape(str(text).strip(), quote=True)
    for pattern in ("javascript:", "<script", "onerror=", "onclick="):
        text = text.replace(pattern, "")
    return text[: max_length - 3] + "..." if len(text) > max_length else text


def digest_subject(alerts: list[dict[str, Any]], max_length: int = 200) -> str:
    parts = []
    for alert_type, label in (
        ("price_drop", "price drop"),
        ("price_increase", "price increase"),
        ("currency_changed", "currency change"),
    ):
        count = sum(alert.get("alert_type") == alert_type for alert in alerts)
        if count:
            parts.append(f"{count} {label}{'s' if count > 1 else ''}")
    return sanitize_text(f"PriceHawk Alert: {' & '.join(parts)}", max_length)


def _html_alert(alert: dict[str, Any], max_product_name_length: int) -> str:
    alert_type = alert.get("alert_type", "price_change")
    product_name = sanitize_text(
        alert.get("product_name", "Unknown Product"), max_product_name_length
    )
    competitor_name = sanitize_text(alert.get("competitor_name", "Unknown Competitor"))
    old_price = float(alert.get("old_price") or 0)
    new_price = float(alert.get("new_price") or 0)
    change_percent = float(alert.get("price_change_percent") or 0)
    currency = sanitize_text(alert.get("currency", "USD"))
    if alert_type == "currency_changed":
        color = "#d97706"
        change_label = sanitize_text(
            f"Currency changed: {alert.get('old_currency') or 'unknown'} → "
            f"{alert.get('new_currency') or 'unknown'}"
        )
        price_label = "Review the competitor currency before comparing prices."
    else:
        color = "#16a34a" if alert_type == "price_drop" else "#dc2626"
        arrow = "↓" if alert_type == "price_drop" else "↑"
        change_label = f"{arrow} {abs(change_percent):.1f}%"
        price_label = (
            '<span style="text-decoration: line-through; color: #9ca3af;">'
            f"{currency} {old_price:.2f}</span>"
            f'<strong style="margin-left: 10px; color: {color};">'
            f"{currency} {new_price:.2f}</strong>"
        )
    return f"""
            <div style="background-color: #f9fafb; border-left: 4px solid {color}; padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                <h3 style="margin: 0 0 10px 0; color: #111827; font-size: 16px;">{product_name}</h3>
                <p style="margin: 5px 0; color: #6b7280; font-size: 14px;">
                    <strong>Store:</strong> {competitor_name}
                </p>
                <p style="margin: 10px 0; font-size: 18px; color: {color};">
                    <strong>{change_label}</strong>
                </p>
                <p style="margin: 5px 0; color: #374151; font-size: 14px;">
                    {price_label}
                </p>
            </div>
            """


def html_digest(
    user_name: str,
    alerts: list[dict[str, Any]],
    digest_period_hours: int,
    max_product_name_length: int = 150,
) -> str:
    """Render all untrusted names and currencies as escaped HTML text."""
    user_name = sanitize_text(user_name or "User", 100)
    alerts_html = "".join(_html_alert(alert, max_product_name_length) for alert in alerts)
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #f3f4f6;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 20px;">
                <div style="text-align: center; padding: 20px 0; border-bottom: 2px solid #e5e7eb;">
                    <h1 style="margin: 0; color: #111827; font-size: 24px;">🦅 PriceHawk</h1>
                    <p style="margin: 5px 0 0 0; color: #6b7280; font-size: 14px;">Price Alert Digest</p>
                </div>

                <div style="padding: 30px 0;">
                    <p style="font-size: 16px; color: #374151; margin-bottom: 20px;">
                        Hi {user_name},
                    </p>
                    <p style="font-size: 14px; color: #6b7280; margin-bottom: 30px;">
                        You have {len(alerts)} price {'alert' if len(alerts) == 1 else 'alerts'} from the last {digest_period_hours} hours:
                    </p>

                    {alerts_html}
                </div>

                <div style="text-align: center; padding: 20px 0; border-top: 2px solid #e5e7eb;">
                    <p style="font-size: 12px; color: #9ca3af; margin: 0;">
                        © 2026 PriceHawk. All rights reserved.
                    </p>
                    <p style="font-size: 12px; color: #9ca3af; margin: 10px 0 0 0;">
                        You're receiving this because you enabled price alerts.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """


def plain_text_digest(
    user_name: str,
    alerts: list[dict[str, Any]],
    digest_period_hours: int,
    max_product_name_length: int = 150,
) -> str:
    user_name = sanitize_text(user_name or "User", 100)
    lines = [
        "PriceHawk Price Alert Digest",
        "=" * 50,
        "",
        f"Hi {user_name},",
        "",
        f"You have {len(alerts)} price {'alert' if len(alerts) == 1 else 'alerts'} from the last {digest_period_hours} hours:",
        "",
    ]
    for index, alert in enumerate(alerts, 1):
        alert_type = alert.get("alert_type", "price_change")
        product_name = sanitize_text(
            alert.get("product_name", "Unknown Product"), max_product_name_length
        )
        competitor_name = sanitize_text(alert.get("competitor_name", "Unknown Competitor"))
        old_price = float(alert.get("old_price") or 0)
        new_price = float(alert.get("new_price") or 0)
        change_percent = float(alert.get("price_change_percent") or 0)
        currency = alert.get("currency", "USD")
        if alert_type == "currency_changed":
            detail_lines = [
                f"   Currency: {alert.get('old_currency') or 'unknown'} → "
                f"{alert.get('new_currency') or 'unknown'}"
            ]
        else:
            arrow = "↓" if alert_type == "price_drop" else "↑"
            detail_lines = [
                f"   Change: {arrow} {abs(change_percent):.1f}%",
                f"   Price: {currency} {old_price:.2f} → {currency} {new_price:.2f}",
            ]
        lines.extend(
            [f"{index}. {product_name}", f"   Store: {competitor_name}", *detail_lines, ""]
        )
    lines.extend(
        [
            "=" * 50,
            "© 2026 PriceHawk. All rights reserved.",
            "You're receiving this because you enabled price alerts.",
        ]
    )
    return "\n".join(lines)


def test_email() -> tuple[str, str, str]:
    """Build the configuration smoke-test email without sending it."""
    html_body = """
        <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2>PriceHawk Email Test</h2>
                <p>This is a test email from PriceHawk.</p>
                <p>If you received this, your email configuration is working correctly!</p>
                <p style="color: #666; font-size: 12px;">
                    Sent at: {timestamp}
                </p>
            </body>
        </html>
        """.format(timestamp=datetime.now().isoformat())
    text_body = f"""
        PriceHawk Email Test

        This is a test email from PriceHawk.
        If you received this, your email configuration is working correctly!

        Sent at: {datetime.now().isoformat()}
        """
    return "PriceHawk Test Email", html_body, text_body
