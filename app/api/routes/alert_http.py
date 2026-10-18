"""HTTP error translation shared by alert controllers."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from app.services.alert_errors import AlertOperationError

logger = logging.getLogger(__name__)


@contextmanager
def alert_errors(message: str) -> Iterator[None]:
    """Translate application failures without exposing infrastructure details."""
    try:
        yield
    except AlertOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("%s", message)
        raise HTTPException(status_code=500, detail=message) from exc
