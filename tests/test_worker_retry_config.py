"""Regression tests for worker retry configuration."""

from celery.utils.time import get_exponential_backoff_interval

from app.tasks.scraper_tasks import scrape_single_competitor


def test_scrape_single_competitor_retry_delays_are_deterministic():
    """Retry configuration matches the documented 60s, 120s, 240s progression."""
    assert scrape_single_competitor.max_retries == 3
    assert scrape_single_competitor.retry_backoff == 60
    assert scrape_single_competitor.retry_backoff_max == 240
    assert scrape_single_competitor.retry_jitter is False

    delays = [
        get_exponential_backoff_interval(
            factor=scrape_single_competitor.retry_backoff,
            retries=retries,
            maximum=scrape_single_competitor.retry_backoff_max,
            full_jitter=scrape_single_competitor.retry_jitter,
        )
        for retries in range(scrape_single_competitor.max_retries)
    ]

    assert delays == [60, 120, 240]
