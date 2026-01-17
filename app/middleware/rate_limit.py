"""
Rate limiting middleware using slowapi.

Limits:
- Auth endpoints: 5/minute (login, signup, password reset)
- Scraping: 10/minute
- General API: 100/minute
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse


def get_client_ip(request: Request) -> str:
    """
    Get client IP from request, checking X-Forwarded-For for proxied requests.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_client_ip,
    default_limits=["100/minute"],
    storage_uri="memory://",
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Custom handler for rate limit exceeded."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please try again later.",
            "retry_after": exc.detail
        }
    )


class RateLimitMiddleware(SlowAPIMiddleware):
    """Rate limiting middleware wrapper."""
    pass


AUTH_RATE_LIMIT = "5/minute"
SCRAPE_RATE_LIMIT = "10/minute"
API_RATE_LIMIT = "100/minute"
