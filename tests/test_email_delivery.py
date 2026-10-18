"""Hermetic SMTP lifecycle, error, and retry coverage."""

import smtplib
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from app.services import email_service


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
    monkeypatch.setattr(
        email_service.smtplib, "SMTP", Mock(side_effect=AssertionError("real SMTP"))
    )
    monkeypatch.setattr("time.sleep", Mock(side_effect=AssertionError("real sleep")))
    return instance


def test_successful_delivery_does_not_retry_or_sleep(service, monkeypatch):
    result = {"success": True, "message": "sent", "error": None}
    send = Mock(return_value=result)
    monkeypatch.setattr(service, "_send_email_smtp", send)
    assert service._send_email_with_retry("u@example.test", "Subject", "html", "text") == result
    send.assert_called_once_with("u@example.test", "Subject", "html", "text")


def test_smtp_multipart_and_starttls_lifecycle(service, monkeypatch):
    smtp = Mock()
    server = smtp.return_value.__enter__ = Mock(return_value=Mock())
    smtp.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp)
    result = service._send_email_smtp("u@example.test", "Subject", "<p>€</p>", "Plain €")
    assert result == {
        "success": True,
        "message": "Email sent successfully to u@example.test",
        "error": None,
    }
    smtp.assert_called_once_with("smtp.example.test", 587)
    connection = server.return_value
    assert connection.mock_calls[:2] == [call.starttls(), call.login("sender", "secret")]
    message = connection.send_message.call_args.args[0]
    assert message["Subject"] == "Subject" and message["To"] == "u@example.test"
    assert message["From"] == "PriceHawk <alerts@example.test>"
    assert message.get_content_type() == "multipart/alternative"
    parts = message.get_payload()
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert [part.get_payload(decode=True).decode("utf-8") for part in parts] == [
        "Plain €",
        "<p>€</p>",
    ]
    smtp.return_value.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize(
    "error,message,detail",
    [
        (
            smtplib.SMTPAuthenticationError(535, b"denied"),
            "SMTP authentication failed",
            "Invalid SMTP credentials. Check SMTP_PASSWORD in .env",
        ),
        (smtplib.SMTPException("unavailable"), "SMTP error occurred", "SMTP error: unavailable"),
        (OSError("offline"), "Failed to send email", "offline"),
    ],
)
def test_smtp_errors_preserve_result_contract(service, monkeypatch, error, message, detail):
    monkeypatch.setattr(email_service.smtplib, "SMTP", Mock(side_effect=error))
    assert service._send_email_smtp("u@example.test", "Subject", "html", "text") == {
        "success": False,
        "message": message,
        "error": detail,
    }


@pytest.mark.parametrize("raised", [False, True])
@pytest.mark.parametrize("recover", [False, True])
def test_retries_returned_failures_and_exceptions(service, monkeypatch, raised, recover):
    failure = RuntimeError("offline") if raised else {"success": False}
    success = {"success": True, "message": "delivered", "error": None}
    send = Mock(side_effect=[failure, success] if recover else [failure] * 3)
    sleep = Mock()
    monkeypatch.setattr(service, "_send_email_smtp", send)
    monkeypatch.setattr("time.sleep", sleep)
    result = service._send_email_with_retry("u@example.test", "Subject", "html", "text")
    if recover:
        assert result == success
    else:
        assert result == {
            "success": False,
            "message": "Email delivery failed after retries" if raised else "Email delivery failed",
            "error": "offline" if raised else "Max retries exceeded",
        }
    attempts = 2 if recover else 3
    assert send.call_args_list == [call("u@example.test", "Subject", "html", "text")] * attempts
    assert sleep.call_args_list == [call(5)] * (attempts - 1)
