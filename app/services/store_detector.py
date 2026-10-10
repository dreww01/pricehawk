"""Backward-compatible re-exports for store_detector."""
from app.services.scraper.detector import (
    HANDLER_CLASSES,
    PlatformDetectionResult,
    classify_platform_from_html,
    classify_platform_from_url,
    detect_platform,
    detect_platform_details,
    detect_platform_from_html,
    get_handler_for_platform,
)

__all__ = [
    "HANDLER_CLASSES",
    "PlatformDetectionResult",
    "classify_platform_from_html",
    "classify_platform_from_url",
    "detect_platform",
    "detect_platform_details",
    "detect_platform_from_html",
    "get_handler_for_platform",
]
