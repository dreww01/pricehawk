"""Signed webhook delivery for price alert digests."""

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx


class WebhookDeliveryError(ValueError):
    """Raised when a webhook cannot be delivered safely."""


def is_safe_public_ip(ip_str: str) -> bool:
    """Verify that an IP string is a valid, globally routable public address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        mapped = ip.ipv4_mapped
        if (
            not mapped.is_global
            or mapped.is_private
            or mapped.is_loopback
            or mapped.is_link_local
            or mapped.is_reserved
            or mapped.is_multicast
            or mapped.is_unspecified
        ):
            return False
    return True


class SSRFSafeSyncBackend(httpcore.SyncBackend):
    """Network backend that enforces SSRF safety and prevents DNS rebinding."""

    def __init__(self, allowed_ips: set[str]) -> None:
        super().__init__()
        self.allowed_ips = allowed_ips

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        try:
            addrinfo = socket.getaddrinfo(host, port)
        except socket.gaierror as exc:
            raise WebhookDeliveryError("Webhook hostname could not be resolved") from exc

        resolved_ips = [res[4][0] for res in addrinfo]
        for ip_str in resolved_ips:
            if not is_safe_public_ip(ip_str):
                raise WebhookDeliveryError(
                    f"Webhook URL must not resolve to a private or reserved address: {ip_str}"
                )
            if self.allowed_ips and ip_str not in self.allowed_ips:
                raise WebhookDeliveryError(
                    f"DNS rebinding detected: destination address {ip_str} not in validated addresses"
                )

        target_ip = resolved_ips[0]
        return super().connect_tcp(
            target_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class SafeWebhookTransport(httpx.HTTPTransport):
    """Transport that protects against SSRF and DNS rebinding."""

    def __init__(self, allowed_ips: set[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pool = httpcore.ConnectionPool(
            ssl_context=self._pool._ssl_context,
            network_backend=SSRFSafeSyncBackend(allowed_ips),
            retries=self._pool._retries,
        )


class WebhookService:
    """Deliver deterministic JSON payloads with an HMAC SHA-256 signature."""

    TIMEOUT_SECONDS = 10.0

    def send_digest(
        self,
        webhook_url: str,
        webhook_secret: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_ips = self._validate_url(webhook_url)
        if len(webhook_secret) < 16:
            raise WebhookDeliveryError("Webhook secret must contain at least 16 characters")

        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = str(int(time.time()))
        signed_content = timestamp.encode("ascii") + b"." + body
        signature = hmac.new(
            webhook_secret.encode("utf-8"), signed_content, hashlib.sha256
        ).hexdigest()

        try:
            transport = SafeWebhookTransport(allowed_ips=allowed_ips)
            with httpx.Client(
                transport=transport,
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
        except WebhookDeliveryError:
            raise
        except httpx.HTTPError as exc:
            return {"success": False, "error": str(exc)[:500]}

        return {"success": True, "status_code": response.status_code, "error": None}

    @staticmethod
    def _validate_url(webhook_url: str) -> set[str]:
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

        if not addresses:
            raise WebhookDeliveryError("Webhook hostname could not be resolved")

        for address in addresses:
            if not is_safe_public_ip(address):
                raise WebhookDeliveryError(
                    "Webhook URL must not resolve to a private or reserved address"
                )

        return addresses
