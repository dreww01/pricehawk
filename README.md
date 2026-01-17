# PriceHawk

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Multi-platform price monitoring system that discovers products from competitor stores, tracks prices over time, and provides AI-powered insights with automated alerts.

```
┌─────────────────────────────────────────────────────────────┐
│                        PriceHawk                            │
├─────────────────────────────────────────────────────────────┤
│  Discover  →  Track  →  Analyze  →  Alert                   │
│                                                             │
│  Shopify     │  Daily     │  AI        │  Email             │
│  WooCommerce │  Scraping  │  Insights  │  Digests           │
│  Custom      │  via       │  via       │  (6/12/24h)        │
│              │  Celery    │  Groq      │                    │
└─────────────────────────────────────────────────────────────┘
```

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Platform Discovery** | Auto-detect and scrape Shopify, WooCommerce, and custom stores |
| **Automated Tracking** | Daily background price collection via Celery + Redis |
| **AI Insights** | Pattern detection and pricing recommendations (Groq Llama 3.3 70B) |
| **Smart Alerts** | Digest-based email notifications (configurable 6/12/24h frequency) |
| **CSV Export** | Download price history for external analysis |
| **Rate Limiting** | Protection against abuse with slowapi |
| **Security** | Row-Level Security (RLS), JWT auth, security headers, input validation |

## Tech Stack

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI (async) |
| Database | Supabase (PostgreSQL + Auth) |
| Task Queue | Celery + Redis |
| Scraping | httpx, BeautifulSoup, Playwright |
| AI | Groq API |
| Rate Limiting | slowapi |
| Validation | Pydantic |
| Frontend | Jinja2, HTMX, Tailwind CSS |

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker (for Redis)
- Supabase account
- Groq API key (optional, for AI insights)

### 1. Clone & Install

```bash
git clone https://github.com/dreww01/pricehawk.git
cd pricehawk
uv sync
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Required - Supabase
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-key

# Required - Redis
REDIS_URL=redis://localhost:6379/0

# Optional - AI Insights
GROQ_API_KEY=your-groq-api-key

# Optional - Email Alerts
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=your-resend-api-key
FROM_EMAIL=alerts@yourdomain.com
```

### 3. Setup Database

1. Create a new [Supabase](https://supabase.com) project
2. Go to SQL Editor
3. Copy contents of `docs/database_schema.sql` and run it
4. Verify tables created: `products`, `competitors`, `price_history`, `insights`

### 4. Install Playwright (for JS-rendered sites)

```bash
playwright install chromium
```

### 5. Start Services

**Terminal 1 - Redis:**
```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

**Terminal 2 - API Server:**
```bash
uv run python run.py
```

**Terminal 3 - Celery Worker (required for manual scrapes):**
```bash
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

**Terminal 4 - Celery Beat (scheduler for daily scrapes):**
```bash
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

> **Note:** Redis + Celery Worker are required for the "Get Current Prices" button to work. The manual scrape runs as a background task with real-time progress via Server-Sent Events (SSE).

### 6. Verify Setup

```bash
curl http://localhost:8000/api/health
# {"status":"healthy"}
```

API Documentation: http://localhost:8000/api/docs

## Docker Deployment

```bash
docker compose up -d
docker compose logs -f
```

## API Reference

### Authentication

All endpoints (except `/api/health`) require JWT token:

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" http://localhost:8000/api/products
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| **Auth** |
| `POST` | `/api/auth/login` | Login with email/password |
| `POST` | `/api/auth/signup` | Create new account |
| `POST` | `/api/auth/forgot-password` | Send password reset email |
| `POST` | `/api/auth/reset-password` | Reset password with token |
| `GET` | `/api/auth/me` | Get current user info |
| **Account** |
| `GET` | `/api/account/settings` | Get account settings |
| `POST` | `/api/account/change-password` | Change password |
| `POST` | `/api/account/change-email` | Request email change |
| `DELETE` | `/api/account/delete` | Delete account |
| **Discovery** |
| `POST` | `/api/stores/discover` | Discover products from store URL |
| `POST` | `/api/stores/track` | Add discovered products to tracking |
| **Products** |
| `GET` | `/api/products` | List tracked products |
| `GET` | `/api/products/{id}` | Get product details with price history |
| `PUT` | `/api/products/{id}` | Update product |
| `DELETE` | `/api/products/{id}` | Delete product (soft delete) |
| **Scraping** |
| `POST` | `/api/scraper/scrape/manual/{product_id}` | Queue manual scrape (returns task_id) |
| `GET` | `/api/scraper/scrape/stream/{task_id}` | SSE stream for scrape progress |
| `GET` | `/api/scraper/scrape/worker-health` | Check Celery worker status |
| **Insights** |
| `GET` | `/api/insights/{product_id}` | Get AI insights for product |
| `POST` | `/api/insights/generate/{product_id}` | Generate new insights |
| **Alerts** |
| `GET` | `/api/alerts/settings` | Get notification settings |
| `PUT` | `/api/alerts/settings` | Update notification settings |
| `GET` | `/api/alerts/history` | Get sent alert history |
| `POST` | `/api/alerts/test` | Send test email |
| **Export** |
| `GET` | `/api/export/{product_id}/csv` | Export price history as CSV |

### Example: Discover & Track Products

```bash
# 1. Discover products from a Shopify store
curl -X POST http://localhost:8000/api/stores/discover \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example-store.myshopify.com", "keyword": "laptop", "limit": 20}'

# 2. Track selected products
curl -X POST http://localhost:8000/api/stores/track \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "group_name": "Competitor Laptops",
    "product_urls": ["https://example-store.myshopify.com/products/laptop-pro"],
    "alert_threshold_percent": 10
  }'

# 3. Trigger manual scrape (returns task_id, scraping happens in background)
curl -X POST http://localhost:8000/api/scraper/scrape/manual/{product_id} \
  -H "Authorization: Bearer $TOKEN"
# Response: {"task_id": "abc123", "status": "queued", "message": "Scraping 5 competitors"}

# 4. Stream progress via SSE (optional - frontend uses EventSource)
curl -N http://localhost:8000/api/scraper/scrape/stream/{task_id}
# Streams: data: {"status": "scraping", "completed": 2, "total": 5, "current": "amazon.com"}

# 5. Get AI insights
curl http://localhost:8000/api/insights/{product_id} \
  -H "Authorization: Bearer $TOKEN"
```

## Project Structure

```
pricehawk/
├── app/
│   ├── api/routes/          # API endpoints
│   │   ├── auth.py          # Login, signup, password reset
│   │   ├── account.py       # Account management
│   │   ├── discovery.py     # Store discovery & tracking
│   │   ├── tracked_products.py  # Product CRUD
│   │   ├── scraper.py       # Manual scrape triggers
│   │   ├── insights.py      # AI insights
│   │   ├── alerts.py        # Alert settings & history
│   │   ├── export.py        # CSV export
│   │   └── pages.py         # HTML page routes
│   ├── core/
│   │   ├── config.py        # Settings (Pydantic)
│   │   └── security.py      # JWT verification
│   ├── db/
│   │   ├── database.py      # Supabase client
│   │   └── models.py        # Pydantic models
│   ├── middleware/
│   │   └── rate_limit.py    # Rate limiting config
│   ├── services/
│   │   ├── store_discovery.py   # Discovery orchestrator
│   │   ├── store_detector.py    # Platform detection
│   │   ├── scraper_service.py   # Price extraction
│   │   ├── ai_service.py        # Groq integration
│   │   ├── alert_service.py     # Alert logic
│   │   ├── email_service.py     # Email sending
│   │   └── stores/              # Platform handlers
│   │       ├── base.py          # Abstract base
│   │       ├── shopify.py
│   │       ├── woocommerce.py
│   │       └── generic.py
│   ├── tasks/
│   │   ├── celery_app.py        # Celery config + Beat schedule
│   │   └── scraper_tasks.py     # Background tasks
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS, JS assets
├── tests/                       # Test suite
├── docs/                        # Documentation
│   ├── architecture.md          # System design docs
│   ├── database_schema.sql      # DB setup
│   ├── logic_used.md            # Complex logic explanations
│   └── prd.md                   # Product requirements
├── main.py                      # FastAPI app
├── run.py                       # Dev server
├── test.http                    # VS Code REST Client tests
├── Dockerfile                   # Docker config
└── docker-compose.yml           # Multi-service orchestration
```

## Testing

```bash
# Run all tests
uv run pytest

# With coverage
uv run pytest --cov=app --cov-report=html

# Specific test file
uv run pytest tests/test_auth.py -v
```

### Manual API Testing

Use `test.http` with VS Code REST Client extension, or:

```bash
# Health check (no auth)
curl http://localhost:8000/api/health

# Check worker status
curl http://localhost:8000/api/scraper/scrape/worker-health
```

## Configuration

### Rate Limits

| Endpoint Type | Limit |
|---------------|-------|
| Auth (login, signup, password reset) | 5/minute |
| Scraping | 10/minute |
| General API | 100/minute |

### Alert Settings

Users can configure via API or Account Settings page:
- `email_enabled`: Toggle alerts on/off
- `digest_frequency_hours`: 6, 12, or 24 hours
- `alert_price_drop`: Notify on price drops
- `alert_price_increase`: Notify on price increases

### Scraping Schedule (Celery Beat)

| Task | Schedule |
|------|----------|
| Daily scrape all products | 2:00 AM UTC |
| Send alert digests | Hourly |
| Cleanup old alerts | 3:00 AM UTC |

## Security Features

- **Authentication**: Supabase Auth with JWT tokens
- **Row-Level Security**: Database policies isolate user data
- **Rate Limiting**: Prevents abuse via slowapi
- **Security Headers**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy, HSTS (production)
- **Input Validation**: Pydantic models validate all inputs
- **Error Handling**: Generic error messages, detailed server-side logging

## Production Checklist

- [ ] Set `DEBUG=false` in `.env`
- [ ] Configure CORS origins (replace `*` with your domains)
- [ ] Enable HTTPS via reverse proxy
- [ ] Set up database backups
- [ ] Configure monitoring (Sentry, etc.)
- [ ] Rotate all secrets from development
- [ ] Review RLS policies in Supabase
- [ ] Configure Supabase email templates for password reset

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design, data flows, security model |
| [docs/logic_used.md](docs/logic_used.md) | Complex algorithm explanations |
| [docs/prd.md](docs/prd.md) | Product requirements & milestones |
| [docs/database_schema.sql](docs/database_schema.sql) | Database setup script |

## License

MIT
