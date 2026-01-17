from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Supabase
    sb_url: str
    sb_anon_key: str
    sb_service_key: str
    sb_jwt_secret: str

    # App
    debug: bool = False

    # Proxy (Webshare API key for dynamic proxy list)
    webshare_api_key: str | None = None

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
