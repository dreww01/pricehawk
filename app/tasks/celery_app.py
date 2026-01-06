from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "pricehawk",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.scraper_tasks"],
)

celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Task time limits
    task_soft_time_limit=270,  # 4.5 minutes soft limit
    task_time_limit=300,  # 5 minutes hard limit

    # Result expiration (24 hours)
    result_expires=86400,

    # Beat schedule - daily scrape at 2 AM UTC
    beat_schedule={
        "daily-scrape-all-products": {
            "task": "app.tasks.scraper_tasks.scrape_all_products",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)
