import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from app.db.database import get_supabase_client
from app.db.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def check_database_connection() -> tuple[bool, str | None]:
    """
    Verify Supabase database connectivity by executing a lightweight query.

    Returns (True, None) if the database responds successfully,
    or (False, error_message) on connection or query failure.
    """
    try:
        client = get_supabase_client()
        # Query products table with limit 1 as a lightweight connectivity probe
        client.table("products").select("id").limit(1).execute()
        return True, None
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return False, str(exc)


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        status.HTTP_200_OK: {
            "model": HealthResponse,
            "description": "System is healthy and database is connected",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "Service is unhealthy or database is unreachable",
        },
    },
    summary="Health & System Status",
    description="Dedicated lightweight health check endpoint to monitor service availability and database connectivity.",
)
@router.get(
    "/health/",
    response_model=HealthResponse,
    include_in_schema=False,
)
def get_health(response: Response) -> HealthResponse:
    """
    Check system health and database connectivity state.

    Returns HTTP 200 with status 'ok' when database is reachable,
    or HTTP 503 with status 'error' when database connectivity fails.
    """
    is_db_connected, db_error = check_database_connection()
    now = datetime.now(timezone.utc)

    if not is_db_connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="error",
            timestamp=now,
            database="disconnected",
            error=db_error,
        )

    return HealthResponse(
        status="ok",
        timestamp=now,
        database="connected",
    )
