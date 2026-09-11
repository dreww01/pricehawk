from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode


DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://localhost:8000"]
DEFAULT_JWT_ALLOWED_ALGORITHMS = ["ES256", "HS256"]


class Settings(BaseSettings):
    # Supabase
    sb_url: str
    sb_anon_key: str
    sb_service_key: str
    sb_jwt_secret: str

    # App
    debug: bool = False
    env: Literal["development", "staging", "production", "test"] = "development"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: DEFAULT_CORS_ORIGINS.copy()
    )
    jwt_allowed_algorithms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: DEFAULT_JWT_ALLOWED_ALGORITHMS.copy()
    )
    default_rate_limit: str = "60/minute"

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("jwt_allowed_algorithms", mode="before")
    @classmethod
    def parse_jwt_allowed_algorithms(cls, value: object) -> object:
        if isinstance(value, str):
            return [alg.strip() for alg in value.split(",") if alg.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    # Redis/Celery
    redis_url: str = "redis://localhost:6379/0"

    # AI (Groq API)
    groq_api_key: str | None = None

    # Store Discovery
    max_products_fetch: int = 500  # Max products to fetch from API-based stores (Shopify, WooCommerce)

    # SMTP / Email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    from_email: str | None = None
    from_name: str = "PriceHawk Alerts"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
