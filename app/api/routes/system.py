"""Public system status endpoint with lazy dependency checks."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.version import APPLICATION_VERSION
from app.middleware.rate_limit import API_RATE_LIMIT, status_limiter
from app.services.system_status import check_cache, check_database

router = APIRouter(prefix="/system", tags=["system"])


class SystemStatusResponse(BaseModel):
    """Operational status contract exposed to API clients."""

    status: Literal["healthy", "degraded"]
    env: Literal["development", "staging", "production", "test"]
    version: str
    database: Literal["connected", "mocked"]
    cache: Literal["connected", "mocked"]


def build_system_status(settings: Settings) -> SystemStatusResponse:
    """Build a status payload without initializing dependencies at import time."""
    database = check_database(settings)
    cache = check_cache(settings)

    return SystemStatusResponse(
        status="healthy" if database.available and cache.available else "degraded",
        env=settings.env,
        version=APPLICATION_VERSION,
        database=database.status,
        cache=cache.status,
    )


@router.get("/status", response_model=SystemStatusResponse)
@status_limiter.limit(API_RATE_LIMIT)
def get_system_status(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> SystemStatusResponse:
    """Report application metadata and dependency connectivity."""
    return build_system_status(settings)
