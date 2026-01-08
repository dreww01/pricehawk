"""
Email service for sending price alert digests using Resend.

Uses Resend test SMTP for development, easily switchable to production API.
Handles HTML and plain text email templates with robust error handling.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from datetime import datetime

from app.core.config import get_settings


# =============================================================================
# CONFIGURATION BLOCK - Easy to tweak
# =============================================================================
class EmailConfig:
    """
    Centralized email configuration for easy adjustments.
    Modify these values to change email behavior.
    """
    # Rate limiting
    MAX_ALERTS_PER_DIGEST = 50  # Maximum alerts to include in one digest

    # SMTP settings (auto-loaded from environment)
    def __init__(self):
        settings = get_settings()
        self.smtp_host = settings.smtp_host or "smtp.resend.com"
        self.smtp_port = settings.smtp_port or 587
        self.smtp_username = settings.smtp_username or "resend"
        self.smtp_password = settings.smtp_password  # Required
        self.from_email = settings.from_email or "noreply@pricehawk.local"
        self.from_name = settings.from_name or "PriceHawk Alerts"

    # Email content limits (security)
    MAX_SUBJECT_LENGTH = 200
    MAX_PRODUCT_NAME_LENGTH = 150

    # Retry settings
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5


# =============================================================================
# Email Service
# =============================================================================
class EmailService:
    """Service for sending email digests via Resend SMTP."""

    def __init__(self):
        self.config = EmailConfig()
        self._validated = False

    def _ensure_configured(self) -> None:
        """Validate that required email configuration is present (lazy)."""
        if self._validated:
            return
        if not self.config.smtp_password:
            raise ValueError(
                "SMTP_PASSWORD not configured. "
                "Get your Resend API key from https://resend.com/api-keys "
                "and set it as SMTP_PASSWORD in .env"
            )
        self._validated = True

    def send_price_alert_digest(
        self,
        to_email: str,
        user_name: str,
        alerts: list[dict[str, Any]],
        digest_period_hours: int = 24
    ) -> dict[str, Any]:
        """
        Send a digest email containing multiple price alerts.

        Args:
            to_email: Recipient email address
            user_name: User's display name (extracted from email)
            alerts: List of alert dicts with structure:
                {
                    "product_name": str,
                    "competitor_name": str,
                    "alert_type": "price_drop" | "price_increase",
                    "old_price": Decimal,
                    "new_price": Decimal,
                    "price_change_percent": Decimal,
                    "currency": str
                }
            digest_period_hours: Time period covered (6, 12, or 24 hours)

        Returns:
            dict with keys: success (bool), message (str), error (str | None)
        """
        self._ensure_configured()

        # Input validation and sanitization
        to_email = self._sanitize_email(to_email)
        user_name = self._sanitize_text(user_name or "User", max_length=100)

        # Limit alerts to prevent abuse
        alerts = alerts[:self.config.MAX_ALERTS_PER_DIGEST]

        if not alerts:
            return {
                "success": False,
                "message": "No alerts to send",
                "error": "Empty alerts list"
            }

        # Build email subject
        alert_count = len(alerts)
        drops = sum(1 for a in alerts if a.get("alert_type") == "price_drop")
        increases = sum(1 for a in alerts if a.get("alert_type") == "price_increase")

        subject_parts = []
        if drops > 0:
            subject_parts.append(f"{drops} price drop{'s' if drops > 1 else ''}")
        if increases > 0:
            subject_parts.append(f"{increases} price increase{'s' if increases > 1 else ''}")

        subject = f"PriceHawk Alert: {' & '.join(subject_parts)}"
        subject = self._sanitize_text(subject, max_length=self.config.MAX_SUBJECT_LENGTH)

        # Generate email body
        html_body = self._generate_html_email(user_name, alerts, digest_period_hours)
        text_body = self._generate_plain_text_email(user_name, alerts, digest_period_hours)

        # Send email with retry logic
        return self._send_email_with_retry(to_email, subject, html_body, text_body)

    def send_test_email(self, to_email: str) -> dict[str, Any]:
        """
        Send a test email to verify email configuration.

        Returns:
            dict with keys: success (bool), message (str)
        """
        self._ensure_configured()
        to_email = self._sanitize_email(to_email)

        subject = "PriceHawk Test Email"
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

        return self._send_email_with_retry(to_email, subject, html_body, text_body)

    def _send_email_with_retry(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str
    ) -> dict[str, Any]:
        """Send email with retry logic."""
        import time

        for attempt in range(self.config.MAX_RETRIES + 1):
            try:
                result = self._send_email_smtp(to_email, subject, html_body, text_body)
                if result["success"]:
                    return result

                # If not last attempt, wait and retry
                if attempt < self.config.MAX_RETRIES:
                    time.sleep(self.config.RETRY_DELAY_SECONDS)

            except Exception as e:
                if attempt == self.config.MAX_RETRIES:
                    return {
                        "success": False,
                        "message": "Email delivery failed after retries",
                        "error": str(e)
                    }
                time.sleep(self.config.RETRY_DELAY_SECONDS)

        return {
            "success": False,
            "message": "Email delivery failed",
            "error": "Max retries exceeded"
        }

    def _send_email_smtp(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str
    ) -> dict[str, Any]:
        """Send email via SMTP (Resend)."""
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.config.from_name} <{self.config.from_email}>"
            msg["To"] = to_email

            # Attach both plain text and HTML versions
            part1 = MIMEText(text_body, "plain", "utf-8")
            part2 = MIMEText(html_body, "html", "utf-8")
            msg.attach(part1)
            msg.attach(part2)

            # Connect and send
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_username, self.config.smtp_password)
                server.send_message(msg)

            return {
                "success": True,
                "message": f"Email sent successfully to {to_email}",
                "error": None
            }

        except smtplib.SMTPAuthenticationError as e:
            return {
                "success": False,
                "message": "SMTP authentication failed",
                "error": "Invalid SMTP credentials. Check SMTP_PASSWORD in .env"
            }
        except smtplib.SMTPException as e:
            return {
                "success": False,
                "message": "SMTP error occurred",
                "error": f"SMTP error: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": "Failed to send email",
                "error": str(e)
            }

    def _generate_html_email(
        self,
        user_name: str,
        alerts: list[dict[str, Any]],
        digest_period_hours: int
    ) -> str:
        """Generate HTML email body."""
        # Build alerts HTML
        alerts_html = ""
        for alert in alerts:
            alert_type = alert.get("alert_type", "price_change")
            product_name = self._sanitize_text(
                alert.get("product_name", "Unknown Product"),
                max_length=self.config.MAX_PRODUCT_NAME_LENGTH
            )
            competitor_name = self._sanitize_text(alert.get("competitor_name", "Unknown Competitor"))
            old_price = float(alert.get("old_price", 0))
            new_price = float(alert.get("new_price", 0))
            change_percent = float(alert.get("price_change_percent", 0))
            currency = alert.get("currency", "USD")

            # Determine color based on alert type
            color = "#16a34a" if alert_type == "price_drop" else "#dc2626"
            arrow = "↓" if alert_type == "price_drop" else "↑"

            alerts_html += f"""
            <div style="background-color: #f9fafb; border-left: 4px solid {color}; padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                <h3 style="margin: 0 0 10px 0; color: #111827; font-size: 16px;">{product_name}</h3>
                <p style="margin: 5px 0; color: #6b7280; font-size: 14px;">
                    <strong>Store:</strong> {competitor_name}
                </p>
                <p style="margin: 10px 0; font-size: 18px; color: {color};">
                    <strong>{arrow} {abs(change_percent):.1f}%</strong>
                </p>
                <p style="margin: 5px 0; color: #374151; font-size: 14px;">
                    <span style="text-decoration: line-through; color: #9ca3af;">{currency} {old_price:.2f}</span>
                    <strong style="margin-left: 10px; color: {color};">{currency} {new_price:.2f}</strong>
                </p>
            </div>
            """

        # Full HTML template
        html = f"""
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
        return html

    def _generate_plain_text_email(
        self,
        user_name: str,
        alerts: list[dict[str, Any]],
        digest_period_hours: int
    ) -> str:
        """Generate plain text email body."""
        lines = [
            "PriceHawk Price Alert Digest",
            "=" * 50,
            "",
            f"Hi {user_name},",
            "",
            f"You have {len(alerts)} price {'alert' if len(alerts) == 1 else 'alerts'} from the last {digest_period_hours} hours:",
            "",
        ]

        for i, alert in enumerate(alerts, 1):
            alert_type = alert.get("alert_type", "price_change")
            product_name = self._sanitize_text(
                alert.get("product_name", "Unknown Product"),
                max_length=self.config.MAX_PRODUCT_NAME_LENGTH
            )
            competitor_name = self._sanitize_text(alert.get("competitor_name", "Unknown Competitor"))
            old_price = float(alert.get("old_price", 0))
            new_price = float(alert.get("new_price", 0))
            change_percent = float(alert.get("price_change_percent", 0))
            currency = alert.get("currency", "USD")

            arrow = "↓" if alert_type == "price_drop" else "↑"

            lines.extend([
                f"{i}. {product_name}",
                f"   Store: {competitor_name}",
                f"   Change: {arrow} {abs(change_percent):.1f}%",
                f"   Price: {currency} {old_price:.2f} → {currency} {new_price:.2f}",
                "",
            ])

        lines.extend([
            "=" * 50,
            "© 2026 PriceHawk. All rights reserved.",
            "You're receiving this because you enabled price alerts.",
        ])

        return "\n".join(lines)

    def _sanitize_email(self, email: str) -> str:
        """Sanitize and validate email address."""
        email = email.strip().lower()

        # Basic validation
        if "@" not in email or "." not in email:
            raise ValueError(f"Invalid email address: {email}")

        # Prevent email header injection
        if "\n" in email or "\r" in email:
            raise ValueError("Email address contains invalid characters")

        return email

    def _sanitize_text(self, text: str, max_length: int = 500) -> str:
        """Sanitize text content for email (XSS prevention)."""
        if not text:
            return ""

        # Strip whitespace
        text = str(text).strip()

        # HTML escape
        text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
        )

        # Remove dangerous patterns
        dangerous = ["javascript:", "<script", "onerror=", "onclick="]
        for pattern in dangerous:
            text = text.replace(pattern, "")

        # Truncate if too long
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."

        return text
