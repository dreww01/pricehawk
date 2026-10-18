"""Email digest delivery via Resend SMTP, with lazy configuration and retries."""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.core.config import get_settings
from app.services.templates import email_templates


class EmailConfig:
    """Centralized email configuration and content limits."""

    MAX_ALERTS_PER_DIGEST = 50
    MAX_SUBJECT_LENGTH = 200
    MAX_PRODUCT_NAME_LENGTH = 150
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5

    def __init__(self) -> None:
        settings = get_settings()
        self.smtp_host = settings.smtp_host or "smtp.resend.com"
        self.smtp_port = settings.smtp_port or 587
        self.smtp_username = settings.smtp_username or "resend"
        self.smtp_password = settings.smtp_password
        self.from_email = settings.from_email or "noreply@pricehawk.local"
        self.from_name = settings.from_name or "PriceHawk Alerts"


class EmailService:
    """Service for sending email digests via Resend SMTP."""

    def __init__(self) -> None:
        self.config = EmailConfig()
        self._validated = False

    def _ensure_configured(self) -> None:
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
        digest_period_hours: int = 24,
    ) -> dict[str, Any]:
        """Send a bounded digest; return success, message and error delivery fields."""
        self._ensure_configured()
        to_email = self._sanitize_email(to_email)
        alerts = alerts[: self.config.MAX_ALERTS_PER_DIGEST]
        if not alerts:
            return {"success": False, "message": "No alerts to send", "error": "Empty alerts list"}
        subject = email_templates.digest_subject(alerts, self.config.MAX_SUBJECT_LENGTH)
        html_body = self._generate_html_email(user_name, alerts, digest_period_hours)
        text_body = self._generate_plain_text_email(user_name, alerts, digest_period_hours)
        return self._send_email_with_retry(to_email, subject, html_body, text_body)

    def send_test_email(self, to_email: str) -> dict[str, Any]:
        """Send a test email to verify email configuration."""
        self._ensure_configured()
        to_email = self._sanitize_email(to_email)
        subject, html_body, text_body = email_templates.test_email()
        return self._send_email_with_retry(to_email, subject, html_body, text_body)

    def _send_email_with_retry(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> dict[str, Any]:
        """Retry both returned delivery failures and raised transport errors."""
        import time

        for attempt in range(self.config.MAX_RETRIES + 1):
            try:
                result = self._send_email_smtp(to_email, subject, html_body, text_body)
                if result["success"]:
                    return result
                if attempt < self.config.MAX_RETRIES:
                    time.sleep(self.config.RETRY_DELAY_SECONDS)
            except Exception as exc:
                if attempt == self.config.MAX_RETRIES:
                    return {
                        "success": False,
                        "message": "Email delivery failed after retries",
                        "error": str(exc),
                    }
                time.sleep(self.config.RETRY_DELAY_SECONDS)
        return {
            "success": False,
            "message": "Email delivery failed",
            "error": "Max retries exceeded",
        }

    def _send_email_smtp(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> dict[str, Any]:
        """Send multipart plain text/HTML over authenticated STARTTLS."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.config.from_name} <{self.config.from_email}>"
            msg["To"] = to_email
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_username, self.config.smtp_password)
                server.send_message(msg)
            return {
                "success": True,
                "message": f"Email sent successfully to {to_email}",
                "error": None,
            }
        except smtplib.SMTPAuthenticationError:
            return {
                "success": False,
                "message": "SMTP authentication failed",
                "error": "Invalid SMTP credentials. Check SMTP_PASSWORD in .env",
            }
        except smtplib.SMTPException as exc:
            return {
                "success": False,
                "message": "SMTP error occurred",
                "error": f"SMTP error: {exc}",
            }
        except Exception as exc:
            return {"success": False, "message": "Failed to send email", "error": str(exc)}

    def _generate_html_email(
        self,
        user_name: str,
        alerts: list[dict[str, Any]],
        digest_period_hours: int,
    ) -> str:
        return email_templates.html_digest(
            user_name,
            alerts,
            digest_period_hours,
            self.config.MAX_PRODUCT_NAME_LENGTH,
        )

    def _generate_plain_text_email(
        self,
        user_name: str,
        alerts: list[dict[str, Any]],
        digest_period_hours: int,
    ) -> str:
        return email_templates.plain_text_digest(
            user_name,
            alerts,
            digest_period_hours,
            self.config.MAX_PRODUCT_NAME_LENGTH,
        )

    def _sanitize_email(self, email: str) -> str:
        email = email.strip().lower()
        if "@" not in email or "." not in email:
            raise ValueError(f"Invalid email address: {email}")
        if "\n" in email or "\r" in email:
            raise ValueError("Email address contains invalid characters")
        return email

    def _sanitize_text(self, text: str, max_length: int = 500) -> str:
        return email_templates.sanitize_text(text, max_length)
