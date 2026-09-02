# REST API & Endpoint Reference

## Overview

The PriceHawk REST API provides programmatic access to automated competitor discovery, price tracking, distributed background scraping, Groq AI insights, and alert dispatch management.

All API routes (with the exception of `/api/health`, public authentication, and public web pages) require an authenticated **Bearer JWT Token** issued by Supabase Auth.

- **Base URL**: `http://localhost:8000` (Dev) / `https://your-domain.railway.app` (Prod)
- **Interactive OpenAPI Documentation**: `/api/docs` (Swagger UI) and `/api/redoc` (ReDoc)
- **OpenAPI Schema Definition**: `/api/openapi.json`

---

## Global Standards & Error Model

### Standard Request Headers
```http
Authorization: Bearer <supabase_jwt_access_token>
Content-Type: application/json
Accept: application/json
```

### Rate Limiting Limits (`slowapi`)
The API enforces rate limits on client IP addresses. When exceeded, the API responds with HTTP 429:

| Scope | Limit | Header / Details |
|---|---|---|
| **Authentication Endpoints** (`/api/auth/*`) | `5 requests / minute` | Mitigates brute-force credential stuffing |
| **Scraper Triggers** (`/api/scraper/scrape/*`) | `10 requests / minute` | Protects background queue capacity |
| **Standard API Endpoints** | `100 requests / minute` | General API abuse protection |

### Standard HTTP Status & Error Codes

| Status Code | Description | Response Model |
|---|---|---|
| **200 OK** | Successful retrieval, update, or execution | JSON Object / Array / Stream |
| **201 Created** | Entity successfully created | JSON Object |
| **400 Bad Request** | Business logic violation, invalid parameter | `{"detail": "Error message"}` |
| **401 Unauthorized** | Missing, invalid, or expired JWT Bearer token | `{"detail": "Invalid or expired token"}` |
| **403 Forbidden** | Authenticated user lacks permission for resource | `{"detail": "Access denied"}` |
| **404 Not Found** | Target resource ID does not exist or belongs to another tenant | `{"detail": "Product not found"}` |
| **422 Unprocessable Entity** | Pydantic schema validation error on input payload | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |
| **429 Too Many Requests** | Rate limit exceeded | `{"error": "Rate limit exceeded: 5 per 1 minute"}` |
| **500 Internal Server Error** | Unexpected server-side exception with masked diagnostic ID | `{"detail": "An unexpected error occurred...", "error_id": "a4df02e0"}` |

---

## 1. Authentication Endpoints (`/api/auth`)

### 1.1 User Login
Authenticates an existing user and returns Supabase session tokens.
- **Method / Route**: `POST /api/auth/login`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJFUzI1NiIs...",
  "token_type": "bearer",
  "user": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "user@example.com"
  }
}
```

---

### 1.2 User Registration
Registers a new user account in Supabase Identity.
- **Method / Route**: `POST /api/auth/signup`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "SecurePassword123!"
}
```

**Response (200 OK):**
```json
{
  "message": "Account created successfully. Please check your email to confirm your account."
}
```

---

### 1.3 Step 1: Request Password Reset OTP
Initiates a 3-step secure password reset process by dispatching a 6-digit OTP code to the user's email.
- **Method / Route**: `POST /api/auth/forgot-password`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

**Response (200 OK):**
```json
{
  "message": "Password reset code sent. Check your email."
}
```

---

### 1.4 Step 2: Verify Reset OTP Code
Verifies the 6-digit OTP token and issues a scoped `reset_token`.
- **Method / Route**: `POST /api/auth/verify-reset-otp`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "email": "user@example.com",
  "otp": "654321"
}
```

**Response (200 OK):**
```json
{
  "message": "Code verified successfully",
  "reset_token": "eyJhbGciOiJFUzI1NiIs..."
}
```

---

### 1.5 Step 3: Complete Password Reset
Consumes the `reset_token` and applies the new password.
- **Method / Route**: `POST /api/auth/reset-password`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "reset_token": "eyJhbGciOiJFUzI1NiIs...",
  "new_password": "BrandNewSecurePassword123!"
}
```

**Response (200 OK):**
```json
{
  "message": "Password has been reset successfully."
}
```

---

### 1.6 Refresh Authentication Token
Refreshes an expired access token using a valid refresh token.
- **Method / Route**: `POST /api/auth/refresh`
- **Rate Limit**: 5/minute
- **Auth Required**: No

**Request Body:**
```json
{
  "refresh_token": "supabase_refresh_token_string"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJFUzI1NiIs...",
  "refresh_token": "new_refresh_token_string",
  "token_type": "bearer"
}
```

---

### 1.7 Current User Identity
Inspects the claims of the currently authenticated Bearer token.
- **Method / Route**: `GET /api/auth/me`
- **Rate Limit**: 100/minute
- **Auth Required**: Yes (`Bearer Token`)

**Response (200 OK):**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "email": "user@example.com",
  "role": "authenticated"
}
```

---

## 2. Account Management Endpoints (`/api/account`)

### 2.1 Get Account Details
- **Method / Route**: `GET /api/account/settings`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "email": "user@example.com"
}
```

---

### 2.2 Change Password
- **Method / Route**: `POST /api/account/change-password`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "current_password": "OldPassword123!",
  "new_password": "NewSecurePassword456!"
}
```

**Response (200 OK):**
```json
{
  "message": "Password updated successfully"
}
```

---

### 2.3 Request Email Change
- **Method / Route**: `POST /api/account/change-email`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "new_email": "updated-email@example.com"
}
```

**Response (200 OK):**
```json
{
  "message": "Verification email sent to your new address. Please check your inbox."
}
```

---

### 2.4 Delete Account & Cascade Data
Permanently purges the authenticated user and cascades deletes across products, competitors, price history, insights, and alerts.
- **Method / Route**: `DELETE /api/account/delete`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "message": "Account data deleted successfully. Please log out."
}
```

---

## 3. Store Discovery & Tracking Endpoints (`/api/stores`)

### 3.1 Discover Products from Store URL
Probes and extracts products from a target store using auto-detected platform engines (Shopify, WooCommerce, Generic).
- **Method / Route**: `POST /api/stores/discover`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "url": "https://example-store.myshopify.com",
  "keyword": "laptop",
  "limit": 50
}
```

**Response (200 OK):**
```json
{
  "platform": "shopify",
  "store_url": "https://example-store.myshopify.com",
  "total_found": 1,
  "products": [
    {
      "name": "Pro Laptop 14 - Space Gray",
      "price": "1499.00",
      "currency": "USD",
      "image_url": "https://example-store.myshopify.com/cdn/image.jpg",
      "product_url": "https://example-store.myshopify.com/products/pro-laptop-14",
      "platform": "shopify",
      "variant_id": "40123984",
      "sku": "LAP-14-SG",
      "in_stock": true
    }
  ],
  "error": null
}
```

---

### 3.2 Track Discovered Products (Immediate Price Reuse)
Creates a product tracking group and persists competitors along with prices discovered during the exploration step.
- **Method / Route**: `POST /api/stores/track`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "group_name": "Premium Ultrabooks",
  "products": [
    {
      "url": "https://example-store.myshopify.com/products/pro-laptop-14",
      "price": "1499.00",
      "currency": "USD"
    }
  ],
  "alert_threshold_percent": "10.00"
}
```

**Response (201 Created):**
```json
{
  "group_id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
  "group_name": "Premium Ultrabooks",
  "products_added": 1,
  "prices_stored": 1
}
```

---

## 4. Tracked Product Management (`/api/products` & `/api/competitors`)

### 4.1 List Tracked Products
- **Method / Route**: `GET /api/products`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "products": [
    {
      "id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
      "product_name": "Premium Ultrabooks",
      "is_active": true,
      "created_at": "2025-01-10T12:00:00Z",
      "updated_at": "2025-01-10T12:00:00Z",
      "competitors": [
        {
          "id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
          "url": "https://example-store.myshopify.com/products/pro-laptop-14",
          "retailer_name": "example-store.myshopify.com",
          "alert_threshold_percent": "10.00",
          "created_at": "2025-01-10T12:00:00Z"
        }
      ]
    }
  ],
  "total": 1
}
```

---

### 4.2 Get Product Details
- **Method / Route**: `GET /api/products/{id}`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
  "product_name": "Premium Ultrabooks",
  "is_active": true,
  "created_at": "2025-01-10T12:00:00Z",
  "updated_at": "2025-01-10T12:00:00Z",
  "competitors": [
    {
      "id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
      "url": "https://example-store.myshopify.com/products/pro-laptop-14",
      "retailer_name": "example-store.myshopify.com",
      "alert_threshold_percent": "10.00",
      "created_at": "2025-01-10T12:00:00Z"
    }
  ]
}
```

---

### 4.3 Update Product Group
- **Method / Route**: `PUT /api/products/{id}`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "product_name": "Enterprise Ultrabooks 2025",
  "is_active": true
}
```

**Response (200 OK):**
```json
{
  "id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
  "product_name": "Enterprise Ultrabooks 2025",
  "is_active": true,
  "created_at": "2025-01-10T12:00:00Z",
  "updated_at": "2025-01-10T14:30:00Z",
  "competitors": []
}
```

---

### 4.4 Delete Product Group (Cascade)
- **Method / Route**: `DELETE /api/products/{id}`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "message": "Product deleted successfully"
}
```

---

### 4.5 Add Competitor to Product
- **Method / Route**: `POST /api/products/{id}/competitors`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "url": "https://competitor.com/products/laptop",
  "retailer_name": "CompetitorDirect",
  "alert_threshold_percent": "8.00"
}
```

**Response (201 Created):**
```json
{
  "id": "8aa85f64-5717-4562-b3fc-2c963f66afd4",
  "url": "https://competitor.com/products/laptop",
  "retailer_name": "CompetitorDirect",
  "alert_threshold_percent": "8.00",
  "created_at": "2025-01-10T15:00:00Z"
}
```

---

### 4.6 Delete Single Competitor
- **Method / Route**: `DELETE /api/competitors/{id}`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "message": "Competitor deleted successfully"
}
```

---

## 5. Distributed Scraping & Price History (`/api/scraper`)

### 5.1 Trigger Manual Background Scrape
Enqueues a Celery scrape task and returns a `task_id` for non-blocking execution.
- **Method / Route**: `POST /api/scraper/scrape/manual/{product_id}`
- **Rate Limit**: 10/minute
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "task_id": "c56d0a7a-8b1e-4c5a-b678-831d683709f1",
  "status": "queued",
  "message": "Scrape task queued"
}
```

---

### 5.2 Stream Scrape Progress (Server-Sent Events)
Provides live real-time progress events for an in-flight manual scrape task.
- **Method / Route**: `GET /api/scraper/scrape/stream/{task_id}`
- **Response Type**: `text/event-stream`
- **Auth Required**: No (Token validated during task generation)

**Stream Chunk Example:**
```http
data: {"status": "scraping", "completed": 1, "total": 3, "current": "example-store.myshopify.com", "results": [...]}

data: {"status": "completed", "completed": 3, "total": 3, "current": null, "results": [...]}
```

---

### 5.3 Worker Infrastructure Health Check
- **Method / Route**: `GET /api/scraper/scrape/worker-health`
- **Auth Required**: No

**Response (200 OK):**
```json
{
  "worker_status": "online",
  "active_workers": 2,
  "broker_status": "connected"
}
```

---

### 5.4 Get Price History for Product Group
- **Method / Route**: `GET /api/scraper/prices/{product_id}/history`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "prices": [
    {
      "id": "1fa85f64-5717-4562-b3fc-2c963f66afe1",
      "competitor_id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
      "price": "1499.00",
      "currency": "USD",
      "scraped_at": "2025-01-10T12:00:00Z",
      "scrape_status": "success",
      "error_message": null
    }
  ],
  "total": 1
}
```

---

### 5.5 Get Latest Price for Competitor
- **Method / Route**: `GET /api/scraper/prices/latest/{competitor_id}`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "id": "1fa85f64-5717-4562-b3fc-2c963f66afe1",
  "competitor_id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
  "price": "1499.00",
  "currency": "USD",
  "scraped_at": "2025-01-10T12:00:00Z",
  "scrape_status": "success",
  "error_message": null
}
```

---

## 6. Charts & Export Endpoints (`/api/charts` & `/api/export`)

### 6.1 Get Structured Time-Series Chart Data
Transforms raw price observations into frontend-ready time series with pre-calculated statistics.
- **Method / Route**: `GET /api/charts/{product_id}?days=30`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "product_id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
  "product_name": "Premium Ultrabooks",
  "competitors": [
    {
      "competitor_id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
      "competitor_name": "example-store.myshopify.com",
      "url": "https://example-store.myshopify.com/products/pro-laptop-14",
      "data_points": [
        {
          "timestamp": "2025-01-01T02:00:00Z",
          "price": "1599.00",
          "currency": "USD",
          "status": "success"
        },
        {
          "timestamp": "2025-01-10T02:00:00Z",
          "price": "1499.00",
          "currency": "USD",
          "status": "success"
        }
      ],
      "average_price": "1549.00",
      "min_price": "1499.00",
      "max_price": "1599.00",
      "current_price": "1499.00",
      "price_change_percent": "-6.25"
    }
  ],
  "date_range_start": "2025-01-01T02:00:00Z",
  "date_range_end": "2025-01-10T02:00:00Z",
  "total_data_points": 2
}
```

---

### 6.2 Export Price History as CSV
Streams historical observations formatted for spreadsheet analysis. Supports Dual Bearer Header and Cookie authentication for direct browser downloads.
- **Method / Route**: `GET /api/export/{product_id}/csv`
- **Response Content-Type**: `text/csv`
- **Header**: `Content-Disposition: attachment; filename="Premium_Ultrabooks_price_history_20250110.csv"`
- **Auth Required**: Yes (`Bearer Token` or `access_token` cookie)

**Sample CSV Stream Output:**
```csv
Date,Competitor,Price,Currency,Status,Error
2025-01-10 02:00:00,example-store.myshopify.com,1499.00,USD,success,
2025-01-01 02:00:00,example-store.myshopify.com,1599.00,USD,success,
```

---

## 7. AI Insights Engine (`/api/insights`)

### 7.1 Get AI Insights for Product
- **Method / Route**: `GET /api/insights/{product_id}`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "insights": [
    {
      "id": "2da85f64-5717-4562-b3fc-2c963f66aff3",
      "product_id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
      "insight_text": "Competitor pricing has decreased by 6.25% over the last 10 days, suggesting post-holiday discounting.",
      "insight_type": "pattern",
      "confidence_score": "0.94",
      "generated_at": "2025-01-10T03:00:00Z"
    }
  ],
  "total": 1
}
```

---

### 7.2 Generate Fresh Insights (Groq Llama 3.3 70B)
Synthesizes price history into structured insights. Rate-limited to once per 24 hours per product.
- **Method / Route**: `POST /api/insights/generate/{product_id}`
- **Auth Required**: Yes

**Request Body (Optional):**
```json
{
  "force_regenerate": false
}
```

**Response (200 OK):**
```json
{
  "insights": [
    {
      "id": "2da85f64-5717-4562-b3fc-2c963f66aff3",
      "product_id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
      "insight_text": "Price stabilized around $1499.00 across retailers. Recommend maintaining current margin target.",
      "insight_type": "recommendation",
      "confidence_score": "0.89",
      "generated_at": "2025-01-10T16:00:00Z"
    }
  ],
  "total": 1
}
```

---

## 8. Smart Alerts & Notification Configuration (`/api/alerts`)

### 8.1 Get Alert Settings
- **Method / Route**: `GET /api/alerts/settings`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "email_enabled": true,
  "digest_frequency_hours": 24,
  "alert_price_drop": true,
  "alert_price_increase": true,
  "last_digest_sent_at": "2025-01-10T00:00:00Z"
}
```

---

### 8.2 Update Alert Settings
- **Method / Route**: `PUT /api/alerts/settings`
- **Auth Required**: Yes

**Request Body:**
```json
{
  "email_enabled": true,
  "digest_frequency_hours": 12,
  "alert_price_drop": true,
  "alert_price_increase": false
}
```

**Response (200 OK):**
```json
{
  "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "email_enabled": true,
  "digest_frequency_hours": 12,
  "alert_price_drop": true,
  "alert_price_increase": false,
  "last_digest_sent_at": "2025-01-10T00:00:00Z"
}
```

---

### 8.3 Get Pending Alerts
- **Method / Route**: `GET /api/alerts/pending`
- **Auth Required**: Yes

**Response (200 OK):**
```json
{
  "alerts": [
    {
      "id": "5ea85f64-5717-4562-b3fc-2c963f66afe5",
      "product_id": "4fa85f64-5717-4562-b3fc-2c963f66afb2",
      "product_name": "Premium Ultrabooks",
      "competitor_id": "9ca85f64-5717-4562-b3fc-2c963f66afc1",
      "competitor_url": "https://example-store.myshopify.com/products/pro-laptop-14",
      "old_price": "1599.00",
      "new_price": "1499.00",
      "price_change_percent": "-6.25",
      "alert_type": "price_drop",
      "old_currency": "USD",
      "new_currency": "USD",
      "created_at": "2025-01-10T02:00:00Z"
    }
  ],
  "total": 1
}
```

---

### 8.4 Accept Currency Migration (Single / Bulk)
Resolves currency mismatch alerts and updates expected currency.

**Single Competitor Update:**
- **Method / Route**: `PATCH /api/alerts/competitors/{competitor_id}/accept-currency`
- **Request Body**: `{"currency": "GBP"}`
- **Response (200 OK)**: `{"success": true, "message": "Now tracking prices in GBP", "new_currency": "GBP"}`

**Bulk Accept All Currency Changes:**
- **Method / Route**: `POST /api/alerts/accept-all-currencies`
- **Response (200 OK)**: `{"success": true, "message": "Accepted 2 currency changes", "updated_count": 2}`

---

### 8.5 Send Diagnostic Test Email
- **Method / Route**: `POST /api/alerts/test`
- **Auth Required**: Yes

**Request Body (Optional):**
```json
{
  "email": "user@example.com"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Test email sent to user@example.com",
  "email": "user@example.com"
}
```

---

## 9. Dashboard Helper Endpoints (`/api/dashboard`)

High-performance aggregated endpoints designed for dashboard rendering with minimal network roundtrips:

| Endpoint | Method | Description | Sample Output |
|---|---|---|---|
| `/api/dashboard/stats` | `GET` | Exact entity counts for dashboard tiles | `{"products": 5, "competitors": 12, "alerts": 3, "insights": 2}` |
| `/api/dashboard/activity` | `GET` | 10 most recent price alerts & movements | `{"activity": [{"id": "...", "type": "price_drop", "product_name": "...", "change_percent": -5.0}]}` |
| `/api/dashboard/products` | `GET` | 5 recent products with competitor counts | `{"products": [{"id": "...", "product_name": "...", "competitor_count": 3}]}` |

---

## 10. System Health Endpoint

### 10.1 Health Check
- **Method / Route**: `GET /api/health`
- **Auth Required**: No

**Response (200 OK):**
```json
{
  "status": "healthy"
}
```
