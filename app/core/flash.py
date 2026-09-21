"""
Native, cryptographically signed in-app flash notification system for PriceHawk.

Uses standard signed session cookies backed by `itsdangerous.URLSafeTimedSerializer`
to ensure flash messages are tamper-proof, display exactly once, and clear automatically
on subsequent navigation.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Sequence

from fastapi import Request, Response
from itsdangerous import BadData, BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

FLASH_COOKIE_NAME = "flash_messages"
FLASH_COOKIE_SALT = "pricehawk-flash-messages"
FLASH_COOKIE_MAX_AGE = 300  # 5 minutes


class FlashCategory(str, Enum):
    """Standard flash notification categories."""
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"


CATEGORY_ALIASES: dict[str, str] = {
    "danger": "error",
    "err": "error",
    "warn": "warning",
    "alert": "warning",
    "notice": "info",
}


def normalize_category(category: str | FlashCategory | None) -> str:
    """Normalize message category to one of: success, warning, error, info."""
    if not category:
        return FlashCategory.INFO.value
    cat_str = str(category).lower().strip()
    if cat_str in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[cat_str]
    valid_values = {c.value for c in FlashCategory}
    if cat_str in valid_values:
        return cat_str
    return FlashCategory.INFO.value


class FlashMessage(dict):
    """
    Dictionary-compatible flash message item providing both dict and attribute access
    for Jinja2 templates (e.g. message.type, message.text, message.category, message.message).
    """

    def __init__(self, text: str, category: str | FlashCategory = FlashCategory.INFO, **kwargs: Any):
        cat = normalize_category(category)
        super().__init__(
            type=cat,
            text=str(text),
            category=cat,
            message=str(text),
            **kwargs,
        )

    @property
    def type(self) -> str:
        return self.get("type", "info")

    @property
    def text(self) -> str:
        return self.get("text", "")

    @property
    def category(self) -> str:
        return self.get("type", "info")

    @property
    def message(self) -> str:
        return self.get("text", "")

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}


def get_flash_secret_key(settings: Settings | None = None) -> str:
    """Resolve the secret key used for signing flash message cookies."""
    cfg = settings or get_settings()
    secret = getattr(cfg, "flash_secret_key", None) or getattr(cfg, "sb_jwt_secret", None)
    if not secret:
        secret = "pricehawk-default-flash-secret-key"
    return secret


def get_flash_serializer(
    secret_key: str | None = None,
    settings: Settings | None = None,
) -> URLSafeTimedSerializer:
    """Instantiate URLSafeTimedSerializer with the configured secret key and salt."""
    key = secret_key or get_flash_secret_key(settings)
    return URLSafeTimedSerializer(secret_key=key, salt=FLASH_COOKIE_SALT)


def encode_flash_messages(
    messages: Sequence[dict[str, Any] | FlashMessage],
    secret_key: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Cryptographically sign and URL-safe encode a list of flash messages."""
    serializer = get_flash_serializer(secret_key=secret_key, settings=settings)
    payload = [
        {"type": normalize_category(m.get("type") or m.get("category")), "text": str(m.get("text") or m.get("message", ""))}
        for m in messages
    ]
    return serializer.dumps(payload)


def decode_flash_messages(
    token: str,
    secret_key: str | None = None,
    max_age: int = FLASH_COOKIE_MAX_AGE,
    settings: Settings | None = None,
) -> list[FlashMessage]:
    """
    Cryptographically verify and decode a flash cookie token.
    Raises BadSignature or SignatureExpired if invalid or expired.
    """
    serializer = get_flash_serializer(secret_key=secret_key, settings=settings)
    data = serializer.loads(token, max_age=max_age)
    if not isinstance(data, list):
        raise BadData("Flash payload must be a list")
    return [
        FlashMessage(text=item.get("text", ""), category=item.get("type", "info"))
        for item in data
        if isinstance(item, dict)
    ]


def safe_decode_flash_messages(
    token: str,
    secret_key: str | None = None,
    max_age: int = FLASH_COOKIE_MAX_AGE,
    settings: Settings | None = None,
) -> list[FlashMessage]:
    """
    Safely verify and decode flash token, returning an empty list and logging
    a warning if tampered, corrupted, or expired.
    """
    if not token:
        return []
    try:
        return decode_flash_messages(token, secret_key=secret_key, max_age=max_age, settings=settings)
    except SignatureExpired as e:
        logger.debug(f"Flash message token expired: {e}")
        return []
    except BadSignature as e:
        logger.warning(f"Flash message token failed cryptographic verification (tampered): {e}")
        return []
    except Exception as e:
        logger.warning(f"Failed to decode flash message token: {e}")
        return []


def get_flash_cookie_options(settings: Settings | None = None) -> dict[str, Any]:
    """
    Return standard cookie attributes for the signed flash cookie.
    Matches project security policy: HTTPS in production, permits HTTP in tests/development.
    """
    cfg = settings or get_settings()
    cookie_name = getattr(cfg, "flash_cookie_name", None) or FLASH_COOKIE_NAME
    max_age = getattr(cfg, "flash_cookie_max_age", None) or FLASH_COOKIE_MAX_AGE
    return {
        "key": cookie_name,
        "path": "/",
        "samesite": "lax",
        "secure": cfg.is_production,
        "httponly": True,
        "max_age": max_age,
    }


def set_flash_cookie(
    response: Response,
    messages: Sequence[dict[str, Any] | FlashMessage] | dict[str, Any] | FlashMessage,
    settings: Settings | None = None,
    secret_key: str | None = None,
) -> None:
    """Set the signed flash cookie on an outgoing response, replacing any prior flash cookie."""
    if isinstance(messages, dict):
        msg_list = [messages]
    else:
        msg_list = list(messages)

    if not msg_list:
        return

    token = encode_flash_messages(msg_list, secret_key=secret_key, settings=settings)
    opts = get_flash_cookie_options(settings)
    key = opts["key"]

    # Remove any existing Set-Cookie for this key from response.raw_headers
    if hasattr(response, "raw_headers"):
        key_prefix = f"{key}=".encode("latin-1")
        response.raw_headers[:] = [
            (k, v) for k, v in response.raw_headers
            if not (k.lower() == b"set-cookie" and v.startswith(key_prefix))
        ]
        if hasattr(response, "_headers"):
            delattr(response, "_headers")

    response.set_cookie(
        key=key,
        value=token,
        path=opts["path"],
        samesite=opts["samesite"],
        secure=opts["secure"],
        httponly=opts["httponly"],
        max_age=opts["max_age"],
    )


def clear_flash_cookie(
    response: Response,
    settings: Settings | None = None,
) -> None:
    """Delete the signed flash cookie from the response."""
    opts = get_flash_cookie_options(settings)
    key = opts["key"]

    # Check if delete header already present to prevent duplicates
    if hasattr(response, "headers"):
        existing = (
            response.headers.getlist("set-cookie")
            if hasattr(response.headers, "getlist")
            else response.headers.get_list("set-cookie")
            if hasattr(response.headers, "get_list")
            else []
        )
        for c in existing:
            if c.startswith(f'{key}=""') or c.startswith(f"{key}=;"):
                return

    response.delete_cookie(
        key=key,
        path=opts["path"],
        samesite=opts["samesite"],
        secure=opts["secure"],
        httponly=opts["httponly"],
    )


def flash(
    target: Request | Response,
    message: str,
    category: str | FlashCategory = FlashCategory.INFO,
    *,
    type: str | FlashCategory | None = None,
) -> None:
    """
    Queue a flash message on a Request or Response.

    - When target is Request: queues message in request.state._queued_flashes,
      which FlashMiddleware attaches to the outgoing response.
    - When target is Response: signs and sets the flash cookie directly on the response.
    """
    cat = normalize_category(type or category)
    item = FlashMessage(text=message, category=cat)

    if isinstance(target, Request):
        if not hasattr(target.state, "_queued_flashes"):
            target.state._queued_flashes = []
        target.state._queued_flashes.append(item)
    elif isinstance(target, Response):
        set_flash_cookie(target, [item])
    else:
        raise TypeError(f"flash target must be a Request or Response instance, got {type(target)}")


def get_flashes(request: Request, clear: bool = False) -> list[FlashMessage]:
    """
    Retrieve and verify flash messages from the request cookie.
    Caches the decoded list on request.state._loaded_flashes.
    """
    if hasattr(request.state, "_loaded_flashes") and request.state._loaded_flashes is not None:
        messages = list(request.state._loaded_flashes)
    else:
        opts = get_flash_cookie_options()
        cookie_name = opts["key"]
        token = request.cookies.get(cookie_name)
        messages = safe_decode_flash_messages(token)
        request.state._loaded_flashes = list(messages)

    if clear:
        request.state._flashes_consumed = True

    return messages


def pop_flashes(request: Request) -> list[FlashMessage]:
    """
    Retrieve flash messages from request and mark them as consumed so they are
    cleared from the client cookie on response.
    """
    messages = get_flashes(request, clear=True)
    request.state._loaded_flashes = []
    return messages


class FlashMiddleware(BaseHTTPMiddleware):
    """
    Middleware that manages the lifecycle of flash messages:
    - Automatically attaches any queued flash messages from `request.state._queued_flashes`
      as signed session cookies to outgoing responses.
    - Clears the flash cookie when messages were consumed on HTML page renders.
    """

    async def dispatch(self, request: Request, call_next):
        if not hasattr(request.state, "_queued_flashes"):
            request.state._queued_flashes = []
        if not hasattr(request.state, "_flashes_consumed"):
            request.state._flashes_consumed = False
        if not hasattr(request.state, "_loaded_flashes"):
            request.state._loaded_flashes = None

        response = await call_next(request)

        queued = getattr(request.state, "_queued_flashes", [])
        if queued:
            set_flash_cookie(response, queued)
        elif getattr(request.state, "_flashes_consumed", False):
            clear_flash_cookie(response)

        return response
