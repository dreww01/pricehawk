"""Data models, enums, and exceptions for scraping operations."""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ScrapeFailureReason(StrEnum):
    """Structured failure categories for price scraping."""
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    LAYOUT_CHANGED = "layout_changed"
    NETWORK_ERROR = "network_error"
    NOT_FOUND = "not_found"
    INVALID_URL = "invalid_url"
    DATABASE_ERROR = "database_error"
    UNKNOWN = "unknown"


class ScrapeException(Exception):
    """Base exception for scraping failures."""
    def __init__(
        self,
        message: str,
        failure_reason: ScrapeFailureReason = ScrapeFailureReason.UNKNOWN,
        retryable: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.failure_reason = failure_reason
        self.retryable = retryable
        self.status_code = status_code


class ScrapeTimeoutError(ScrapeException):
    def __init__(self, message: str = "Site timed out. The server took too long to respond."):
        super().__init__(message, failure_reason=ScrapeFailureReason.TIMEOUT, retryable=True)


class ScrapeNetworkError(ScrapeException):
    def __init__(self, message: str = "Connection error. Unable to connect to the store server."):
        super().__init__(message, failure_reason=ScrapeFailureReason.NETWORK_ERROR, retryable=True)


class ScrapeRateLimitError(ScrapeException):
    def __init__(self, message: str = "Site rate limit exceeded (HTTP 429). The store is temporarily blocking requests."):
        super().__init__(message, failure_reason=ScrapeFailureReason.BLOCKED, retryable=True, status_code=429)


class ScrapeAccessDeniedError(ScrapeException):
    def __init__(self, message: str = "Access blocked. The store denied access or requires verification."):
        super().__init__(message, failure_reason=ScrapeFailureReason.BLOCKED, retryable=False, status_code=403)


class ScrapeNotFoundError(ScrapeException):
    def __init__(self, message: str = "Product page not found (HTTP 404)."):
        super().__init__(message, failure_reason=ScrapeFailureReason.NOT_FOUND, retryable=False, status_code=404)


class ScrapeLayoutError(ScrapeException):
    def __init__(self, message: str = "Page layout changed or unsupported. Price could not be located on the product page."):
        super().__init__(message, failure_reason=ScrapeFailureReason.LAYOUT_CHANGED, retryable=False)


class DatabasePersistenceError(ScrapeException):
    def __init__(self, message: str = "Failed to record price history."):
        super().__init__(message, failure_reason=ScrapeFailureReason.DATABASE_ERROR, retryable=True)


@dataclass
class ScrapeResult:
    price: Decimal | None
    currency: str
    status: str
    error_message: str | None = None
    failure_reason: str | None = None
    retry_count: int = 0

    @property
    def failure_status(self) -> str | None:
        """Alias for failure_reason."""
        return self.failure_reason


@dataclass
class FetchResult:
    html: str | None
    status_code: int | None
    retry_count: int = 0
    failure_reason: str | None = None
    error_message: str | None = None
