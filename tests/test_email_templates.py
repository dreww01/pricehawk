"""Hermetic coverage of email content and the existing SMTP delivery contract."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import email_service
from app.services.templates import email_templates as templates


@pytest.fixture
def service(monkeypatch):
    settings = SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="sender",
        smtp_password="secret",
        from_email="alerts@example.test",
        from_name="PriceHawk",
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)
    instance = email_service.EmailService()
    # Any accidental real transport or delay fails rather than leaving the sandbox.
    monkeypatch.setattr(
        email_service.smtplib, "SMTP", Mock(side_effect=AssertionError("real SMTP"))
    )
    monkeypatch.setattr("time.sleep", Mock(side_effect=AssertionError("real sleep")))
    return instance


@pytest.fixture
def alert():
    return dict(
        product_name="Widget",
        competitor_name="Store",
        alert_type="price_drop",
        old_price=Decimal("100.00"),
        new_price=Decimal("89.99"),
        price_change_percent=Decimal("-10.01"),
        currency="USD",
    )


def test_digest_renders_drops_increases_and_currency_changes(alert):
    alerts = [
        alert,
        dict(alert, alert_type="price_increase"),
        dict(
            alert,
            alert_type="currency_changed",
            old_currency="USD",
            new_currency="EUR",
        ),
    ]
    html = templates.html_digest("Alex", alerts, 12)
    text = templates.plain_text_digest("Alex", alerts, 12)
    assert "Hi Alex," in html and "Hi Alex," in text
    for body in (html, text):
        assert "3 price alerts from the last 12 hours" in body
        assert "↓ 10.0%" in body and "↑ 10.0%" in body
        assert "USD 100.00" in body and "USD 89.99" in body
        assert "USD → EUR" in body
    assert "#16a34a" in html and "#dc2626" in html and "#d97706" in html
    assert "Review the competitor currency before comparing prices." in html
    assert templates.digest_subject(alerts) == (
        "PriceHawk Alert: 1 price drop &amp; 1 price increase &amp; 1 currency change"
    )


def test_html_escapes_names_and_all_currency_fields(alert):
    unsafe = '<img src=x onerror="bad">&\''
    alerts = [
        dict(alert, product_name=unsafe, competitor_name=unsafe, currency=unsafe),
        dict(
            alert,
            alert_type="currency_changed",
            old_currency=unsafe,
            new_currency=unsafe,
        ),
    ]
    html = templates.html_digest(unsafe, alerts, 24)
    assert unsafe not in html
    assert "<img" not in html
    assert "&lt;img src=x &quot;bad&quot;&gt;&amp;&#x27;" in html
    assert "&amp;lt;" not in html
    assert "onerror=" not in html
    assert html.count("&lt;img") == 7


def test_content_defaults_and_limits():
    html = templates.html_digest("", [{}], 6)
    text = templates.plain_text_digest("", [{}], 6)
    for body in (html, text):
        assert "Hi User," in body
        assert "Unknown Product" in body and "Unknown Competitor" in body
        assert "USD 0.00" in body
        assert "1 price alert from the last 6 hours" in body
    assert templates.sanitize_text("x" * 151, 150) == "x" * 147 + "..."
    assert templates.sanitize_text(" <>&\"' ") == "&lt;&gt;&amp;&quot;&#x27;"
    assert templates.sanitize_text("") == ""
    assert len(templates.digest_subject([{"alert_type": "price_drop"}], 20)) == 20
    for body in (
        templates.html_digest("A" * 101, [{"product_name": "B" * 151}], 24),
        templates.plain_text_digest("A" * 101, [{"product_name": "B" * 151}], 24),
    ):
        assert "A" * 97 + "..." in body and "B" * 147 + "..." in body


def test_digest_limit_applies_to_subject_and_both_bodies(service, alert, monkeypatch):
    send = Mock(return_value={"success": True})
    monkeypatch.setattr(service, "_send_email_with_retry", send)
    alerts = [alert] * 50 + [dict(alert, product_name="OMITTED", alert_type="price_increase")]
    assert service.send_price_alert_digest(" USER@Example.Test ", "Alex", alerts) == {
        "success": True
    }
    recipient, subject, html, text = send.call_args.args
    assert recipient == "user@example.test"
    assert subject == "PriceHawk Alert: 50 price drops"
    assert "50 price alerts" in html and "50 price alerts" in text
    assert "OMITTED" not in html + text
    assert len(alerts) == 51


def test_config_fallbacks_and_tunable_limits(monkeypatch):
    settings = SimpleNamespace(
        **dict.fromkeys(
            [
                "smtp_host",
                "smtp_port",
                "smtp_username",
                "smtp_password",
                "from_email",
                "from_name",
            ]
        )
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)
    config = email_service.EmailConfig()
    assert (config.smtp_host, config.smtp_port, config.smtp_username) == (
        "smtp.resend.com",
        587,
        "resend",
    )
    assert (config.from_email, config.from_name) == ("noreply@pricehawk.local", "PriceHawk Alerts")
    assert config.smtp_password is None
    assert (config.MAX_RETRIES, config.RETRY_DELAY_SECONDS, config.MAX_ALERTS_PER_DIGEST) == (
        2,
        5,
        50,
    )


def test_empty_digest_does_not_send(service, monkeypatch):
    send = Mock()
    monkeypatch.setattr(service, "_send_email_with_retry", send)
    assert service.send_price_alert_digest("u@example.test", "User", []) == {
        "success": False,
        "message": "No alerts to send",
        "error": "Empty alerts list",
    }
    send.assert_not_called()


@pytest.mark.parametrize(
    "recipient",
    ["invalid", "a@localhost", "a@example.test\nBcc: x@example.test", "a@ex\rample.test"],
)
@pytest.mark.parametrize("method", ["send_test_email", "send_price_alert_digest"])
def test_invalid_recipients_raise_before_delivery(service, alert, recipient, method):
    args = (recipient,) if method == "send_test_email" else (recipient, "User", [alert])
    with pytest.raises(ValueError):
        getattr(service, method)(*args)


def test_configuration_validation_is_lazy_and_cached(service):
    service.config.smtp_password = ""
    with pytest.raises(ValueError, match="SMTP_PASSWORD not configured"):
        service.send_test_email("u@example.test")
    service.config.smtp_password = "secret"
    service._ensure_configured()
    service.config.smtp_password = ""
    service._ensure_configured()


def test_test_email_content_and_dispatch(service, monkeypatch):
    clock = Mock()
    clock.now.return_value = datetime(2026, 1, 2, 3, 4, 5)
    monkeypatch.setattr(templates, "datetime", clock)
    send = Mock(return_value={"success": True})
    monkeypatch.setattr(service, "_send_email_with_retry", send)
    assert service.send_test_email(" USER@Example.Test ") == {"success": True}
    recipient, subject, html, text = send.call_args.args
    assert recipient == "user@example.test" and subject == "PriceHawk Test Email"
    for body in (html, text):
        assert "PriceHawk Email Test" in body
        assert "Sent at: 2026-01-02T03:04:05" in body
        assert "your email configuration is working correctly!" in body
