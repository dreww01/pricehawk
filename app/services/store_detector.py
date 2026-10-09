"""Backward compatibility facade for store platform detection.

All logic has been moved to app.services.scraper.detector.
"""
from app.services.scraper.detector import (
    HANDLER_CLASSES,
    PlatformDetectionResult,
    PlatformDetector,
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
    "PlatformDetector",
    "classify_platform_from_html",
    "classify_platform_from_url",
    "detect_platform",
    "detect_platform_details",
    "detect_platform_from_html",
    "get_handler_for_platform",
]
