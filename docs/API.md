# PriceHawk REST API Reference

## Navigation

- [Base URLs and Auth](#base-urls-and-auth)
- [Error Model](#error-model)
- [Endpoints](#endpoints)
- [Schemas](#schemas)

## Base URLs and Auth

- Local API: `http://localhost:8000`
- OpenAPI JSON: `/api/openapi.json`
- Swagger UI: `/api/docs`
- ReDoc: `/api/redoc`

Protected endpoints require a Supabase access token:

```http
Authorization: Bearer <access_token>
```

`GET /api/health` and HTML page routes are public. `GET /api/export/{product_id}/csv` accepts either a bearer token or the browser `access_token` cookie to support direct downloads.

## Error Model

FastAPI and route handlers return JSON error payloads.

| Status | Meaning | Typical body |
| --- | --- | --- |
| `400` | Invalid request or rejected domain operation | `{"detail":"No fields to update"}` |
| `401` | Missing, expired, or invalid authentication | `{"detail":"Not authenticated"}` |
| `403` | Authenticated but not authorized for the resource/action | `{"detail":"Not authorized to modify this competitor"}` |
| `404` | Resource not found or intentionally hidden by ownership checks | `{"detail":"Product not found"}` |
| `429` | slowapi or upstream auth reset throttling | `{"detail":"Rate limit exceeded"}` |
| `500` | Unexpected service failure | `{"detail":"An unexpected error occurred. Please try again.","error_id":"..."}` |

Validation errors use FastAPI's standard `422 Unprocessable Entity` response.

## Endpoints

### Health

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/health` | No | Returns service health. |

Response:

```json
{"status":"healthy"}
```

### Authentication

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/api/auth/login` | No | Sign in with Supabase email/password. |
| `POST` | `/api/auth/signup` | No | Create a new Supabase account. |
| `POST` | `/api/auth/forgot-password` | No | Send recovery OTP/email through Supabase. |
| `POST` | `/api/auth/verify-reset-otp` | No | Verify recovery OTP and return reset token. |
| `POST` | `/api/auth/reset-password` | Reset token | Update password using verified recovery token. |
| `GET` | `/api/auth/me` | Bearer | Return current user identity. |

`POST /api/auth/login`

```json
{"email":"user@example.com","password":"correct-horse-battery-staple"}
```

```json
{
  "access_token": "jwt",
  "token_type": "bearer",
  "user_id": "uuid",
  "email": "user@example.com"
}
```

`POST /api/auth/signup`

```json
{"email":"user@example.com","password":"correct-horse-battery-staple"}
```

```json
{
  "message": "Account created successfully",
  "user_id": "uuid",
  "email": "user@example.com",
  "email_confirmed": false
}
```

### Account

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/account/settings` | Bearer | Return current user ID and email. |
| `POST` | `/api/account/change-password` | Bearer | Update Supabase password. |
| `POST` | `/api/account/change-email` | Bearer | Request Supabase email change verification. |
| `DELETE` | `/api/account/delete` | Bearer | Delete user-owned application data. |

### Store Discovery and Tracking

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/api/stores/discover` | Bearer | Detect platform and return normalized products. |
| `POST` | `/api/stores/track` | Bearer | Create a product group and competitors from selected products. |

`POST /api/stores/discover`

```json
{
  "url": "https://example.myshopify.com",
  "keyword": "laptop",
  "limit": 20
}
```

```json
{
  "platform": "shopify",
  "store_url": "https://example.myshopify.com",
  "total_found": 1,
  "products": [
    {
      "name": "Laptop Pro",
      "price": "1299.00",
      "currency": "USD",
      "image_url": "https://example/image.jpg",
      "product_url": "https://example.myshopify.com/products/laptop-pro",
      "platform": "shopify",
      "variant_id": "123",
      "sku": "LP-1",
      "in_stock": true
    }
  ],
  "error": null
}
```

`POST /api/stores/track`

```json
{
  "group_name": "Laptop Market",
  "products": [
    {"url":"https://example.myshopify.com/products/laptop-pro","price":"1299.00","currency":"USD"}
  ],
  "alert_threshold_percent": "10.00"
}
```

```json
{"group_id":"uuid","group_name":"Laptop Market","products_added":1,"prices_stored":1}
```

### Tracked Products

Mounted by `app/api/routes/tracked_products.py` under `/api/tracked-products`.

| Method | Path | Query/body | Description |
| --- | --- | --- | --- |
| `GET` | `/api/tracked-products` | None | List active product groups for current user. |
| `GET` | `/api/tracked-products/{product_id}` | Path `product_id` | Get one product group with competitors. |
| `PUT` | `/api/tracked-products/{product_id}` | `ProductUpdate` | Update `product_name` and/or `is_active`. |
| `DELETE` | `/api/tracked-products/{product_id}` | Path `product_id` | Soft delete by setting `is_active=false`. |

Product list response:

```json
{
  "products": [
    {
      "id": "uuid",
      "product_name": "Laptop Market",
      "is_active": true,
      "created_at": "2025-01-01T00:00:00Z",
      "updated_at": "2025-01-01T00:00:00Z",
      "competitors": [
        {"id":"uuid","url":"https://store/product","retailer_name":"store","alert_threshold_percent":"10.00","created_at":"2025-01-01T00:00:00Z"}
      ]
    }
  ],
  "total": 1
}
```

### Scraping, Price History, and Charts

Mounted by `app/api/routes/scraper.py` under `/api/scraper`.

| Method | Path | Query/body | Description |
| --- | --- | --- | --- |
| `POST` | `/api/scraper/scrape/manual/{product_id}` | Path `product_id` | Queue asynchronous manual scrape; returns `202`. |
| `GET` | `/api/scraper/scrape/stream/{task_id}` | Path `task_id` | Server-Sent Events scrape progress stream. |
| `GET` | `/api/scraper/prices/{product_id}/history` | `limit`, `offset` | Price history for all competitors in a product group. |
| `GET` | `/api/scraper/prices/latest/{competitor_id}` | Path `competitor_id` | Latest price snapshot for one competitor. |
| `GET` | `/api/scraper/prices/{product_id}/chart-data` | `days` | Chart-ready time series and aggregate stats. |
| `GET` | `/api/scraper/scrape/worker-health` | None | Celery worker ping/active task summary. |

Manual scrape response:

```json
{"task_id":"celery-task-id","status":"queued","message":"Scraping 3 competitors"}
```

SSE event example:

```text
data: {"status":"scraping","completed":2,"total":3,"current":"store.example"}
```

Price history response:

```json
{
  "prices": [
    {"id":"uuid","competitor_id":"uuid","price":"1299.00","currency":"USD","scraped_at":"2025-01-01T00:00:00Z","scrape_status":"success","error_message":null}
  ],
  "total": 1
}
```

### Charts

| Method | Path | Query/body | Description |
| --- | --- | --- | --- |
| `GET` | `/api/charts/{product_id}` | `days` integer, 1-365, default 30 | Chart-ready price data for frontend visualization. |

### AI Insights

| Method | Path | Query/body | Description |
| --- | --- | --- | --- |
| `GET` | `/api/insights/{product_id}` | Path `product_id` | List persisted AI insights. |
| `POST` | `/api/insights/generate/{product_id}` | `{"force_regenerate": false}` | Generate and store fresh insights. |

Response:

```json
{
  "insights": [
    {"id":"uuid","product_id":"uuid","insight_text":"Competitor A is trending down.","insight_type":"pattern","confidence_score":"0.82","generated_at":"2025-01-01T00:00:00Z"}
  ],
  "total": 1
}
```

### Alerts

| Method | Path | Query/body | Description |
| --- | --- | --- | --- |
| `GET` | `/api/alerts/settings` | None | Get or create default user alert settings. |
| `PUT` | `/api/alerts/settings` | partial settings | Update alert settings. |
| `GET` | `/api/alerts/pending` | None | List pending, not-yet-digested alerts. |
| `GET` | `/api/alerts/history` | `limit`, default 20, max 100 | List digest delivery history. |
| `POST` | `/api/alerts/digests/run` | `{"force":false,"dry_run":false}` | Run the current user's digest and return channel/count totals. Dry runs never claim or clear alerts; `force=true` requires an admin/service-role JWT. |
| `POST` | `/api/alerts/test` | optional `email` | Send a test email. |
| `PATCH` | `/api/alerts/competitors/{competitor_id}/accept-currency` | `{"currency":"USD"}` | Accept detected currency for one competitor. |
| `POST` | `/api/alerts/accept-all-currencies` | None | Accept all pending currency changes for current user. |

Settings request:

```json
{"email_enabled":true,"digest_frequency_hours":12,"alert_price_drop":true,"alert_price_increase":false,"webhook_enabled":true,"webhook_url":"https://hooks.example.com/pricehawk","webhook_secret":"replace-with-at-least-16-characters"}
```

Webhook deliveries use canonical JSON and include `X-PriceHawk-Timestamp` plus
`X-PriceHawk-Signature: sha256=<hex>`. Consumers should compute HMAC-SHA256 over
`<timestamp>.<raw request body>` with their configured secret, compare signatures
in constant time, and reject stale timestamps. Redirects, credential-bearing URLs,
and destinations resolving to private/reserved networks are rejected.

### Export

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/api/export/{product_id}/csv` | Bearer or `access_token` cookie | Streams CSV with `Date,Time,Competitor,Price,Currency,Status,Error`. |

### Dashboard and HTML Pages

HTML routes render Jinja2 templates at `/login`, `/signup`, `/forgot-password`, `/verify-reset-code`, `/reset-password`, `/dashboard`, `/tracked`, `/tracked/{product_id}`, `/discover`, `/insights`, `/alerts/settings`, `/account/settings`, and `/logout`.

Dashboard JSON helpers are mounted by the pages router at absolute paths:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/dashboard/stats` | Aggregated counts for dashboard cards. |
| `GET` | `/api/dashboard/activity` | Recent price/alert activity. |
| `GET` | `/api/dashboard/products` | Recent products for dashboard display. |
| `GET` | `/api/insights` | Dashboard-level insight summary endpoint. |

## Schemas

Canonical request/response models live in [`../app/db/models.py`](../app/db/models.py) and route-local auth/account models live in their route modules. The OpenAPI document at `/api/openapi.json` is the source of truth for machine-readable schemas.
