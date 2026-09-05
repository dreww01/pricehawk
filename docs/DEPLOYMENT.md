# PriceHawk — Deployment & Operations Runbook

This runbook provides an exhaustive, production-grade guide for deploying, operating, scaling, and maintaining **PriceHawk** on Docker, Docker Compose, Railway, and Supabase.

---

## Table of Contents

1. [Production Architecture Topology](#1-production-architecture-topology)
2. [Containerization & Docker Specification](#2-containerization--docker-specification)
3. [Multi-Container Orchestration (`docker-compose.yml`)](#3-multi-container-orchestration-docker-composeyml)
4. [Railway Production Deployment Runbook](#4-railway-production-deployment-runbook)
5. [Supabase Integration & Database Setup](#5-supabase-integration--database-setup)
6. [Production Environment Variable Checklist](#6-production-environment-variable-checklist)
7. [Operational Maintenance, Scaling & Observability](#7-operational-maintenance-scaling--observability)

---

## 1. Production Architecture Topology

```
                                  INTERNET / USERS
                                         │
                                         ▼ (HTTPS / TLS 1.3)
                       ┌───────────────────────────────────┐
                       │     Railway Public Ingress        │
                       └─────────────────┬─────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PriceHawk Railway Project                             │
│                                                                                 │
│   ┌────────────────────────┐  Private Network  ┌────────────────────────────┐  │
│   │   FastAPI Web Service  │──────────────────►│   Redis Broker & Backend   │  │
│   │   (Uvicorn on $PORT)   │                   │   (Managed Redis Instance) │  │
│   └───────────┬────────────┘                   └─────────────┬──────────────┘  │
│               │                                              │                  │
│               │                                              │ Celery Queues    │
│               │                                              ▼                  │
│               │                                ┌────────────────────────────┐  │
│               │                                │    Celery Worker Service   │  │
│               │                                │    (Headless Scrapers)     │  │
│               │                                └─────────────┬──────────────┘  │
│               │                                              │                  │
│               └──────────────────────┬───────────────────────┘                  │
│                                      │                                          │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
                         PostgreSQL / Auth / REST APIs
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │    Supabase Managed Cloud DB      │
                     │    PostgreSQL 15+ & RLS Policies  │
                     └───────────────────────────────────┘
```

---

## 2. Containerization & Docker Specification

PriceHawk uses a multi-stage `Dockerfile` to produce minimal, secure, non-root container images with pre-configured Playwright Chromium binaries:

```dockerfile
# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1: Builder
# ============================================================================
FROM python:3.13-slim AS builder

WORKDIR /app

# Install uv for high-speed package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency specifications
COPY pyproject.toml uv.lock* ./

# Synchronize production dependencies into /app/.venv
RUN uv sync --frozen --no-dev

# ============================================================================
# Stage 2: Production Runtime
# ============================================================================
FROM python:3.13-slim AS production

WORKDIR /app

# Install native system libraries required by Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user
RUN groupadd --gid 1000 pricehawk && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home pricehawk

# Copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Environment paths and execution flags
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_BROWSERS_PATH=/home/pricehawk/.cache/ms-playwright

# Install Chromium browser binaries in the non-root home directory
RUN mkdir -p /home/pricehawk/.cache && \
    playwright install chromium && \
    chown -R pricehawk:pricehawk /home/pricehawk/.cache

# Copy application source code
COPY --chown=pricehawk:pricehawk . .

# Switch to non-root execution
USER pricehawk

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 3. Multi-Container Orchestration (`docker-compose.yml`)

The root `docker-compose.yml` orchestrates the complete production service topology:

```yaml
services:
  # 1. FastAPI Ingress Application
  api:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  # 2. Redis Message Broker & State Store
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  # 3. Celery Background Worker
  celery-worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: celery -A app.tasks.celery_app worker --loglevel=info
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

  # 4. Celery Beat Periodic Scheduler
  celery-beat:
    build:
      context: .
      dockerfile: Dockerfile
    command: celery -A app.tasks.celery_app beat --loglevel=info
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

volumes:
  redis-data:
```

### Running with Docker Compose

```bash
# Start all services in detached mode
docker compose up -d

# Stream logs across all services
docker compose logs -f

# Check service status
docker compose ps

# Teardown services and volumes
docker compose down -v
```

---

## 4. Railway Production Deployment Runbook

### Step 1: Create a Railway Project
1. Log into [Railway](https://railway.app).
2. Click **New Project** ➔ **Deploy from GitHub repo**.
3. Select your private repository (`dreww01/pricehawk`).

### Step 2: Add Redis Database
1. In the project canvas, click **New** (or press `Ctrl+K`).
2. Select **Database** ➔ **Redis**.
3. Railway provisions a managed Redis instance on private networking.

### Step 3: Configure the Web Service
1. Select the provisioned application service.
2. In **Settings** ➔ **Networking**, click **Generate Domain** to provision public TLS routing.
3. In **Variables**, link Redis and populate secrets (see Section 6).

### Step 4: Deploy Celery Worker Service
1. Click **New** ➔ **GitHub Repo** ➔ select `pricehawk` repository again.
2. Name the service `celery-worker`.
3. Go to **Settings** ➔ **Deploy** ➔ **Custom Start Command**:
   ```bash
   celery -A app.tasks.celery_app worker --loglevel=info
   ```
4. Add the exact same environment variables as the Web Service.

### Step 5: Deploy Celery Beat Scheduler (Optional Dedicated Service)
1. Repeat Step 4 to create a service named `celery-beat`.
2. Set the custom start command to:
   ```bash
   celery -A app.tasks.celery_app beat --loglevel=info
   ```

---

## 5. Supabase Integration & Database Setup

### Step 1: Provision Supabase Project
1. Create a project on [Supabase](https://supabase.com).
2. Note your **Project URL** (`https://<project-ref>.supabase.co`).
3. Under **Project Settings** ➔ **API**, extract:
   - `anon` `public` key (`SB_ANON_KEY`)
   - `service_role` `secret` key (`SB_SERVICE_KEY`)
   - `JWT Secret` (`SB_JWT_SECRET`)

### Step 2: Initialize Database Schema & RLS
1. Navigate to the **SQL Editor** in the Supabase Dashboard.
2. Open [`docs/database_schema.sql`](database_schema.sql).
3. Paste and click **Run** to execute the idempotent migration:
   - Creates all tables (`products`, `competitors`, `price_history`, `insights`, `tracking_jobs`, `pending_alerts`, `user_alert_settings`, `alert_history`).
   - Configures B-Tree indexes for high-frequency queries.
   - Attaches `update_updated_at()` trigger.
   - Activates Row-Level Security (RLS) policies.

---

## 6. Production Environment Variable Checklist

Configure these variables in your deployment environment or `.env` file:

| Variable Name | Required | Example / Format | Purpose |
|---|---|---|---|
| `SB_URL` | **Yes** | `https://xyzproject.supabase.co` | Supabase API URL and JWKS key endpoint. |
| `SB_ANON_KEY` | **Yes** | `eyJhbGciOi...` | Supabase anonymous public key for RLS queries. |
| `SB_SERVICE_KEY` | **Yes** | `eyJhbGciOi...` | Supabase service role secret key for worker bypass. |
| `SB_JWT_SECRET` | **Yes** | `your-supabase-jwt-secret-string` | Symmetric fallback JWT signing secret. |
| `REDIS_URL` | **Yes** | `redis://default:pass@host:port/0` | Celery message broker and result backend URL. |
| `GROQ_API_KEY` | Optional | `gsk_...` | Groq API key for Llama 3.3 70B price intelligence. |
| `SMTP_HOST` | Optional | `smtp.resend.com` | SMTP relay hostname for price digest delivery. |
| `SMTP_PORT` | Optional | `587` | SMTP port (TLS). |
| `SMTP_USERNAME` | Optional | `resend` | SMTP authentication username. |
| `SMTP_PASSWORD` | Optional | `re_123456789...` | SMTP authentication API key or password. |
| `FROM_EMAIL` | Optional | `alerts@yourdomain.com` | Sender email address for price change digests. |
| `DEBUG` | **Yes** | `false` | Disable Swagger sandbox mode and enable strict CORS/HSTS. |
| `MAX_PRODUCTS_FETCH` | Optional | `500` | Ceiling limit for store catalog discovery queries. |

---

## 7. Operational Maintenance, Scaling & Observability

### 7.1 Health & Liveness Probes

- **HTTP API Liveness**: `GET /api/health` ➔ Returns `{"status": "healthy"}` (HTTP 200).
- **Celery Worker Liveness**: `GET /api/scraper/scrape/worker-health` ➔ Queries broker heartbeat.

### 7.2 Scaling Strategies

1. **API Gateway (Stateless)**: Scale horizontally by increasing web replica instances behind a reverse proxy or load balancer.
2. **Celery Scraping Workers**: Scale worker instances horizontally when monitoring thousands of URLs:
   ```bash
   celery -A app.tasks.celery_app worker --concurrency=8 --max-tasks-per-child=200
   ```
   *Note: `--max-tasks-per-child` prevents memory leaks in long-running browser automation processes.*

### 7.3 Secret Rotation Protocol

1. When rotating `SB_SERVICE_KEY` or `SB_JWT_SECRET`, update secrets in Railway environment variables.
2. Trigger a synchronized redeploy of both the Web Service and Celery Workers to ensure token validation remains harmonious.
