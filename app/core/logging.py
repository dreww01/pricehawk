"""
Structured logging and correlation ID tracking for PriceHawk.

Provides:
- ContextVar-backed correlation ID propagation across async/sync boundaries
- Structured JSON logging formatter for production environments
- Text formatter with correlation ID tags for development
- Celery Task subclass and Celery signal handlers for header propagation
- Starlette / FastAPI middleware for request-scoped correlation IDs
"""

import contextvars
import json
import logging
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-ID"

CORRELATION_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

STANDARD_LOG_RECORD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "taskName",
    "correlation_id",
}


def generate_correlation_id(prefix: str | None = None) -> str:
    """Generate a unique correlation ID (UUID v4), optionally with a prefix."""
    raw_id = str(uuid.uuid4())
    if prefix:
        return f"{prefix.rstrip('-')}-{raw_id}"
    return raw_id


def get_correlation_id() -> str | None:
    """Retrieve the active correlation ID from the current context."""
    return CORRELATION_ID_CTX.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token[str | None]:
    """Set the active correlation ID in the current context."""
    return CORRELATION_ID_CTX.set(correlation_id)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Reset the correlation ID context to a previous token."""
    CORRELATION_ID_CTX.reset(token)


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Generator[str, None, None]:
    """
    Context manager to execute a code block under a specific correlation ID.

    If correlation_id is not provided, a new UUID v4 is generated.
    The context is guaranteed to be restored on exit.
    """
    cid = correlation_id or generate_correlation_id()
    token = set_correlation_id(cid)
    try:
        yield cid
    finally:
        reset_correlation_id(token)


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects correlation_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        cid = getattr(record, "correlation_id", None) or get_correlation_id()
        record.correlation_id = cid
        return True


def _record_factory_with_correlation(old_factory):
    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.correlation_id = get_correlation_id()
        return record

    return factory


_factory_installed = False


def install_log_record_factory() -> None:
    """Install global log record factory to attach correlation_id to every record."""
    global _factory_installed
    if not _factory_installed:
        current_factory = logging.getLogRecordFactory()
        logging.setLogRecordFactory(_record_factory_with_correlation(current_factory))
        _factory_installed = True


class JSONFormatter(logging.Formatter):
    """
    Structured JSON formatter for production application and worker logs.

    Emits one JSON object per line with timestamp, level, logger name,
    message, correlation ID, exception tracebacks, and extra attributes.
    """

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None) or get_correlation_id()

        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": cid,
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_entry["exception"] = record.exc_text

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        # Include custom extra fields attached to the LogRecord
        for key, val in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(val)
                    log_entry[key] = val
                except (TypeError, OverflowError):
                    log_entry[key] = str(val)

        return json.dumps(log_entry)


class TextFormatter(logging.Formatter):
    """Human-readable formatter with correlation ID tags for development logs."""

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None) or get_correlation_id()
        cid_tag = f" [{cid}]" if cid else ""
        asctime = self.formatTime(record, self.datefmt)
        levelname = record.levelname
        name = record.name
        message = record.getMessage()

        base_msg = f"{asctime} | {levelname:<8} | {name}{cid_tag} | {message}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            if not base_msg.endswith("\n"):
                base_msg += "\n"
            base_msg += record.exc_text
        if record.stack_info:
            if not base_msg.endswith("\n"):
                base_msg += "\n"
            base_msg += self.formatStack(record.stack_info)

        return base_msg


def setup_logging(
    log_level: int | str | None = None,
    log_format: str | None = None,
) -> None:
    """
    Configure application and worker root logger.

    - Installs the correlation ID log record factory
    - Selects JSONFormatter in production or when log_format='json'
    - Selects TextFormatter in development/test
    - Silences noisy third-party loggers
    """
    from app.core.config import get_settings

    install_log_record_factory()

    settings = get_settings()

    # Determine log level
    if log_level is None:
        level = logging.DEBUG if settings.debug else logging.INFO
    elif isinstance(log_level, str):
        level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        level = log_level

    # Determine formatter format
    configured_format = log_format or getattr(settings, "log_format", "auto")
    use_json = (
        configured_format == "json"
        or (configured_format == "auto" and settings.is_production)
    )

    formatter: logging.Formatter = JSONFormatter() if use_json else TextFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(CorrelationIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy loggers
    for noisy in ("httpx", "httpcore", "hpack", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Celery Integration: Task Class and Signal Handlers
# ---------------------------------------------------------------------------

try:
    from celery import Task
    from celery.signals import (
        after_setup_logger,
        after_setup_task_logger,
        before_task_publish,
        task_postrun,
        task_prerun,
    )
except ImportError:
    Task = object  # type: ignore[misc,assignment]
    before_task_publish = None  # type: ignore[assignment]
    task_prerun = None  # type: ignore[assignment]
    task_postrun = None  # type: ignore[assignment]
    after_setup_logger = None  # type: ignore[assignment]
    after_setup_task_logger = None  # type: ignore[assignment]


class CorrelatedTask(Task):
    """
    Celery Task subclass that ensures correlation ID propagation in task headers.

    - Propagates existing correlation ID into Celery task headers during apply_async/delay
    - Generates a new correlation ID if none exists in context
    - Wraps direct __call__ invocations in a correlation context for hermetic test execution
    """

    def apply_async(
        self,
        args=None,
        kwargs=None,
        task_id=None,
        producer=None,
        link=None,
        link_error=None,
        shadow=None,
        **options,
    ):
        headers = dict(options.get("headers") or {})
        cid = headers.get("correlation_id") or get_correlation_id()
        if not cid:
            cid = generate_correlation_id()
        headers["correlation_id"] = cid
        options["headers"] = headers
        return super().apply_async(
            args=args,
            kwargs=kwargs,
            task_id=task_id,
            producer=producer,
            link=link,
            link_error=link_error,
            shadow=shadow,
            **options,
        )

    def __call__(self, *args, **kwargs):
        req = getattr(self, "request", None)
        req_headers = getattr(req, "headers", None) or {}
        cid = (
            get_correlation_id()
            or req_headers.get("correlation_id")
            or getattr(req, "correlation_id", None)
            or generate_correlation_id()
        )
        with correlation_context(cid):
            if req is not None:
                req.correlation_id = cid
                if not getattr(req, "headers", None):
                    req.headers = {}
                req.headers["correlation_id"] = cid
            return super().__call__(*args, **kwargs)


_signals_registered = False


def _on_before_task_publish(headers=None, **kwargs):
    if headers is not None:
        cid = headers.get("correlation_id") or get_correlation_id()
        if not cid:
            cid = generate_correlation_id()
        headers["correlation_id"] = cid


def _on_task_prerun(task_id=None, task=None, *args, **kwargs):
    req = getattr(task, "request", None)
    req_headers = getattr(req, "headers", None) or {}
    cid = (
        req_headers.get("correlation_id")
        or getattr(req, "correlation_id", None)
        or get_correlation_id()
        or generate_correlation_id()
    )
    set_correlation_id(cid)
    if req is not None:
        req.correlation_id = cid


def _on_task_postrun(task_id=None, task=None, *args, **kwargs):
    set_correlation_id(None)


def _configure_celery_logger(logger=None, **kwargs):
    if logger is None:
        return
    from app.core.config import get_settings

    settings = get_settings()
    configured_format = getattr(settings, "log_format", "auto")
    use_json = (
        configured_format == "json"
        or (configured_format == "auto" and settings.is_production)
    )
    formatter = JSONFormatter() if use_json else TextFormatter()

    logger_handlers = getattr(logger, "handlers", None)
    if logger_handlers:
        for h in logger_handlers:
            h.setFormatter(formatter)
            h.addFilter(CorrelationIdFilter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(CorrelationIdFilter())
        logger.addHandler(handler)


def register_celery_signals(celery_app=None) -> None:
    """Connect Celery signal handlers for correlation ID and logger configuration."""
    global _signals_registered
    if _signals_registered or before_task_publish is None:
        return

    before_task_publish.connect(_on_before_task_publish, weak=False)
    task_prerun.connect(_on_task_prerun, weak=False)
    task_postrun.connect(_on_task_postrun, weak=False)

    if after_setup_logger:
        after_setup_logger.connect(_configure_celery_logger, weak=False)
    if after_setup_task_logger:
        after_setup_task_logger.connect(_configure_celery_logger, weak=False)

    _signals_registered = True


# ---------------------------------------------------------------------------
# FastAPI / Starlette Middleware
# ---------------------------------------------------------------------------

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Extract or generate a correlation ID for incoming HTTP requests.

    - Reads X-Correlation-ID or X-Request-ID headers if present
    - Generates a new UUID v4 if absent
    - Sets correlation ID in contextvar and request.state.correlation_id
    - Appends X-Correlation-ID header to response
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        header_cid = (
            request.headers.get(CORRELATION_ID_HEADER)
            or request.headers.get(REQUEST_ID_HEADER)
        )
        if header_cid:
            # Sanitize: strip whitespace and limit length
            clean_cid = header_cid.strip()[:128]
            cid = clean_cid if clean_cid else generate_correlation_id()
        else:
            cid = generate_correlation_id()

        token = set_correlation_id(cid)
        request.state.correlation_id = cid

        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = cid
            return response
        finally:
            reset_correlation_id(token)
