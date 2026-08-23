# Contributing to PriceHawk

Thank you for your interest in contributing to **PriceHawk**! PriceHawk is a multi-platform price monitoring system that discovers products from competitor stores, tracks prices over time, and provides AI-powered insights with automated alerts.

We welcome contributions of all kinds: bug reports, bug fixes, new store handlers, feature enhancements, documentation improvements, and architectural refinements.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Prerequisites](#prerequisites)
3. [Local Development Setup](#local-development-setup)
   - [1. Clone the Repository](#1-clone-the-repository)
   - [2. Install Dependencies](#2-install-dependencies)
   - [3. Configure Environment Variables](#3-configure-environment-variables)
   - [4. Set Up Supabase Database](#4-set-up-supabase-database)
   - [5. Install Playwright Browsers](#5-install-playwright-browsers)
   - [6. Start Local Services](#6-start-local-services)
   - [7. Verify the Setup](#7-verify-the-setup)
4. [Development Workflow](#development-workflow)
   - [Branching Strategy](#branching-strategy)
   - [Commit Message Conventions](#commit-message-conventions)
   - [Pull Request Workflow](#pull-request-workflow)
5. [Architecture & Coding Standards](#architecture--coding-standards)
   - [Async-First Design](#async-first-design)
   - [Type Annotations & Validation](#type-annotations--validation)
   - [Adding a New Store Handler (Plugin Architecture)](#adding-a-new-store-handler-plugin-architecture)
   - [Security & Row-Level Security (RLS)](#security--row-level-security-rls)
   - [Background Tasks & Celery](#background-tasks--celery)
   - [Frontend & Templates](#frontend--templates)
6. [Testing Guidelines](#testing-guidelines)
   - [Running Tests](#running-tests)
   - [Writing Hermetic Unit & Integration Tests](#writing-hermetic-unit--integration-tests)
   - [Test Coverage](#test-coverage)
7. [Reporting Issues & Submitting Bug Reports](#reporting-issues--submitting-bug-reports)
8. [Documentation Contributions](#documentation-contributions)
9. [Review Process & PR Checklist](#review-process--pr-checklist)

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free experience for everyone.

- **Be respectful and collaborative:** Treat all contributors, maintainers, and community members with dignity and respect.
- **Provide constructive feedback:** When reviewing code or discussing architecture, focus on the code and design rationale.
- **Gracefully accept constructive criticism:** Value technical discussions that elevate code health and maintainability.
- **Focus on what is best for the project:** Prioritize simplicity, reliability, security, and user experience.

---

## Prerequisites

Before setting up PriceHawk locally, ensure you have the following tools and accounts:

- **Python 3.13+**: PriceHawk leverages modern Python features (strict type syntax, async performance improvements).
- **[uv](https://docs.astral.sh/uv/)** (recommended) or standard `pip` / `virtualenv`.
- **Docker**: For running a local Redis instance (or access to an external Redis instance).
- **Supabase Account**: For PostgreSQL database, Auth, and Row-Level Security (free tier is sufficient).
- **Groq API Key** (optional): For AI-powered market insights (Llama 3.3 70B via Groq API).
- **Resend API Key / SMTP credentials** (optional): For testing digest email alerts.

---

## Local Development Setup

### 1. Clone the Repository

```bash
git clone https://github.com/dreww01/pricehawk.git
cd pricehawk
```

### 2. Install Dependencies

Using `uv` (recommended):

```bash
# Create virtualenv and install all dependencies including test dependencies
uv sync --extra test
```

Using standard `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

### 3. Configure Environment Variables

Create your local `.env` file from the provided template:

```bash
cp .env.example .env
```

Edit `.env` and fill in the required variables:

```env
# Server
DEBUG=true
PORT=8000
HOST=0.0.0.0

# Required - Supabase Database & Auth
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-supabase-anon-key
SB_SERVICE_KEY=your-supabase-service-role-key

# Required - Redis (Message Broker for Celery)
REDIS_URL=redis://localhost:6379/0

# Optional - Groq API (AI Insights)
GROQ_API_KEY=gsk_your_groq_api_key

# Optional - SMTP Email Alerts (Resend or other SMTP)
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=re_your_resend_api_key
FROM_EMAIL=alerts@yourdomain.com
FROM_NAME="PriceHawk Alerts"
```

### 4. Set Up Supabase Database

1. Create a project in [Supabase](https://supabase.com).
2. Open the **SQL Editor** in your Supabase dashboard.
3. Paste and execute the contents of [`docs/database_schema.sql`](docs/database_schema.sql).
4. Verify the required tables and security policies are created:
   - `products`
   - `competitors`
   - `price_history`
   - `insights`
   - `pending_alerts`
   - `user_alert_settings`
   - `alert_history`

### 5. Install Playwright Browsers

For stores requiring dynamic JavaScript rendering (e.g. Amazon, single-page apps):

```bash
uv run playwright install chromium
# Or with active virtual environment:
playwright install chromium
```

### 6. Start Local Services

You will typically run four processes during active development:

#### Terminal 1: Redis Broker

```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

*(If already created: `docker start redis`)*

#### Terminal 2: FastAPI Web / API Server

```bash
uv run python run.py
# Or directly via uvicorn:
uv run uvicorn main:app --reload --port 8000
```

#### Terminal 3: Celery Worker (Processes Manual and Scheduled Scrapes)

```bash
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

#### Terminal 4: Celery Beat (Schedules Daily Scrapes and Alert Digests)

```bash
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

#### Alternative: Full Docker Compose Setup

To spin up all services concurrently:

```bash
docker compose up -d
docker compose logs -f
```

### 7. Verify the Setup

1. Check the health check endpoint:
   ```bash
   curl http://localhost:8000/api/health
   # Expected response: {"status":"ok","timestamp":"...","database":"connected","error":null}
   ```
2. Check the Celery worker health endpoint:
   ```bash
   curl http://localhost:8000/api/scraper/scrape/worker-health
   ```
3. Open the interactive API documentation:
   - Swagger UI: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)
   - ReDoc: [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)
   - Web App: [http://localhost:8000/](http://localhost:8000/)

---

## Development Workflow

### Branching Strategy

- `main` is protected and represents production-ready code.
- Create feature or bugfix branches off `main`.
- Branch naming convention:
  - `feat/<short-description>` or `feat/<issue-id>-<description>`
  - `fix/<short-description>`
  - `docs/<short-description>`
  - `refactor/<short-description>`
  - `test/<short-description>`
  - `chore/<short-description>`

### Commit Message Conventions

PriceHawk follows the **Conventional Commits** specification:

```
<type>(<optional-scope>): <description>

[optional body]

[optional footer(s)]
```

#### Common Types:
- `feat`: A new user-facing feature or capability.
- `fix`: A bug fix.
- `docs`: Documentation changes only.
- `style`: Formatting, missing semicolons, whitespace (no code change).
- `refactor`: Refactoring production code without changing behavior.
- `perf`: Code change that improves performance.
- `test`: Adding missing tests or correcting existing tests.
- `build` / `chore`: Maintenance tasks, dependency updates, tooling.

#### Example Commit Messages:
- `feat(discovery): add BigCommerce store handler plugin`
- `fix(scraper): handle multi-currency decimal separators in euro prices`
- `test(health): add test cases for degraded database connectivity`
- `docs: update deployment guidelines for Railway`

### Pull Request Workflow

1. **Keep PRs Focused:** Aim for single-purpose, atomic pull requests. Avoid mixing unrelated refactors with feature implementations.
2. **Sync with Main:** Keep your branch up to date with `git fetch origin && git rebase origin/main`.
3. **Run Local Tests:** Ensure the full test suite passes with 100% green status before pushing.
4. **Open a PR:** Fill out the PR template completely:
   - Describe the problem and proposed solution.
   - Link related issue(s) (e.g. `Fixes #12` or `Relates to ORC-15`).
   - Include verification steps or test output.

---

## Architecture & Coding Standards

For detailed architecture diagrams, request flows, and algorithms, refer to:
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/logic_used.md`](docs/logic_used.md)

### Async-First Design

PriceHawk is built with asynchronous Python:
- All route handlers and I/O-bound functions must be `async def`.
- Use `httpx.AsyncClient` for all outbound HTTP requests; never use blocking libraries like `urllib` or `requests` in asynchronous event loop contexts.
- Always manage resources cleanly using async context managers (`async with httpx.AsyncClient(...) as client:`).

### Type Annotations & Validation

- Use strict Python 3.13+ type hinting for all function parameters and return types (e.g. `list[str]`, `dict[str, Any]`, `int | None`).
- Use Pydantic models (`app/db/models.py` or route-level schemas) for all request bodies and response payloads.
- Ensure all configuration settings in `app/core/config.py` use Pydantic `BaseSettings`.

### Adding a New Store Handler (Plugin Architecture)

PriceHawk uses the **Strategy Pattern** for store discovery and product extraction:

1. **Subclass `BaseStoreHandler`**:
   Create a new file in `app/services/stores/<platform>.py`:

   ```python
   from decimal import Decimal
   import httpx
   from app.services.stores.base import BaseStoreHandler, DiscoveredProduct

   class MyPlatformHandler(BaseStoreHandler):
       platform_name: str = "myplatform"

       async def detect(self, url: str) -> bool:
           """Return True if this store runs on MyPlatform."""
           async with httpx.AsyncClient(timeout=10.0) as client:
               try:
                   response = await client.head(f"{url.rstrip('/')}/api/products")
                   return response.status_code == 200
               except Exception:
                   return False

       async def fetch_products(
           self,
           url: str,
           keyword: str | None = None,
           limit: int = 50,
       ) -> list[DiscoveredProduct]:
           """Fetch products and normalize into DiscoveredProduct objects."""
           # Implement platform-specific extraction logic...
           products = [...]
           return self.filter_by_keyword(products, keyword)[:limit]
   ```

2. **Register the Handler**:
   In `app/services/store_detector.py`, add the new handler to the prioritized detection chain before `GenericHandler` (which serves as the fallback).

3. **Add Tests**:
   Create unit tests covering both positive detection, negative detection, and product extraction with mock responses.

### Security & Row-Level Security (RLS)

- **Defense in Depth**:
  - Always enforce user ownership in the application layer (e.g. checking `user_id == current_user.id`).
  - Rely on Supabase Row-Level Security (RLS) policies at the database layer.
- **Client Tokens vs Service Role**:
  - User-facing API routes must use `get_supabase_client(access_token)` to enforce RLS.
  - Background Celery tasks use `get_supabase_client()` with the service role key to process cross-user scheduled tasks.
- **Input Sanitization**:
  - Sanitize user inputs and external scraped content to prevent XSS.
  - Emails never include clickable competitor URLs to prevent phishing risks.
- **Rate Limiting**:
  - Apply `@limiter.limit(...)` decorators from `app/middleware/rate_limit.py` to endpoints susceptible to abuse (auth, discovery, scraping).

### Background Tasks & Celery

- **Idempotency**: Scraping tasks check `price_history` to prevent duplicate entries on task retries within the same day.
- **Exponential Backoff**: Configure Celery retry logic with exponential backoff (`countdown=60 * (2 ** retry_count)`).
- **Time Limits**: Maintain sensible soft (`270s`) and hard (`300s`) task time limits to prevent hung scrapers.

### Frontend & Templates

- Templates reside in `app/templates/` and use Jinja2.
- Interactive components use **HTMX** (e.g. SSE progress streams, inline form submissions).
- Styling uses utility classes compatible with **Tailwind CSS**.

---

## Testing Guidelines

Reliable automated testing is fundamental to PriceHawk. All modifications and new features must be accompanied by comprehensive tests.

### Running Tests

Run all unit and integration tests:

```bash
uv run pytest
# Or with active virtual environment:
pytest
```

Run tests with verbose output:

```bash
pytest -v
```

Run a specific test module:

```bash
pytest tests/test_health.py -v
```

Run with test coverage report:

```bash
pytest --cov=app --cov-report=term-missing
```

Generate HTML coverage report:

```bash
pytest --cov=app --cov-report=html
# Open htmlcov/index.html in your browser
```

### Writing Hermetic Unit & Integration Tests

- **No Live External Calls**: Tests must never call live Supabase instances, external store websites, or third-party APIs during execution.
- **Mocking Auth**: Use FastAPI's `dependency_overrides` with `get_current_user` to simulate authenticated users:
  ```python
  app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-123", email="user@example.com")
  ```
- **Mocking Supabase Client**: Use `unittest.mock.patch` to mock database queries and assert expected operations.
- **Test Structure**:
  - Route and API contract tests belong in `tests/` or `app/tests/`.
  - Service and scraper tests belong in `tests/` or `app/tests/`.
  - Follow the naming pattern `test_<module_name>.py`.

---

## Reporting Issues & Submitting Bug Reports

When opening an issue, provide clear and actionable information:

### Bug Reports
- **Description**: Clear summary of the issue.
- **Steps to Reproduce**: Minimal, sequential reproduction steps.
- **Expected Behavior**: What you expected to happen.
- **Actual Behavior**: What actually happened (include HTTP status codes, error messages, or logs).
- **Environment**: OS, Python version, browser (if frontend issue), Docker/Redis version.

### Security Vulnerabilities
If you discover a security vulnerability, please **do not open a public GitHub issue**. Responsibly disclose it by contacting the repository maintainers directly.

---

## Documentation Contributions

Documentation is treated with the same engineering rigor as production code:

- Update `README.md` if developer setup, environment variables, or CLI commands change.
- Update `docs/architecture.md` and `docs/logic_used.md` if architectural patterns, database schemas, or scraping logic evolve.
- Maintain comprehensive OpenAPI docstrings and Pydantic field descriptions on all new route handlers and models.

---

## Review Process & PR Checklist

Before submitting your Pull Request, complete this verification checklist:

- [ ] My code adheres to the project's coding standards and Python 3.13+ typing guidelines.
- [ ] New or modified features include corresponding tests in `tests/` or `app/tests/`.
- [ ] All test cases pass with 100% green status (`pytest`).
- [ ] No regression is introduced to existing functionality.
- [ ] Sensitive secrets and credentials are not hardcoded or committed.
- [ ] No temporary or internal harness files (`.dsh/`, cache artifacts) are staged.
- [ ] Commit messages follow the [Conventional Commits](#commit-message-conventions) specification.
- [ ] Related documentation has been updated.

Thank you for contributing to PriceHawk! 🦅
