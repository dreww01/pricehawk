"""Hermetic tests for application configuration."""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_CORS_ORIGINS, Settings


REQUIRED_SETTINGS = {
    "sb_url": "https://example.supabase.co",
    "sb_anon_key": "anon-key",
    "sb_service_key": "service-key",
    "sb_jwt_secret": "jwt-secret",
}


def build_settings(**overrides: object) -> Settings:
    """Build settings without reading a developer's local .env file."""
    return Settings(_env_file=None, **REQUIRED_SETTINGS, **overrides)


def test_default_settings_initialization() -> None:
    settings = build_settings()

    assert settings.env == "development"
    assert settings.cors_origins == DEFAULT_CORS_ORIGINS
    assert settings.default_rate_limit == "60/minute"
    assert settings.is_production is False


def test_cors_origins_parse_from_comma_separated_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        " https://dashboard.example.com,https://admin.example.com , ",
    )

    settings = build_settings()

    assert settings.cors_origins == [
        "https://dashboard.example.com",
        "https://admin.example.com",
    ]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("development", False),
        ("staging", False),
        ("production", True),
        ("PRODUCTION", True),
        ("test", False),
    ],
)
def test_is_production_across_supported_environments(env: str, expected: bool) -> None:
    settings = build_settings(env=env)

    assert settings.is_production is expected


def test_jwt_allowed_algorithms_parse_from_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_ALLOWED_ALGORITHMS", "ES256, HS256")
    settings = build_settings()
    assert settings.jwt_allowed_algorithms == ["ES256", "HS256"]


def test_rejects_unsupported_environment() -> None:
    with pytest.raises(ValidationError):
        build_settings(env="preview")
