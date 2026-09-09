"""Runtime dependency checks used by the system status API."""

import logging
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings

logger = logging.getLogger(__name__)

ConnectionStatus = Literal["connected", "mocked"]


@dataclass(frozen=True)
class DependencyStatus:
    """Public status plus whether the underlying dependency is available."""

    status: ConnectionStatus
    available: bool


def check_database(settings: Settings) -> DependencyStatus:
    """Check Supabase connectivity, using a hermetic mock outside production."""
    if not settings.is_production:
        return DependencyStatus(status="mocked", available=True)

    try:
        # Import lazily so importing the route cannot initialize a database client.
        from app.db.database import get_supabase_client

        client = get_supabase_client()
        client.table("products").select("id").limit(1).execute()
    except Exception:
        logger.warning("Database connectivity check failed", exc_info=True)
        return DependencyStatus(status="mocked", available=False)

    return DependencyStatus(status="connected", available=True)


def check_cache(settings: Settings) -> DependencyStatus:
    """Check Redis connectivity, using a hermetic mock outside production."""
    if not settings.is_production:
        return DependencyStatus(status="mocked", available=True)

    client = None
    try:
        from redis import Redis

        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
    except Exception:
        logger.warning("Cache connectivity check failed", exc_info=True)
        return DependencyStatus(status="mocked", available=False)
    finally:
        if client is not None:
            client.close()

    return DependencyStatus(status="connected", available=True)
