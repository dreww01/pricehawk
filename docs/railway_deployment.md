# Railway Deployment Guide for PriceHawk

This guide walks you through deploying PriceHawk (FastAPI + Redis + Celery) on Railway.

---

## What You'll Deploy

| Service | Purpose |
|---------|---------|
| **FastAPI** | Main web app (API + frontend) |
| **Redis** | Message broker for Celery |
| **Celery Worker** | Processes background scraping tasks |

---

## Prerequisites

Before starting:
1. Create a [Railway account](https://railway.app) (free tier gives $5/month credit)
2. Have a GitHub account
3. Push your PriceHawk code to GitHub

---

## Step 1: Create a New Railway Project

1. Go to [railway.app](https://railway.app)
2. Click **"Start a New Project"**
3. Select **"Deploy from GitHub repo"**
4. Connect your GitHub account (if not already connected)
5. Find and select your `pricehawk` repository
6. Railway will detect it's a Python project

---

## Step 2: Add Redis to Your Project

1. In your Railway project dashboard, click the **"+ New"** button (or press `Ctrl+K`)
2. Select **"Database"** → **"Redis"**
3. Railway will spin up a Redis instance automatically
4. Wait for it to show a green "Running" status

---

## Step 3: Create Required Files

### 3.1 Create `Procfile` in your project root

This tells Railway what services to run.

```procfile
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: celery -A app.tasks.celery_app worker --loglevel=info
```

### 3.2 Create `runtime.txt` in your project root

This tells Railway which Python version to use.

```
python-3.13.0
```

### 3.3 Create `requirements.txt` (if you don't have one)

Railway needs this file. Export from your pyproject.toml:

```bash
# Run this locally
pip freeze > requirements.txt
```

Or manually create it with your dependencies.

---

## Step 4: Set Up Environment Variables

1. In Railway dashboard, click on your **FastAPI service**
2. Go to the **"Variables"** tab
3. Add these variables:

### Required Variables

| Variable | Value | Where to get it |
|----------|-------|-----------------|
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Click "Add Reference" → Select Redis |
| `SUPABASE_URL` | Your Supabase URL | From Supabase dashboard |
| `SUPABASE_KEY` | Your Supabase anon key | From Supabase dashboard |
| `SECRET_KEY` | Random string | Generate one (see below) |
| `GROQ_API_KEY` | Your Groq API key | From Groq console |

### Generate a Secret Key

Run this in Python:
```python
import secrets
print(secrets.token_hex(32))
```

### How to Reference Redis URL

1. Click **"+ Add Variable"**
2. For the name, type `REDIS_URL`
3. For the value, click **"Add Reference"**
4. Select **Redis** → **REDIS_URL**
5. It will show as `${{Redis.REDIS_URL}}`

---

## Step 5: Deploy the Celery Worker as a Separate Service

Railway runs each Procfile entry as a separate service. You need to tell it to run the worker.

1. In your project, click **"+ New"** → **"GitHub Repo"**
2. Select the same `pricehawk` repository again
3. Click on this new service
4. Go to **"Settings"** tab
5. Under **"Start Command"**, enter:
   ```
   celery -A app.tasks.celery_app worker --loglevel=info
   ```
6. Go to **"Variables"** tab
7. Add the same `REDIS_URL` reference as Step 4

---

## Step 6: Update Your Code for Railway

### 6.1 Update Celery Configuration

Make sure your Celery app reads from environment variables. In your `app/tasks/celery_app.py` or similar:

```python
import os
from celery import Celery

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "pricehawk",
    broker=redis_url,
    backend=redis_url
)
```

### 6.2 Update FastAPI to Use Dynamic Port

Railway assigns a random port via `$PORT`. In your `app/main.py`:

```python
# Railway provides PORT env variable
# uvicorn reads it from the Procfile command
```

No code change needed if you use the Procfile command above.

---

## Step 7: Deploy

1. Commit and push your changes to GitHub:
   ```bash
   git add .
   git commit -m "Add Railway deployment config"
   git push
   ```

2. Railway will automatically detect the push and start deploying
3. Watch the build logs in Railway dashboard
4. Wait for green "Running" status on all services

---

## Step 8: Get Your Public URL

1. Click on your **FastAPI service** in Railway
2. Go to **"Settings"** tab
3. Under **"Networking"** → **"Public Networking"**
4. Click **"Generate Domain"**
5. You'll get a URL like `pricehawk-production.up.railway.app`

---

## Step 9: Verify Everything Works

1. Visit your public URL in a browser
2. Check the API docs at `https://your-url.railway.app/docs`
3. Check Railway logs for any errors:
   - Click on a service
   - Go to **"Logs"** tab

---

## Troubleshooting

### "Redis connection refused"

**Problem**: Your app can't connect to Redis.

**Fix**:
- Make sure `REDIS_URL` variable is set correctly
- Check it references `${{Redis.REDIS_URL}}`
- Restart the service after adding variables

### "Module not found" errors

**Problem**: Missing dependencies.

**Fix**:
- Make sure `requirements.txt` includes all packages
- Check the build logs for what's missing
- Add missing packages and push again

### "Port already in use"

**Problem**: Hardcoded port conflict.

**Fix**:
- Don't hardcode ports. Use `$PORT` environment variable
- Use the Procfile command: `--port $PORT`

### Celery worker not processing tasks

**Problem**: Worker service isn't running.

**Fix**:
- Check worker service has green "Running" status
- Verify `REDIS_URL` is set in worker service variables
- Check worker logs for errors

### Build fails on Playwright

**Problem**: Playwright needs browser binaries.

**Fix**: Add a `Dockerfile` instead (see Advanced section below).

---

## Using Dockerfile (for Playwright support)

PriceHawk includes a `Dockerfile` with Playwright and Chromium pre-configured:

```dockerfile
# syntax=docker/dockerfile:1

# Build stage
FROM python:3.13-slim AS builder

WORKDIR /app

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.13-slim AS production

WORKDIR /app

# Install Playwright system dependencies for Chromium
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

# Create non-root user for security
RUN groupadd --gid 1000 pricehawk && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home pricehawk

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_BROWSERS_PATH=/home/pricehawk/.cache/ms-playwright

# Install Playwright Chromium browser
RUN mkdir -p /home/pricehawk/.cache && \
    playwright install chromium && \
    chown -R pricehawk:pricehawk /home/pricehawk/.cache

# Copy application code
COPY --chown=pricehawk:pricehawk . .

# Switch to non-root user
USER pricehawk

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Deploying with Dockerfile on Railway

Railway auto-detects the Dockerfile. For the **web service**, no extra config needed.

For the **Celery worker service**:
1. In Railway, add a new service from the same GitHub repo
2. Go to **Settings** → **Deploy** → **Custom Start Command**:
   ```
   celery -A app.tasks.celery_app worker --loglevel=info
   ```
3. Add the same environment variables (`REDIS_URL`, etc.)

---

## Project Structure After Setup

```
pricehawk/
├── app/
│   ├── main.py
│   ├── tasks/
│   │   └── celery_app.py
│   └── ...
├── Procfile              # NEW
├── runtime.txt           # NEW
├── requirements.txt      # NEW (or updated)
├── Dockerfile            # OPTIONAL (for Playwright)
├── Dockerfile.worker     # OPTIONAL (for Playwright)
└── ...
```

---

## Cost Estimate

Railway Hobby plan ($5/month credit):
- **FastAPI**: ~$2-3/month (depending on usage)
- **Redis**: ~$1-2/month
- **Celery Worker**: ~$2-3/month

**Total**: Usually fits within free tier for light usage.

---

## Quick Reference Commands

```bash
# Install Railway CLI (optional)
npm install -g @railway/cli

# Login to Railway
railway login

# Link to existing project
railway link

# Deploy from CLI
railway up

# View logs
railway logs

# Open project in browser
railway open
```

---

## Useful Links

- [Railway Dashboard](https://railway.app/dashboard)
- [Railway FastAPI Guide](https://docs.railway.com/guides/fastapi)
- [Railway Redis Guide](https://docs.railway.com/guides/redis)
- [Railway Private Networking](https://docs.railway.com/guides/private-networking)
