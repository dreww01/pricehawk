# PriceHawk — Local Development Guide

This guide provides step-by-step onboarding instructions for setting up, developing, testing, and debugging **PriceHawk** in a local development environment.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local Environment Setup](#2-local-environment-setup)
3. [Running Local Services](#3-running-local-services)
4. [Automated Testing Suite](#4-automated-testing-suite)
5. [Code Quality, Linting & Static Typing](#5-code-quality-linting--static-typing)
6. [Debugging & Developer Tooling](#6-debugging--developer-tooling)

---

## 1. Prerequisites

Ensure the following tools are installed on your workstation:

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** (recommended high-speed Python package manager) or standard `pip` / `venv`
- **Docker Desktop** or **Podman** (for running local Redis and container tests)
- **Supabase Account** (free tier at [supabase.com](https://supabase.com))
- **Groq API Key** (optional, for local AI insights testing at [console.groq.com](https://console.groq.com))

---

## 2. Local Environment Setup

### 2.1 Clone Repository & Install Dependencies

Using `uv` (recommended):

```bash
# Clone the repository
git clone https://github.com/dreww01/pricehawk.git
cd pricehawk

# Synchronize virtual environment with all dependencies
uv sync

# Activate the virtual environment
source .venv/bin/activate
```

Or using standard `python3` / `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.2 Install Playwright Chromium Browser

PriceHawk uses Playwright for JavaScript-rendered store extraction and anti-bot fallback:

```bash
playwright install chromium
```

### 2.3 Configure Local Environment Variables

Copy the example template:

```bash
cp .env.example .env
```

Populate `.env` with your development credentials:

```env
# Supabase Configuration
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-key
SB_JWT_SECRET=your-jwt-secret

# Redis Configuration (Local Docker Redis)
REDIS_URL=redis://localhost:6379/0

# App Settings
DEBUG=true
MAX_PRODUCTS_FETCH=500

# Optional: Groq AI Insights
GROQ_API_KEY=your-groq-api-key

# Optional: Resend SMTP Email Delivery
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=re_your_api_key
FROM_EMAIL=alerts@yourdomain.com
```

### 2.4 Initialize Supabase Database

1. Navigate to the **SQL Editor** in your Supabase project dashboard.
2. Load the contents of [`docs/database_schema.sql`](database_schema.sql).
3. Execute the script to initialize tables, indexes, triggers, and Row-Level Security policies.

---

## 3. Running Local Services

To run PriceHawk with full manual scraping and periodic task capabilities, execute the following processes across separate terminals:

### Terminal 1: Start Redis Message Broker (Docker)
```bash
docker run --rm -d -p 6379:6379 --name pricehawk-redis redis:7-alpine
```

### Terminal 2: Start FastAPI Web Server
```bash
# Using dev runner script
python run.py

# Or via uvicorn with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
- Web Application: http://localhost:8000
- Interactive Swagger Docs: http://localhost:8000/api/docs
- ReDoc API Reference: http://localhost:8000/api/redoc

### Terminal 3: Start Celery Worker (Required for Manual Scrapes)
```bash
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```
*(Note: `--pool=solo` is recommended on macOS / Windows for compatibility with asyncio and Playwright subprocesses).*

### Terminal 4: Start Celery Beat (Periodic Scheduler)
```bash
celery -A app.tasks.celery_app beat --loglevel=info
```

---

## 4. Automated Testing Suite

PriceHawk includes hermetic automated tests covering authentication, route authorization, health endpoints, Jinja2 page rendering, and scraper progress flows.

### 4.1 Running Tests

```bash
# Run all automated tests
pytest tests/ -v

# Run with verbose tracebacks
pytest tests/ -vv --tb=short

# Run a specific test module
pytest tests/test_auth.py -v
pytest tests/test_pages.py -v
pytest tests/test_scraper.py -v
pytest tests/test_health.py -v
```

### 4.2 Test Suite Structure

```
tests/
├── conftest.py          # Pytest fixtures, mock env defaults, TestClient setup
├── test_auth.py         # Login, signup, password reset validation tests
├── test_health.py       # Health check, root redirects, docs availability
├── test_pages.py        # Jinja2 template rendering, auth redirects, cookies
└── test_scraper.py      # Scraper auth gates, price history, worker health
```

---

## 5. Code Quality, Linting & Static Typing

### 5.1 Static Typing & Pydantic Validation

- All request/response payloads must adhere to Pydantic v2 schemas in `app/db/models.py`.
- Type annotations (`str`, `int`, `Decimal`, `Optional[T]`) must be used across all route handlers and service methods.

### 5.2 Code Formatting & Standards

```bash
# Format code with ruff / black
ruff format .

# Check linting rules
ruff check .
```

---

## 6. Debugging & Developer Tooling

### 6.1 VS Code REST Client (`test.http`)

The root directory contains a `test.http` file for rapidly issuing test requests directly inside VS Code:
- Health check requests
- Signup & Login token extraction
- Store discovery triggers
- Manual scrape tasks and SSE progress streaming

### 6.2 Checking Worker Health

Verify your Celery worker is receiving heartbeats from Redis:

```bash
curl http://localhost:8000/api/scraper/scrape/worker-health
# {"worker_status":"healthy","ping_response":"['celery@...']","active_tasks":0,"error":null}
```
