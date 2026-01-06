# How to Test the Scraper

## Before You Start

You need 3 things:

1. **Run the database setup** - Go to `supabase_setup.md`, find Milestone 3, copy the SQL and run it in Supabase
2. **Add your Webshare API key** to `.env` file:
   ```
   WEBSHARE_API_KEY=your_key_here
   ```
3. **Install the browser** (one-time):
   ```bash
   uv run playwright install chromium
   ```

---

## Method 1: Quick Test (No Server Needed)

Run this command to test if scraping works:

```bash
uv run python -c "
import asyncio
from app.services.scraper_service import scrape_url

async def test():
    result = await scrape_url('https://www.amazon.com/dp/B0CHX3QBCH')
    print(f'Price: {result.price} {result.currency}')
    print(f'Status: {result.status}')

asyncio.run(test())
"
```

You should see something like:
```
Price: 20.93 USD
Status: success
```

---

## Method 2: Test with Swagger UI (Recommended)

Swagger is a web page where you can test all API endpoints by clicking buttons.

### Step 1: Start the server

```bash
uv run uvicorn main:app --reload
```

### Step 2: Open Swagger

Go to: **http://localhost:8000/docs**

You'll see a list of all API endpoints.

### Step 3: Authenticate

1. Click the **"Authorize"** button (top right, lock icon)
2. Enter your access token in the format: `Bearer YOUR_TOKEN`
3. Click "Authorize"

> **How to get a token?** Use your Supabase project's auth endpoint or the login endpoint if you have one.

### Step 4: Create a product

1. Find **POST /api/products**
2. Click it, then click **"Try it out"**
3. Paste this JSON:
   ```json
   {
     "product_name": "Test Product",
     "competitors": [
       {
         "url": "https://www.amazon.com/dp/B0CHX3QBCH",
         "retailer_name": "Amazon"
       }
     ]
   }
   ```
4. Click **"Execute"**
5. Copy the `id` from the response

### Step 5: Scrape the product

1. Find **POST /api/scrape/manual/{product_id}**
2. Click "Try it out"
3. Paste your product ID
4. Click "Execute"
5. You should see the scraped price in the response

### Step 6: View price history

1. Find **GET /api/prices/{product_id}/history**
2. Enter the product ID
3. Click "Execute"
4. See all scraped prices

---

## Method 3: Test with Postman

Postman is a desktop app for testing APIs with a nice visual interface.

### Step 1: Start the server

```bash
uv run uvicorn main:app --reload
```

### Step 2: Set up authentication

1. Open Postman
2. Click **"New"** → **"Request"**
3. Go to the **"Authorization"** tab
4. Type: **Bearer Token**
5. Paste your access token in the Token field

### Step 3: Create a product

1. Set method to **POST**
2. URL: `http://localhost:8000/api/products`
3. Go to **"Body"** tab → select **"raw"** → choose **"JSON"**
4. Paste:
   ```json
   {
     "product_name": "Test Product",
     "competitors": [
       {
         "url": "https://www.amazon.com/dp/B0CHX3QBCH",
         "retailer_name": "Amazon"
       }
     ]
   }
   ```
5. Click **"Send"**
6. Copy the `id` from the response

### Step 4: Scrape the product

1. Create a new request
2. Set method to **POST**
3. URL: `http://localhost:8000/api/scrape/manual/YOUR_PRODUCT_ID`
4. Add the same Bearer Token auth
5. Click **"Send"**
6. You'll see the scraped price in the response

### Step 5: View price history

1. Create a new request
2. Set method to **GET**
3. URL: `http://localhost:8000/api/prices/YOUR_PRODUCT_ID/history`
4. Add Bearer Token auth
5. Click **"Send"**

> **Tip:** Save these requests in a Postman Collection so you don't have to set them up again.

---

## Method 4: Test with curl (Command Line)

### Start server first
```bash
uv run uvicorn main:app --reload
```

### Create a product
```bash
curl -X POST "http://localhost:8000/api/products" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"product_name": "Test", "competitors": [{"url": "https://www.amazon.com/dp/B0CHX3QBCH", "retailer_name": "Amazon"}]}'
```

### Scrape it
```bash
curl -X POST "http://localhost:8000/api/scrape/manual/PRODUCT_ID" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Test URLs You Can Use

| Retailer | Example URL |
|----------|-------------|
| Amazon   | `https://www.amazon.com/dp/B0CHX3QBCH` |
| Amazon   | `https://www.amazon.com/dp/B0BSHF7WHW` |
| eBay     | Find any product listing on ebay.com |
| Walmart  | Find any product on walmart.com |

---

## Common Problems

### "Could not extract price"
- The product page might not exist
- Check if the URL shows a price when you visit it in a browser

### Proxy errors (timeout, 402)
- Check if you have credits left on Webshare
- Make sure your API key is correct in `.env`

### "Domain not in whitelist"
- We only support Amazon, eBay, Walmart
- Other sites won't work unless you add them to the code

---

## Testing Background Tasks (Celery + Redis)

Background tasks run price scraping automatically every day. Here's how to test them.

### Step 1: Start Redis with Docker

1. Open **Docker Desktop** and make sure it's running
2. Open a terminal and run:
   ```bash
   docker run -d --name redis -p 6379:6379 redis
   ```
3. Verify it's running:
   ```bash
   docker ps
   ```
   You should see a container named `redis` with port `6379`

**To stop Redis later:**
```bash
docker stop redis
```

**To start it again:**
```bash
docker start redis
```

---

### Step 2: Start the Celery Worker

Open a **new terminal** (keep Redis running) and run:

```bash
cd c:\Users\User\Desktop\pricehawk
uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

> **Note:** `--pool=solo` is needed on Windows

You should see:
```
[config]
.> app:         pricehawk:...
.> transport:   redis://localhost:6379/0
...
[INFO] celery@... ready.
```

Keep this terminal open - it processes background tasks.

---

### Step 3: Test a Background Task Manually

Open a **third terminal** and run:

```bash
cd c:\Users\User\Desktop\pricehawk
uv run python -c "
from app.tasks.scraper_tasks import check_worker_health
result = check_worker_health.delay()
print('Task sent! ID:', result.id)
print('Waiting for result...')
print('Result:', result.get(timeout=10))
"
```

You should see:
```
Task sent! ID: some-uuid-here
Waiting for result...
Result: {'status': 'healthy', 'timestamp': '2026-01-03T...'}
```

If this works, your Celery + Redis setup is correct!

---

### Step 4: Test the Worker Health Endpoint

With all services running (Redis, Celery worker, API server), test via API:

```bash
curl http://localhost:8000/api/scrape/worker-health
```

Expected response:
```json
{
  "worker_status": "healthy",
  "ping_response": "['celery@your-computer']",
  "active_tasks": 0
}
```

---

### Step 5: Test Scraping via Background Task

This queues a scrape task instead of running it directly:

```bash
cd c:\Users\User\Desktop\pricehawk
uv run python -c "
from app.tasks.scraper_tasks import scrape_single_competitor

# Replace with a real competitor_id from your database
competitor_id = 'YOUR_COMPETITOR_ID'
url = 'https://www.amazon.com/dp/B0CHX3QBCH'

result = scrape_single_competitor.delay(competitor_id, url)
print('Task queued! ID:', result.id)
print('Check the worker terminal to see it processing...')
"
```

Watch the Celery worker terminal - you'll see the task being processed.

---

### Step 6: Start the Scheduler (Celery Beat)

For automatic daily scraping, you need Celery Beat running.

Open a **fourth terminal**:

```bash
cd c:\Users\User\Desktop\pricehawk
uv run celery -A app.tasks.celery_app beat --loglevel=info
```

This schedules `scrape_all_products` to run daily at 2 AM UTC.

---

## Summary: All Services

| Terminal | Command | Purpose |
|----------|---------|---------|
| 1 | `docker start redis` | Message broker |
| 2 | `uv run celery -A app.tasks.celery_app worker --loglevel=info --pool=solo` | Process tasks |
| 3 | `uv run celery -A app.tasks.celery_app beat --loglevel=info` | Schedule daily tasks |
| 4 | `uv run uvicorn main:app --reload` | API server |

---

## Common Problems

### "Cannot connect to redis://localhost:6379"
- Redis is not running
- Run: `docker start redis` or `docker run -d --name redis -p 6379:6379 redis`

### Worker starts but no tasks run
- Make sure you're using `--pool=solo` on Windows
- Check if Redis is accessible: `docker ps` should show redis container

### "Task not found" error
- Celery worker can't find the task module
- Make sure you're in the project directory when starting the worker

### Beat scheduler not triggering
- It only triggers at 2 AM UTC
- For testing, manually call: `scrape_all_products.delay()`
