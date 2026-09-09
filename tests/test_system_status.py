"""Three-tier verification for the system status endpoint."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import system
from app.api.routes.system import SystemStatusResponse, build_system_status
from app.core.config import Settings, get_settings
from app.core.version import APPLICATION_VERSION
from app.services.system_status import (
    DependencyStatus,
    check_cache,
    check_database,
)
from main import app


REQUIRED_SETTINGS = {
    "sb_url": "https://example.supabase.co",
    "sb_anon_key": "test-anon-key",
    "sb_service_key": "test-service-key",
    "sb_jwt_secret": "test-jwt-secret",
}


def build_settings(env: str) -> Settings:
    """Build isolated settings without reading external credentials."""
    return Settings(_env_file=None, env=env, **REQUIRED_SETTINGS)


# Tier 1: unit/schema verification.
def test_system_status_payload_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system,
        "check_database",
        lambda settings: DependencyStatus(status="connected", available=True),
    )
    monkeypatch.setattr(
        system,
        "check_cache",
        lambda settings: DependencyStatus(status="mocked", available=True),
    )

    payload = build_system_status(build_settings("development"))

    assert payload.model_dump() == {
        "status": "healthy",
        "env": "development",
        "version": APPLICATION_VERSION,
        "database": "connected",
        "cache": "mocked",
    }


def test_system_status_schema_rejects_invalid_contract_values() -> None:
    with pytest.raises(ValidationError):
        SystemStatusResponse(
            status="unknown",
            env="development",
            version="1.0.0",
            database="offline",
            cache="mocked",
        )


# Tier 2: integration verification through the real FastAPI application.
def test_system_status_endpoint_returns_contract_and_rate_limit_headers(
    client: TestClient,
) -> None:
    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "env": "development",
        "version": APPLICATION_VERSION,
        "database": "mocked",
        "cache": "mocked",
    }
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0
    assert "X-RateLimit-Reset" in response.headers


def test_unavailable_dependencies_degrade_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system,
        "check_database",
        lambda settings: DependencyStatus(status="mocked", available=False),
    )
    monkeypatch.setattr(
        system,
        "check_cache",
        lambda settings: DependencyStatus(status="connected", available=True),
    )

    payload = build_system_status(build_settings("production"))

    assert payload.status == "degraded"
    assert payload.database == "mocked"
    assert payload.cache == "connected"


def test_importing_system_route_has_no_database_side_effects() -> None:
    env = {
        **os.environ,
        "SB_URL": REQUIRED_SETTINGS["sb_url"],
        "SB_ANON_KEY": REQUIRED_SETTINGS["sb_anon_key"],
        "SB_SERVICE_KEY": REQUIRED_SETTINGS["sb_service_key"],
        "SB_JWT_SECRET": REQUIRED_SETTINGS["sb_jwt_secret"],
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import app.api.routes.system; "
                "assert 'app.db.database' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_development_dependency_checks_do_not_create_external_clients() -> None:
    settings = build_settings("development")

    database = check_database(settings)
    cache = check_cache(settings)

    assert database == DependencyStatus(status="mocked", available=True)
    assert cache == DependencyStatus(status="mocked", available=True)


# Tier 3: environment contract verification without external services.
@pytest.mark.parametrize("env", ["development", "production"])
def test_system_status_is_credential_free_across_runtime_environments(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_settings = build_settings(env)
    app.dependency_overrides[get_settings] = lambda: runtime_settings
    monkeypatch.setattr(
        system,
        "check_database",
        lambda settings: DependencyStatus(status="mocked", available=True),
    )
    monkeypatch.setattr(
        system,
        "check_cache",
        lambda settings: DependencyStatus(status="mocked", available=True),
    )

    try:
        with TestClient(app) as client:
            response = client.get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "env": env,
        "version": APPLICATION_VERSION,
        "database": "mocked",
        "cache": "mocked",
    }
