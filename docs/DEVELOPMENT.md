# PriceHawk Local Development Guide

This guide walks new engineers through setting up a local development environment, installing dependencies, running background services, executing tests, and maintaining code quality standards for **PriceHawk**.

---

## 1. Prerequisites

Ensure you have the following installed on your host machine:
- **Python 3.13+** (or Python 3.12+)
- **[uv](https://docs.astral.sh/uv/)** (recommended high-speed Python package manager) or standard `pip`
- **Docker** (for local Redis service)
- **Supabase Account** (for hosted PostgreSQL and Auth)
- **Git**

---

## 2. Environment Setup

### 2.1 Clone Repository & Install Dependencies
Using `uv`:
```bash
# Clone the repository
git clone https://github.com/dreww01/pricehawk.git
cd pricehawk

# Synchronize virtual environment with all runtime and dev dependencies
uv sync
```

Alternatively, using standard Python `venv` and `pip`:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.2 Install Playwright Chromium Browser
PriceHawk uses Playwright as a headless fallback for JavaScript-rendered e-commerce sites:
```bash
uv run playwright install chromium
# Or with system dependencies on Linux:
# uv run playwright install --with-deps chromium
```

### 2.3 Configure Local Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Configure your credentials:
```env
# Required Supabase Credentials
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-key
SB_JWT_SECRET=your-jwt-secret

# Required Redis Broker
REDIS_URL=redis://localhost:6379/0

# Optional AI & Email
GROQ_API_KEY=your-groq-api-key
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=your-resend-api-key
FROM_EMAIL=alerts@yourdomain.com
DEBUG=true
```

---

## 3. Running Services Locally

PriceHawk requires Redis, the FastAPI web server, and a Celery worker to support full manual price discovery and background collection.

### Terminal 1: Redis Broker
```bash
docker run -d -p 6379:6379 --name pricehawk-redis redis:7-alpine
```

### Terminal 2: FastAPI Application Server

#### Option A: Run via `run.py` (Default Port 5000)
Starts the application server on port 5000 via the `run.py` entrypoint script:
```bash
uv run python run.py
```
- **Base Server:** `http://127.0.0.1:5000`
- **Health Check:** `http://127.0.0.1:5000/api/health`
- **Swagger Documentation:** `http://127.0.0.1:5000/api/docs`
- **ReDoc Documentation:** `http://127.0.0.1:5000/api/redoc`

#### Option B: Run via Direct Uvicorn (Default Port 8000)
Starts the FastAPI application directly with Uvicorn on default port 8000 with auto-reload:
```bash
uv run uvicorn main:app --reload
```
- **Base Server:** `http://127.0.0.1:8000`
- **Health Check:** `http://127.0.0.1:8000/api/health`
- **Swagger Documentation:** `http://127.0.0.1:8000/api/docs`
- **ReDoc Documentation:** `http://127.0.0.1:8000/api/redoc`

### Terminal 3: Celery Background Worker
```bash
# Note: --pool=solo is required on macOS and Windows to prevent Playwright subprocess deadlock
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

### Terminal 4: Celery Beat Scheduler (Optional for Cron Jobs)
```bash
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

---

## 4. Running Hermetic Test Suites

PriceHawk uses `pytest` for unit and integration testing. Tests are designed to run hermetically with mocked Supabase clients and authentication overrides.

### Execute the Test Suite
```bash
# Run all unit and integration tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=app --cov-report=term-missing
```

### Test Directory Structure
```
tests/
├── conftest.py          # Pytest fixtures, test client, mock auth headers
├── test_auth.py         # Authentication route validations
├── test_health.py       # Liveness and readiness endpoints
├── test_pages.py        # Frontend Jinja2 template rendering tests
└── test_scraper.py      # Scraper auth gates and worker health checks
```

---

## 5. Code Quality, Typing, and Style Standards

### Static Typing
- All functions, models, and service methods must include strict Python type hints (`str`, `Decimal`, `Optional[T]`, `list[T]`).
- Request and response schemas must inherit from Pydantic `BaseModel`.

### Decimal for Financial Figures
- Always use `decimal.Decimal` when storing, parsing, or calculating prices and percentages. **Never use floating-point arithmetic for currency.**

### Defensive Security & Validation
- Sanitize user-provided HTML, product names, and search keywords.
- Enforce SSRF domain blocking for all outbound scraping requests (`validate_url()`).
- Verify tenant ownership on all database operations using Supabase RLS and explicit `.eq("user_id", current_user.id)` query filters.
