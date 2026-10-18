# PriceHawk Local Development Guide

## Navigation

- [Prerequisites](#prerequisites)
- [Install](#install)
- [Environment](#environment)
- [Database](#database)
- [Running Services](#running-services)
- [Testing](#testing)
- [Formatting, Linting, and Typing](#formatting-linting-and-typing)
- [Development Workflow](#development-workflow)

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) preferred; pip is acceptable for fallback virtualenv workflows
- Docker and Docker Compose
- Supabase project for integration testing and manual development
- Redis for Celery
- Playwright Chromium for JavaScript-rendered storefronts

## Install

Preferred uv workflow:

```bash
uv sync
```

Fallback pip workflow:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest pytest-asyncio pytest-cov
```

Install Playwright browser binaries:

```bash
uv run playwright install chromium
```

or, in an activated pip virtualenv:

```bash
playwright install chromium
```

## Environment

Create `.env` with the variables consumed by `app/core/config.py`:

```env
SB_URL=https://your-project.supabase.co
SB_ANON_KEY=your-anon-key
SB_SERVICE_KEY=your-service-role-key
SB_JWT_SECRET=your-supabase-jwt-secret
REDIS_URL=redis://localhost:6379/0
DEBUG=true
GROQ_API_KEY=
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
FROM_EMAIL=
FROM_NAME=PriceHawk Alerts
MAX_PRODUCTS_FETCH=500
```

Keep optional `GROQ_API_KEY` and SMTP values empty for development unless testing those features. AI and email clients are lazily initialized, so the API can start without optional integrations.

## Database

Run [`database_schema.sql`](database_schema.sql) in Supabase SQL Editor. See [Database](DATABASE.md) for full schema, indexes, RLS, and verification queries.

For hermetic automated tests, prefer route-level dependency overrides and mocked Supabase clients rather than a shared external database.

## Running Services

### Docker Compose

```bash
docker compose up --build
```

### Individual processes

```bash
# terminal 1
redis-server

# terminal 2
uv run python run.py

# terminal 3
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo

# terminal 4
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

Health check:

```bash
curl http://localhost:8000/api/health
```

Interactive docs:

- Swagger UI: <http://localhost:8000/api/docs>
- ReDoc: <http://localhost:8000/api/redoc>

## Testing

Run the required issue verification:

```bash
uv run pytest tests/ -v
```

Additional useful commands:

```bash
uv run pytest -v
uv run pytest tests/test_auth.py -v
uv run pytest --cov=app --cov-report=term-missing
```

Test standards:

- Mock external Supabase, Groq, SMTP, and storefront calls.
- Test public route/service behavior rather than private implementation details.
- Assert explicit status codes and response payloads.
- Keep tests deterministic and safe to run without network credentials.

## Formatting, Linting, and Typing

The current project metadata does not declare a dedicated formatter, linter, or static type checker dependency. New code should still follow these standards:

| Concern | Standard |
| --- | --- |
| Formatting | Black-compatible Python formatting, 88-100 character practical line width, no trailing whitespace. |
| Imports | Standard library, third-party, local imports grouped clearly. |
| Typing | Type hints for new functions, route helpers, service methods, and return values. |
| Validation | Pydantic models for public API request/response contracts. |
| Comments | Explain non-obvious rationale and operational constraints, not obvious syntax. |

If tooling is added later, prefer:

```bash
uv add --dev ruff mypy
uv run ruff check .
uv run ruff format .
uv run mypy app
```

Do not introduce broad formatting churn in unrelated files during feature work.

## Development Workflow

1. Read [Architecture](ARCHITECTURE.md) and [API](API.md) before changing routes or services.
2. Ground changes in actual route modules under `app/api/routes/`; do not add synthetic endpoints not used by the product.
3. Keep application logic changes minimal and focused.
4. Add or update tests under `tests/` for behavior changes.
5. Run `uv run pytest tests/ -v` before submitting.
6. Update documentation when setup, public routes, schemas, or operational behavior changes.

## Common Debugging Checks

| Problem | Check |
| --- | --- |
| API does not start | Missing required `SB_URL`, `SB_ANON_KEY`, `SB_SERVICE_KEY`, or `SB_JWT_SECRET`. |
| Authenticated route returns 401 | Bearer token missing, expired, or signed by a different Supabase project. |
| Product route returns 404 | Product does not belong to current user or was soft-deleted. |
| Manual scrape stays queued | Redis or Celery worker is not running. |
| Worker cannot write price history | Worker missing service key or Supabase schema not applied. |
| Playwright fallback fails | Chromium not installed with `playwright install chromium`. |
