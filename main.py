import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from slowapi.errors import RateLimitExceeded

from app.api.routes import auth, tracked_products, scraper, discovery, insights, alerts, export, charts, pages, account
from app.core.config import get_settings
from app.middleware.rate_limit import limiter, rate_limit_exceeded_handler


settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Silence noisy third-party HTTP loggers
for noisy_logger in ("httpx", "httpcore", "hpack"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.perf_counter()
    # Startup: initialize resources here (DB pools, caches, etc.)
    logger.info("PriceHawk API starting...")
    yield
    # Shutdown: cleanup resources here
    elapsed = (time.perf_counter() - t0) * 1000
    logger.info("PriceHawk API shutdown after %.1f ms uptime", elapsed)


app = FastAPI(
    title="PriceHawk API",
    description="Monitor competitor prices, detect changes, and get AI-powered insights",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Static files and templates
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(tracked_products.router, prefix="/api")
app.include_router(scraper.router, prefix="/api/scraper")
app.include_router(discovery.router, prefix="/api")
app.include_router(insights.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(account.router, prefix="/api")

# Page routes (HTML templates)
app.include_router(pages.router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler - logs full details server-side,
    returns generic message to client (OWASP compliant).
    """
    error_id = str(uuid.uuid4())[:8]

    logger.exception(
        f"Unhandled error [{error_id}] {request.method} {request.url.path}: {exc}"
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred. Please try again.",
            "error_id": error_id
        }
    )


@app.get("/", include_in_schema=False)
def root():
    """Redirect root to dashboard or login."""
    return RedirectResponse(url="/dashboard")


@app.get("/api/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Handle 404 errors with HTML page for browser requests."""
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html lang="en" class="dark">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>404 - Not Found | PriceHawk</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <script>
                    tailwind.config = { darkMode: 'class', theme: { extend: { colors: { dark: { bg: '#0f0f1a', card: '#1a1a2e' }, accent: '#f59e0b' }}}}
                </script>
            </head>
            <body class="min-h-screen bg-gray-50 dark:bg-dark-bg flex items-center justify-center p-4">
                <div class="text-center">
                    <h1 class="text-6xl font-bold text-accent mb-4">404</h1>
                    <p class="text-xl text-gray-600 dark:text-gray-400 mb-8">Page not found</p>
                    <a href="/dashboard" class="px-6 py-3 bg-accent hover:bg-amber-600 text-white rounded-lg transition-colors">Go to Dashboard</a>
                </div>
            </body>
            </html>
            """,
            status_code=404
        )
    return JSONResponse(status_code=404, content={"detail": "Not found"})
