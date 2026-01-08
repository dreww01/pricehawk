import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.routes import auth, products, scraper, discovery, insights, alerts, export
from app.core.config import get_settings


settings = get_settings()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PriceHawk API",
    description="""
## Multi-Platform Price Monitoring System

PriceHawk discovers products from competitor stores, tracks prices over time,
and provides automated alerts on price changes.

### Features
- **Multi-platform discovery**: Shopify, WooCommerce, Amazon, eBay, custom sites
- **Automated scraping**: Daily background price collection via Celery
- **AI insights**: Pattern detection and pricing recommendations
- **Email alerts**: Notifications on significant price changes
- **CSV export**: Download price history for analysis

### Authentication
All endpoints (except health check) require a valid Supabase JWT token.
Include the token in the `Authorization` header: `Bearer <token>`
    """,
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={
        "name": "PriceHawk Support",
        "email": "support@pricehawk.app",
    },
    license_info={
        "name": "MIT",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(scraper.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(insights.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(export.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    logger.warning("PriceHawk API started")


@app.get("/", include_in_schema=False)
def root():
    """Redirect root to docs."""
    return RedirectResponse(url="/api/docs")


@app.get("/api/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}
