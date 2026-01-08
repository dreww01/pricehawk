# PriceHawk

Multi-platform price monitoring system that discovers products from competitor stores, tracks prices over time, and provides automated alerts on price changes.

## Features

- **Multi-platform discovery**: Shopify, WooCommerce, Amazon, eBay, custom sites
- **Automated scraping**: Daily background price collection via Celery
- **AI insights**: Pattern detection and pricing recommendations (Groq)
- **Email alerts**: Notifications on significant price changes
- **CSV export**: Download price history for analysis

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI |
| Database | Supabase (PostgreSQL) |
| Auth | Supabase Auth (JWT) |
| Task Queue | Celery + Redis |
| Scraping | httpx, BeautifulSoup, Playwright |
| AI | Groq API |

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Redis (for Celery)
- Supabase account
- Groq API key (for AI insights)

### Local Development

1. **Clone and setup**
   ```bash
   git clone <repo-url>
   cd pricehawk
   ```

2. **Install dependencies**
   ```bash
   # Using uv (recommended)
   uv sync

   # Or using pip
   pip install -e .
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Setup Supabase database**
   - Create a new Supabase project
   - Run the SQL in `database_schema.sql`
   - Enable RLS policies

5. **Install Playwright browsers** (for Amazon/eBay scraping)
   ```bash
   playwright install chromium
   ```

6. **Start Redis** (required for Celery)
   ```bash
   # Using Docker
   docker run -d -p 6379:6379 redis:7-alpine

   # Or install locally
   ```

7. **Run the API**
   ```bash
   # Development
   uv run python run.py

   # Or
   uvicorn main:app --reload
   ```

8. **Start Celery worker** (separate terminal)
   ```bash
   celery -A app.tasks.celery_app worker --loglevel=info
   ```

9. **Start Celery beat** (separate terminal, for scheduled tasks)
   ```bash
   celery -A app.tasks.celery_app beat --loglevel=info
   ```

### Docker Deployment

```bash
# Build and run all services
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

## API Documentation

- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI JSON: http://localhost:8000/api/openapi.json

## API Endpoints

### Health
- `GET /api/health` - Health check

### Products
- `GET /api/products` - List tracked products
- `GET /api/products/{id}` - Get product details
- `PUT /api/products/{id}` - Update product
- `DELETE /api/products/{id}` - Delete product

### Discovery
- `POST /api/stores/discover` - Discover products from store URL
- `POST /api/stores/track` - Add products to tracking

### Scraping
- `POST /api/scrape/manual/{product_id}` - Trigger manual scrape
- `GET /api/scrape/worker-health` - Check Celery worker status

### Insights
- `GET /api/insights/{product_id}` - Get AI insights
- `POST /api/insights/generate/{product_id}` - Generate new insights

### Alerts
- `GET /api/alerts` - Get alert history
- `GET /api/alerts/settings` - Get notification settings
- `PUT /api/alerts/settings` - Update notification settings
- `POST /api/alerts/test` - Send test email

### Export
- `GET /api/export/{product_id}/csv` - Export price history

## Environment Variables

```env
# Supabase (required)
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-key

# App
DEBUG=false

# Redis/Celery
REDIS_URL=redis://localhost:6379/0

# AI (optional)
GROQ_API_KEY=your-groq-api-key

# Email (optional)
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=your-api-key
FROM_EMAIL=noreply@yourdomain.com
```

## Testing

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
pytest

# With coverage
pytest --cov=app --cov-report=html
```

## Production Checklist

- [ ] Set `DEBUG=false`
- [ ] Configure proper CORS origins
- [ ] Enable HTTPS (via reverse proxy)
- [ ] Set up database backups
- [ ] Configure monitoring (Sentry, etc.)
- [ ] Rotate all secrets from development
- [ ] Enable rate limiting
- [ ] Review RLS policies

## License

MIT
