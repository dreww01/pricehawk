"""FastAPI application module for deployment imports.

The application implementation currently lives in the repository-level
``main.py`` module. This wrapper exposes the same ``app`` object from the
``app.main`` import path used by Procfile-based deployments.
"""

from main import app

__all__ = ["app"]
