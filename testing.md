# PriceHawk API Testing Guide

## Prerequisites

1. **Start the API server:**
   ```bash
   python run.py
   ```
   Server runs at: `http://127.0.0.1:5000`

2. **Get Supabase JWT token:**
   - Sign up/login via Supabase Auth
   - Get the access token from the session

3. **API Documentation:**
   - Swagger UI: `http://127.0.0.1:5000/api/docs`
   - ReDoc: `http://127.0.0.1:5000/api/redoc`

---

## API Endpoints

### 1. Health Check

```http
GET /api/health
```

**Expected Response:**
```json
{
  "status": "healthy"
}
```

---

### 2. Store Discovery

Discover products from any supported store.

```http
POST /api/stores/discover
Authorization: Bearer <your-jwt-token>
Content-Type: application/json

{
  "url": "https://example-store.myshopify.com",
  "keyword": "laptop",
  "limit": 20
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| url | string | Yes | Store URL (HTTPS only) |
| keyword | string | No | Filter products by keyword |
| limit | int | No | Max products to return (1-250, default 50) |

**Supported Platforms:**
- Shopify stores (detects via `/products.json`)
- WooCommerce stores (detects via `/wp-json/wc/store/products`)
- Amazon store/search pages (URLs with `/stores/`, `/s?`, `/brand/`)
- eBay store/search pages (URLs with `/str/`, `/sch/`)
- Custom stores (generic HTML scraping)

**Expected Response:**
```json
{
  "platform": "shopify",
  "store_url": "https://example-store.myshopify.com",
  "total_found": 15,
  "products": [
    {
      "name": "Product Name",
      "price": 29.99,
      "currency": "USD",
      "image_url": "https://...",
      "product_url": "https://...",
      "platform": "shopify",
      "variant_id": "12345",
      "sku": "SKU123",
      "in_stock": true
    }
  ],
  "error": null
}
```

---

### 3. Track Products

Add discovered products to tracking for price monitoring.

```http
POST /api/stores/track
Authorization: Bearer <your-jwt-token>
Content-Type: application/json

{
  "group_name": "My Laptops",
  "product_urls": [
    "https://example.com/products/laptop-1",
    "https://example.com/products/laptop-2"
  ],
  "alert_threshold_percent": 10
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| group_name | string | Yes | Name for the tracking group |
| product_urls | array | Yes | URLs to track (1-50) |
| alert_threshold_percent | decimal | No | Alert threshold (default 10%) |

**Expected Response:**
```json
{
  "group_id": "uuid",
  "group_name": "My Laptops",
  "products_added": 2
}
```

---

### 4. List Tracked Products

```http
GET /api/products
Authorization: Bearer <your-jwt-token>
```

**Expected Response:**
```json
{
  "products": [
    {
      "id": "uuid",
      "product_name": "My Laptops",
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z",
      "competitors": [
        {
          "id": "uuid",
          "url": "https://...",
          "retailer_name": null,
          "alert_threshold_percent": 10,
          "created_at": "2024-01-01T00:00:00Z"
        }
      ]
    }
  ],
  "total": 1
}
```

---

### 5. Get Single Product

```http
GET /api/products/{product_id}
Authorization: Bearer <your-jwt-token>
```

---

### 6. Update Product

```http
PUT /api/products/{product_id}
Authorization: Bearer <your-jwt-token>
Content-Type: application/json

{
  "product_name": "Updated Name",
  "is_active": false
}
```

---

### 7. Delete Product (Soft Delete)

```http
DELETE /api/products/{product_id}
Authorization: Bearer <your-jwt-token>
```

**Response:** 204 No Content

---

### 8. Manual Scrape

Trigger immediate price scrape for all competitors of a product.

```http
POST /api/scrape/manual/{product_id}
Authorization: Bearer <your-jwt-token>
```

**Expected Response:**
```json
[
  {
    "competitor_id": "uuid",
    "competitor_url": "https://...",
    "price": 299.99,
    "currency": "USD",
    "status": "success",
    "error_message": null
  }
]
```

---

### 9. Get Price History

```http
GET /api/prices/{product_id}/history?limit=100&offset=0
Authorization: Bearer <your-jwt-token>
```

**Expected Response:**
```json
{
  "prices": [
    {
      "id": "uuid",
      "competitor_id": "uuid",
      "price": 299.99,
      "currency": "USD",
      "scraped_at": "2024-01-01T02:00:00Z",
      "scrape_status": "success",
      "error_message": null
    }
  ],
  "total": 10
}
```

---

### 10. Get Latest Price

```http
GET /api/prices/latest/{competitor_id}
Authorization: Bearer <your-jwt-token>
```

---

### 11. Worker Health Check

```http
GET /api/scrape/worker-health
```

**Expected Response:**
```json
{
  "worker_status": "healthy",
  "ping_response": "['celery@hostname']",
  "active_tasks": 0,
  "error": null
}
```

---

## Testing with cURL

### Discover Products (Shopify)
```bash
curl -X POST http://127.0.0.1:5000/api/stores/discover \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://shop.example.com", "limit": 10}'
```

### Track Products
```bash
curl -X POST http://127.0.0.1:5000/api/stores/track \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "group_name": "My Products",
    "product_urls": ["https://example.com/product1"],
    "alert_threshold_percent": 15
  }'
```

### List Products
```bash
curl http://127.0.0.1:5000/api/products \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Running Automated Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all tests
pytest tests/test_api.py -v

# Run specific test class
pytest tests/test_api.py::TestStoreDiscovery -v

# Run with coverage
pip install pytest-cov
pytest tests/test_api.py --cov=app --cov-report=term-missing
```

---

## Test Scenarios

### Store Discovery Flow
1. POST `/api/stores/discover` with Shopify store URL
2. Verify platform detection returns "shopify"
3. Verify products array contains expected fields
4. Test keyword filtering

### Product Tracking Flow
1. POST `/api/stores/track` with discovered product URLs
2. GET `/api/products` to verify group created
3. POST `/api/scrape/manual/{product_id}` to scrape prices
4. GET `/api/prices/{product_id}/history` to verify price recorded

### Error Handling
1. Test 401 Unauthorized without token
2. Test 404 Not Found with invalid product_id
3. Test 422 Validation Error with invalid URL (HTTP instead of HTTPS)

---

## Background Tasks

### Start Celery Worker
```bash
celery -A app.tasks.celery_app worker -l info
```

### Start Celery Beat (Scheduler)
```bash
celery -A app.tasks.celery_app beat
```

The scheduler runs `scrape_all_products` daily at 2 AM UTC.

---

## Common Issues

### 1. "Token has expired"
Get a fresh JWT token from Supabase.

### 2. "Domain not in whitelist"
The old scraper has a whitelist. Use the new `/stores/discover` endpoint which accepts any HTTPS URL.

### 3. "No workers responded to ping"
Start the Celery worker: `celery -A app.tasks.celery_app worker -l info`

### 4. Empty products array
- Check if the store URL is accessible
- For Shopify: URL must have `/products.json` available
- For Amazon/eBay: Use store/search page URLs, not product pages
