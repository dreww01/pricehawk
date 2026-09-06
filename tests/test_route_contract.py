"""
Route contract and documentation drift prevention tests.
Ensures docs/API.md stays in 100% sync with registered FastAPI OpenAPI routes.
"""

import re
from pathlib import Path
from main import app


def _extract_documented_routes(docs_path: str = "docs/API.md") -> set[tuple[str, str]]:
    """Extract all documented (METHOD, PATH) pairs from docs/API.md."""
    content = Path(docs_path).read_text(encoding="utf-8")
    # Matches markdown inline code like `GET /api/dashboard/stats` or `POST /api/auth/login`
    matches = re.findall(r"`(GET|POST|PUT|DELETE|PATCH)\s+(/api[^\`\s]*)`", content)
    return {(method.upper(), path.strip()) for method, path in matches}


def _extract_registered_api_routes() -> set[tuple[str, str]]:
    """Extract all registered (METHOD, PATH) pairs from the FastAPI OpenAPI schema."""
    schema = app.openapi()
    routes = set()
    for path, methods in schema.get("paths", {}).items():
        if path.startswith("/api"):
            for method in methods:
                if method.lower() in ("get", "post", "put", "delete", "patch"):
                    routes.add((method.upper(), path))
    return routes


def test_route_inventory_no_drift():
    """Verify that every route in docs/API.md is registered, and every /api route in FastAPI is documented."""
    documented_routes = _extract_documented_routes()
    registered_routes = _extract_registered_api_routes()

    missing_in_app = documented_routes - registered_routes
    missing_in_docs = registered_routes - documented_routes

    assert not missing_in_app, (
        f"Documented routes in docs/API.md missing from FastAPI OpenAPI registry ({len(missing_in_app)}): "
        f"{sorted(missing_in_app)}"
    )
    assert not missing_in_docs, (
        f"FastAPI /api routes missing from docs/API.md documentation ({len(missing_in_docs)}): "
        f"{sorted(missing_in_docs)}"
    )


def test_dashboard_and_insights_routes_contract():
    """Ensure dashboard helper endpoints and /api/insights feed are registered and documented."""
    documented = _extract_documented_routes()
    registered = _extract_registered_api_routes()

    critical_routes = [
        ("GET", "/api/dashboard/stats"),
        ("GET", "/api/dashboard/activity"),
        ("GET", "/api/dashboard/products"),
        ("GET", "/api/insights"),
        ("POST", "/api/scraper/scrape/manual/{product_id}"),
        ("GET", "/api/scraper/scrape/stream/{task_id}"),
        ("GET", "/api/scraper/scrape/worker-health"),
    ]

    for method, path in critical_routes:
        assert (method, path) in registered, f"Route {method} {path} not registered in FastAPI OpenAPI schema"
        assert (method, path) in documented, f"Route {method} {path} not documented in docs/API.md"
