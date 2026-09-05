# Local Development & Engineering Guide

## Overview

This guide provides step-by-step developer onboarding for building, running, and testing the PriceHawk codebase locally.

---

## Prerequisites & Toolchain

- **Python**: Version `3.13+` (or `3.12+` with compatible libraries)
- **Package & Environment Manager**: [uv](https://docs.astral.sh/uv/) (recommended) or `pip` / `venv`
- **Container Engine**: Docker Desktop or Podman (for running local Redis)
- **Database**: Active Supabase project (Free tier)
- **AI Engine (Optional)**: [Groq Cloud](https://console.groq.com) API Key for LLM price analysis

---

## Step-by-Step Local Setup

### 1. Clone Repository & Setup Environment

```bash
# Clone the repository
git clone https://github.com/dreww01/pricehawk.git
cd pricehawk

# Synchronize dependencies with uv, including the test extra (creates .venv)
uv sync --extra test

# Alternatively, using standard python venv and pip:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

### 2. Install Playwright Chromium Browser Engine

Playwright requires Chromium system binaries to scrape JavaScript-rendered single-page applications:

```bash
# Install Chromium binary
playwright install chromium

# On Linux/Debian systems, install missing OS dependencies if needed:
playwright install-deps chromium
```

### 3. Environment Configuration

Copy the example configuration and populate your project secrets:

```bash
cp .env.example .env
```

Edit `.env` with your values:
```env
# Supabase Configuration (Required)
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-key
SB_JWT_SECRET=your-jwt-secret

# Redis Configuration (Required for background workers)
REDIS_URL=redis://localhost:6379/0

# App Settings
DEBUG=true
MAX_PRODUCTS_FETCH=500

# Optional: Groq AI & SMTP
GROQ_API_KEY=gsk_your_groq_api_key
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=re_your_resend_key
FROM_EMAIL=alerts@yourdomain.com
```

### 4. Initialize Database Schema

1. Open your [Supabase Dashboard](https://supabase.com/dashboard).
2. Navigate to **SQL Editor**.
3. Copy and execute the complete schema setup from `docs/database_schema.sql`.

---

## Running the Application Locally

Running the complete application locally involves 3 or 4 terminal processes (or 1 command using Docker Compose):

### Option A: Manual Terminal Processes

**Terminal 1: Start Redis Broker**
```bash
docker run -d --name pricehawk-redis -p 6379:6379 redis:7-alpine
```

**Terminal 2: Start FastAPI Web Server**
```bash
uv run python run.py
# Server running at: http://127.0.0.1:5000 (or http://localhost:8000 via uvicorn main:app --reload)
```

**Terminal 3: Start Celery Worker**
```bash
# macOS / Windows Development
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo

# Linux Development
uv run celery -A app.tasks.celery_app worker --loglevel=info
```

**Terminal 4: Start Celery Beat (Scheduler)**
```bash
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

### Option B: Docker Compose (All-in-One)

```bash
docker compose up -d --build
docker compose logs -f
```

---

## Testing & Quality Assurance

PriceHawk includes automated test suites covering authentication, scraping engines, page rendering, export streaming, and system health checks.

### Running Test Suites with Pytest

Install the `test` extra before running pytest or coverage commands. If you did not use `uv sync --extra test` during setup, run:

```bash
uv sync --extra test
```

```bash
# Run all unit and integration tests (requires the test extra installed above)
uv run pytest tests/ -v

# Run tests with code coverage report (requires the test extra installed above)
uv run pytest tests/ --cov=app --cov-report=term-missing

# Run a specific test module (requires the test extra installed above)
uv run pytest tests/test_auth.py -v
uv run pytest tests/test_pages.py -v
uv run pytest tests/test_scraper.py -v
```

---

## Code Quality, Linting & Typing Standards

### 1. Static Typing
- All new service functions, route handlers, and data models must have explicit type annotations.
- Use Python 3.10+ union types (e.g., `str | None`, `list[dict]`).
- Request and response boundaries must use strictly validated Pydantic models.

### 2. Error Handling & OWASP Compliance
- Route handlers must catch exceptions and return standardized HTTP status codes.
- Do not expose raw tracebacks or sensitive internal parameters to clients in production (`DEBUG=false`).
- Use the global exception handler in `main.py` which generates unique, traceable `error_id` hashes.

### 3. Asynchronous Discipline
- Use `httpx.AsyncClient` with proper lifecycle management for all outbound HTTP requests.
- Never execute blocking synchronous I/O or sleep calls inside `async def` route handlers.
- Offload expensive computations or scraping operations to Celery tasks.
