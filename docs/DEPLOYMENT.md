# PriceHawk Deployment & Operations Runbook

This runbook provides comprehensive instructions for deploying, configuring, and operating **PriceHawk** across Docker Compose and Railway production environments.

---

## 1. Production Architecture Overview

In production, PriceHawk operates as three core components connected to cloud-managed databases and services:

```mermaid
flowchart TB
    subgraph Client["Edge & DNS"]
        Internet["Internet Traffic / Web Clients"]
    end

    subgraph Railway["Railway Cloud Platform"]
        WebService["FastAPI Web & API Service (uvicorn main:app)"]
        WorkerService["Celery Worker Service (celery worker --pool=solo)"]
        RedisInstance[("Managed Redis Instance (redis://...)")]
    end

    subgraph CloudServices["External Managed Platforms"]
        SupabasePostgres[("Supabase PostgreSQL (Database + RLS)")]
        SupabaseAuth["Supabase Authentication"]
        GroqCloud["Groq Cloud AI (Llama 3.3 70B)"]
        ResendEmail["Resend SMTP Email Delivery"]
    end

    Internet -->|HTTPS / Port 443| WebService
    WebService -->|Read / Write (User Token RLS)| SupabasePostgres
    WebService -->|Verify JWT ES256| SupabaseAuth
    WebService -->|Enqueue Jobs| RedisInstance
    WebService -->|AI Requests| GroqCloud

    RedisInstance --> WorkerService
    WorkerService -->|Write Snapshots (Service Role Key)| SupabasePostgres
    WorkerService -->|Send Digest Emails| ResendEmail
```

---

## 2. Environment Variables & Secret Configuration

Create a `.env` file from `.env.example`. The following matrix outlines required vs optional environment variables:

| Variable | Required | Default / Example | Purpose & Description |
| :--- | :---: | :--- | :--- |
| `SB_URL` | **Yes** | `https://your-project.supabase.co` | Supabase project API gateway URL. |
| `SB_ANON_KEY` | **Yes** | `eyJhbGciOi...` | Supabase anonymous public key (enforces RLS). |
| `SB_SERVICE_KEY` | **Yes** | `eyJhbGciOi...` | Supabase service role key (bypasses RLS for worker). |
| `SB_JWT_SECRET` | **Yes** | `your-jwt-secret` | Supabase JWT signing secret. |
| `REDIS_URL` | **Yes** | `redis://localhost:6379/0` | Redis connection URL for task queue and broker. |
| `GROQ_API_KEY` | No | `gsk_...` | Groq API key for Llama 3.3 70B price trend synthesis. |
| `SMTP_HOST` | No | `smtp.resend.com` | SMTP host for price drop/increase digest emails. |
| `SMTP_PORT` | No | `587` | SMTP TLS port. |
| `SMTP_USERNAME` | No | `resend` | SMTP username. |
| `SMTP_PASSWORD` | No | `re_...` | SMTP API key / password. |
| `FROM_EMAIL` | No | `alerts@yourdomain.com` | Verified sender email address. |
| `FROM_NAME` | No | `PriceHawk Alerts` | Display name on dispatched digest emails. |
| `DEBUG` | No | `false` | Enable verbose logging and permissive CORS. Set `false` in production. |
| `MAX_PRODUCTS_FETCH` | No | `500` | Ceiling for store catalog discovery queries. |

---

## 3. Docker & Docker Compose Deployment

### 3.1 Multi-Stage Dockerfile
PriceHawk uses a hardened multi-stage Docker build that packages Python 3.13, Playwright, and Chromium under a non-root `pricehawk:1000` user:

```dockerfile
# Stage 1: Build virtual environment via uv
FROM python:3.13-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

# Stage 2: Runtime with Chromium dependencies
FROM python:3.13-slim AS production
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 pricehawk && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home pricehawk
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/home/pricehawk/.cache/ms-playwright
RUN mkdir -p /home/pricehawk/.cache && \
    playwright install chromium && \
    chown -R pricehawk:pricehawk /home/pricehawk/.cache
COPY --chown=pricehawk:pricehawk . .
USER pricehawk
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3.2 Running Multi-Container Stack
```bash
# 1. Start all containers in background
docker compose up -d

# 2. Inspect running services (api, redis, celery-worker, celery-beat)
docker compose ps

# 3. Stream real-time logs
docker compose logs -f

# 4. Tear down stack
docker compose down
```

---

## 4. Railway Cloud Production Deployment

### Step 1: Provision Managed Redis
1. In the [Railway Dashboard](https://railway.app), open your project.
2. Click **+ New** ➔ **Database** ➔ **Redis**.
3. Railway automatically sets `${{Redis.REDIS_URL}}`.

### Step 2: Configure Web Application Service
1. Link your GitHub repository (`dreww01/pricehawk`).
2. Add environment variables under the **Variables** tab (`SB_URL`, `SB_ANON_KEY`, `SB_SERVICE_KEY`, `SB_JWT_SECRET`, `GROQ_API_KEY`, `SMTP_PASSWORD`).
3. Set `REDIS_URL` with reference: `${{Redis.REDIS_URL}}`.
4. Under **Networking**, click **Generate Domain** to assign a public HTTPS address.

### Step 3: Configure Celery Worker Service
1. Click **+ New** ➔ **GitHub Repo** ➔ select `pricehawk` again.
2. Go to **Settings** ➔ **Deploy** ➔ **Custom Start Command**:
   ```bash
   celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
   ```
3. Share the same environment variables from the web service.

---

## 5. Supabase Database Setup & Migrations

1. Open your project on [Supabase](https://supabase.com).
2. Go to the **SQL Editor**.
3. Copy and run the entire idempotent schema from `docs/database_schema.sql`.
4. Confirm tables (`products`, `competitors`, `price_history`, `insights`, `pending_alerts`, `user_alert_settings`, `alert_history`) and RLS policies are active.

---

## 6. Observability & Operational Troubleshooting

### Problem: Redis Connection Refused
- **Symptom:** `redis.exceptions.ConnectionError: Error connecting to localhost:6379`.
- **Mitigation:** Ensure `REDIS_URL` matches the internal or external Redis host (e.g. `redis://redis:6379/0` in Docker Compose or `${{Redis.REDIS_URL}}` in Railway).

### Problem: Playwright Browser Execution Failure
- **Symptom:** `playwright._impl._errors.Error: Executable doesn't exist at /home/pricehawk/.cache/ms-playwright/...`
- **Mitigation:** Execute `playwright install chromium` inside the container or ensure `PLAYWRIGHT_BROWSERS_PATH` is configured in the environment.

### Problem: Database RLS Permission Denials
- **Symptom:** API queries return empty arrays or background tasks fail to write price snapshots.
- **Mitigation:** Ensure the web API forwards the user's Bearer token (`get_supabase_client(token)`), and the background worker uses the service key (`get_supabase_client()`) to bypass RLS.
