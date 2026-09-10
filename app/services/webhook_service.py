"""Signed webhook delivery for price alert digests."""

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx


class WebhookDeliveryError(ValueError):
    """Raised when a webhook cannot be delivered safely."""


class WebhookService:
    """Deliver deterministic JSON payloads with an HMAC SHA-256 signature."""

    TIMEOUT_SECONDS = 10.0

    def send_digest(
        self,
        webhook_url: str,
        webhook_secret: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_url(webhook_url)
        if len(webhook_secret) < 16:
            raise WebhookDeliveryError("Webhook secret must contain at least 16 characters")

        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = str(int(time.time()))
        signed_content = timestamp.encode("ascii") + b"." + body
        signature = hmac.new(
            webhook_secret.encode("utf-8"), signed_content, hashlib.sha256
        ).hexdigest()

        try:
            with httpx.Client(
                timeout=self.TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    webhook_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "PriceHawk-Webhooks/1.0",
                        "X-PriceHawk-Timestamp": timestamp,
                        "X-PriceHawk-Signature": f"sha256={signature}",
                    },
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"success": False, "error": str(exc)[:500]}

        return {"success": True, "status_code": response.status_code, "error": None}

    @staticmethod
    def _validate_url(webhook_url: str) -> None:
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WebhookDeliveryError("Webhook URL must be an absolute HTTPS URL")
        if parsed.username or parsed.password:
            raise WebhookDeliveryError("Webhook URL must not contain credentials")

        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            }
        except socket.gaierror as exc:
            raise WebhookDeliveryError("Webhook hostname could not be resolved") from exc

        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise WebhookDeliveryError(
                    "Webhook URL must not resolve to a private or reserved address"
                )
