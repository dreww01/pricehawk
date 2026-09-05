# PriceHawk — REST API & Endpoint Reference

This document provides a comprehensive REST API specification for **PriceHawk**, covering authentication models, endpoint routes, query parameters, request/response JSON schemas, rate limits, and HTTP error representations.

---

## Table of Contents

1. [API Architecture & Conventions](#1-api-architecture--conventions)
2. [Authentication & Authorization](#2-authentication--authorization)
3. [Global Error Models & HTTP Status Codes](#3-global-error-models--http-status-codes)
4. [Authentication Endpoints (`/api/auth`)](#4-authentication-endpoints-apiauth)
5. [Account Management Endpoints (`/api/account`)](#5-account-management-endpoints-apiaccount)
6. [Store Discovery & Tracking Endpoints (`/api/stores`)](#6-store-discovery--tracking-endpoints-apistores)
7. [Tracked Products Endpoints (`/api/tracked-products`)](#7-tracked-products-endpoints-apitracked-products)
8. [Scraper & Price History Endpoints (`/api/scraper`)](#8-scraper--price-history-endpoints-apiscraper)
9. [Charts Visualization Endpoints (`/api/charts`)](#9-charts-visualization-endpoints-apicharts)
10. [AI Insights Endpoints (`/api/insights`)](#10-ai-insights-endpoints-apiinsights)
11. [Alerts & Notification Endpoints (`/api/alerts`)](#11-alerts--notification-endpoints-apialerts)
12. [CSV Export Endpoints (`/api/export`)](#12-csv-export-endpoints-apiexport)
13. [System Health Endpoint (`/api/health`)](#13-system-health-endpoint-apihealth)

---

## 1. API Architecture & Conventions

- **Base URL**: `http://localhost:8000/api` (Production: `https://<domain>/api`)
- **Interactive Documentation**: Swagger UI at `/api/docs`, ReDoc at `/api/redoc`, OpenAPI JSON schema at `/api/openapi.json`.
- **Content Type**: `application/json` (except SSE streams: `text/event-stream` and CSV export: `text/csv`).
- **Date & Time Format**: ISO 8601 extended strings (`YYYY-MM-DDTHH:MM:SS.mmmmmm+00:00` or `YYYY-MM-DDTHH:MM:SSZ`).
- **Numeric Precision**: Prices and percentages are represented as numeric Decimals (e.g. `99.99`).

---

## 2. Authentication & Authorization

All protected endpoints require a Supabase JWT passed via the standard HTTP `Authorization` header:

```http
Authorization: Bearer <JWT_ACCESS_TOKEN>
```

For browser file downloads (such as CSV export), the system provides dual authentication support by falling back to the `access_token` cookie if the `Authorization` header is omitted.

### Rate Limiting Policies (SlowAPI)

| Tier | Endpoints | Limit |
|---|---|---|
| **Auth Tier** | `/api/auth/*` | `5 requests / minute` |
| **Scrape Tier** | `/api/scraper/scrape/manual/*` | `10 requests / minute` |
| **General Tier** | All standard read/write endpoints | `100 requests / minute` |

---

## 3. Global Error Models & HTTP Status Codes

The API emits standardized JSON error responses:

```json
{
  "detail": "Actionable error description or validation message."
}
```

For unhandled server errors (500), the response includes an `error_id` for backend log correlation (OWASP compliant):

```json
{
  "detail": "An unexpected error occurred. Please try again.",
  "error_id": "a1b2c3d4"
}
```

### Standard HTTP Status Codes

- `200 OK`: Request succeeded.
- `201 Created`: Resource successfully created.
- `202 Accepted`: Asynchronous background task queued.
- `204 No Content`: Resource successfully deleted.
- `400 Bad Request`: Malformed payload, invalid query parameter, or business rule violation.
- `401 Unauthorized`: Missing, invalid, or expired JWT token.
- `403 Forbidden`: Authenticated user lacks permission for the requested entity.
- `404 Not Found`: Resource does not exist or is not accessible by the authenticated user.
- `422 Unprocessable Entity`: Request payload failed Pydantic schema validation.
- `429 Too Many Requests`: Client exceeded rate limits.
- `500 Internal Server Error`: Server exception caught by global error middleware.

---

## 4. Authentication Endpoints (`/api/auth`)

### 4.1 Login
`POST /api/auth/login`
- **Rate Limit**: 5/minute
- **Request Body**:
  ```json
  {
    "email": "user@example.com",
    "password": "SecretPassword123"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "access_token": "eyJhbGciOiJFUzI1NiIs...",
    "token_type": "bearer",
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "user@example.com"
  }
  ```
- **Errors**: `401 Unauthorized` (Invalid credentials), `422 Unprocessable Entity`.

---

### 4.2 Signup
`POST /api/auth/signup`
- **Rate Limit**: 5/minute
- **Request Body**:
  ```json
  {
    "email": "newuser@example.com",
    "password": "StrongPassword123"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "Account created successfully",
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "newuser@example.com",
    "email_confirmed": false
  }
  ```
- **Errors**: `400 Bad Request` (Email already registered), `422 Unprocessable Entity`.

---

### 4.3 Forgot Password (Request OTP)
`POST /api/auth/forgot-password`
- **Rate Limit**: 5/minute
- **Request Body**:
  ```json
  {
    "email": "user@example.com"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "If an account exists with this email, a reset code has been sent."
  }
  ```
- **Errors**: `429 Too Many Requests` (Includes `X-Retry-After`), `422 Unprocessable Entity`.

---

### 4.4 Verify Reset OTP
`POST /api/auth/verify-reset-otp`
- **Rate Limit**: 5/minute
- **Request Body**:
  ```json
  {
    "email": "user@example.com",
    "otp": "123456"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "Code verified successfully",
    "reset_token": "temp-jwt-token-for-password-reset"
  }
  ```
- **Errors**: `400 Bad Request` (Invalid or expired code).

---

### 4.5 Reset Password
`POST /api/auth/reset-password`
- **Rate Limit**: 5/minute
- **Request Body**:
  ```json
  {
    "reset_token": "temp-jwt-token-for-password-reset",
    "new_password": "NewSecretPassword123"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "Password has been reset successfully. You can now log in."
  }
  ```
- **Errors**: `400 Bad Request` (Weak password, expired reset session).

---

### 4.6 Get Current User
`GET /api/auth/me`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "user@example.com",
    "role": "authenticated"
  }
  ```
- **Errors**: `401 Unauthorized`.

---

## 5. Account Management Endpoints (`/api/account`)

### 5.1 Get Account Settings
`GET /api/account/settings`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "user@example.com"
  }
  ```

---

### 5.2 Change Password
`POST /api/account/change-password`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "current_password": "OldPassword123",
    "new_password": "NewPassword456"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "Password updated successfully"
  }
  ```
- **Errors**: `400 Bad Request` (Weak password).

---

### 5.3 Change Email
`POST /api/account/change-email`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "new_email": "updated-email@example.com"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "message": "Verification email sent to your new address. Please check your inbox."
  }
  ```

---

### 5.4 Delete Account
`DELETE /api/account/delete`
- **Auth**: Required
- **Description**: Permanently purges user account data, tracking groups, competitors, price history, and alerts.
- **Response `200 OK`**:
  ```json
  {
    "message": "Account data deleted successfully. Please log out."
  }
  ```

---

## 6. Store Discovery & Tracking Endpoints (`/api/stores`)

### 6.1 Discover Products from Store URL
`POST /api/stores/discover`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "url": "https://example-store.myshopify.com",
    "keyword": "headphone",
    "limit": 50
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "platform": "shopify",
    "store_url": "https://example-store.myshopify.com",
    "total_found": 1,
    "products": [
      {
        "name": "Noise Cancelling Headphones Pro",
        "price": 299.99,
        "currency": "USD",
        "image_url": "https://example-store.com/images/hp1.jpg",
        "product_url": "https://example-store.myshopify.com/products/headphones-pro",
        "platform": "shopify",
        "variant_id": "401928374",
        "sku": "HP-PRO-01",
        "in_stock": true
      }
    ],
    "error": null
  }
  ```

---

### 6.2 Track Discovered Products
`POST /api/stores/track`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "group_name": "Premium Headphones",
    "products": [
      {
        "url": "https://example-store.myshopify.com/products/headphones-pro",
        "price": 299.99,
        "currency": "USD"
      }
    ],
    "alert_threshold_percent": 10.0
  }
  ```
- **Response `201 Created`**:
  ```json
  {
    "group_id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
    "group_name": "Premium Headphones",
    "products_added": 1,
    "prices_stored": 1
  }
  ```

---

## 7. Tracked Products Endpoints (`/api/tracked-products`)

### 7.1 List Tracked Products
`GET /api/tracked-products`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "products": [
      {
        "id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
        "product_name": "Premium Headphones",
        "is_active": true,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "2026-09-01T12:00:00Z",
        "competitors": [
          {
            "id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "url": "https://example-store.myshopify.com/products/headphones-pro",
            "retailer_name": "example-store.myshopify.com",
            "alert_threshold_percent": 10.0,
            "created_at": "2026-09-01T12:00:00Z"
          }
        ]
      }
    ],
    "total": 1
  }
  ```

---

### 7.2 Get Tracked Product by ID
`GET /api/tracked-products/{product_id}`
- **Auth**: Required
- **Response `200 OK`**: `ProductResponse` model (as shown above).
- **Errors**: `404 Not Found`.

---

### 7.3 Update Tracked Product
`PUT /api/tracked-products/{product_id}`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "product_name": "Flagship Wireless Headphones",
    "is_active": true
  }
  ```
- **Response `200 OK`**: `ProductResponse` model.
- **Errors**: `400 Bad Request` (No fields provided), `404 Not Found`.

---

### 7.4 Delete (Soft Delete) Tracked Product
`DELETE /api/tracked-products/{product_id}`
- **Auth**: Required
- **Response `204 No Content`**
- **Errors**: `404 Not Found`.

---

## 8. Scraper & Price History Endpoints (`/api/scraper`)

### 8.1 Queue Manual Scrape Task
`POST /api/scraper/scrape/manual/{product_id}`
- **Auth**: Required
- **Rate Limit**: 10/minute
- **Response `202 Accepted`**:
  ```json
  {
    "task_id": "b3e947c1-0c58-4122-b5e1-95c52c93d9b0",
    "status": "queued",
    "message": "Scraping 3 competitors"
  }
  ```
- **Errors**: `404 Not Found` (Product or competitors not found).

---

### 8.2 Stream Scrape Progress (Server-Sent Events)
`GET /api/scraper/scrape/stream/{task_id}`
- **Auth**: Public stream identifier
- **Content-Type**: `text/event-stream`
- **Stream Event Format**:
  ```
  data: {"status": "scraping", "completed": 1, "total": 3, "current": "amazon.com"}

  data: {"status": "scraping", "completed": 2, "total": 3, "current": "walmart.com"}

  data: {"status": "completed", "completed": 3, "total": 3, "results": [{"competitor_id": "...", "price": 289.99, "status": "success"}]}
  ```

---

### 8.3 Get Price History
`GET /api/scraper/prices/{product_id}/history`
- **Auth**: Required
- **Query Parameters**:
  - `limit` (integer, default: 100)
  - `offset` (integer, default: 0)
- **Response `200 OK`**:
  ```json
  {
    "prices": [
      {
        "id": "7f8a9b0c-1d2e-3f4a-5b6c-7d8e9f0a1b2c",
        "competitor_id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "price": 289.99,
        "currency": "USD",
        "scraped_at": "2026-09-02T02:00:00Z",
        "scrape_status": "success",
        "error_message": null
      }
    ],
    "total": 1
  }
  ```

---

### 8.4 Get Latest Price for Competitor
`GET /api/scraper/prices/latest/{competitor_id}`
- **Auth**: Required
- **Response `200 OK`**: `PriceHistoryResponse` model or `null`.
- **Errors**: `404 Not Found`.

---

### 8.5 Get Formatted Chart Data
`GET /api/scraper/prices/{product_id}/chart-data`
- **Auth**: Required
- **Query Parameters**:
  - `days` (integer, default: 30)
- **Response `200 OK`**: `ChartDataResponse` (see section 9 for schema).

---

### 8.6 Worker Health Check
`GET /api/scraper/scrape/worker-health`
- **Auth**: None
- **Response `200 OK`**:
  ```json
  {
    "worker_status": "healthy",
    "ping_response": "['celery@worker-node-1']",
    "active_tasks": 0,
    "error": null
  }
  ```

---

## 9. Charts Visualization Endpoints (`/api/charts`)

### 9.1 Get Chart Visualization JSON
`GET /api/charts/{product_id}`
- **Auth**: Required
- **Query Parameters**:
  - `days` (integer, default: 30, min: 1, max: 365)
- **Response `200 OK`**:
  ```json
  {
    "product_id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
    "product_name": "Premium Headphones",
    "competitors": [
      {
        "competitor_id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "competitor_name": "example-store.myshopify.com",
        "url": "https://example-store.myshopify.com/products/headphones-pro",
        "data_points": [
          {
            "timestamp": "2026-08-25T02:00:00Z",
            "price": 299.99,
            "currency": "USD",
            "status": "success"
          },
          {
            "timestamp": "2026-09-02T02:00:00Z",
            "price": 289.99,
            "currency": "USD",
            "status": "success"
          }
        ],
        "average_price": 294.99,
        "min_price": 289.99,
        "max_price": 299.99,
        "current_price": 289.99,
        "price_change_percent": -3.33
      }
    ],
    "date_range_start": "2026-08-25T02:00:00Z",
    "date_range_end": "2026-09-02T02:00:00Z",
    "total_data_points": 2
  }
  ```

---

## 10. AI Insights Endpoints (`/api/insights`)

### 10.1 Get Product Insights
`GET /api/insights/{product_id}`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "insights": [
      {
        "id": "3a4b5c6d-7e8f-9a0b-1c2d-3e4f5a6b7c8d",
        "product_id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
        "insight_text": "Competitor audio-direct.com lowered prices by 10% on weekends over the past 30 days.",
        "insight_type": "pattern",
        "confidence_score": 0.88,
        "generated_at": "2026-09-02T02:15:00Z"
      }
    ],
    "total": 1
  }
  ```

---

### 10.2 Generate AI Insights
`POST /api/insights/generate/{product_id}`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "force_regenerate": false
  }
  ```
- **Response `200 OK`**: `InsightListResponse` model.
- **Errors**: `400 Bad Request` (Insufficient price history or 1/day rate limit reached).

---

## 11. Alerts & Notification Endpoints (`/api/alerts`)

### 11.1 Get Alert Settings
`GET /api/alerts/settings`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email_enabled": true,
    "digest_frequency": "daily",
    "alert_on_price_drop": true,
    "alert_on_price_increase": true,
    "alert_threshold_percent": 5.0
  }
  ```

---

### 11.2 Update Alert Settings
`PUT /api/alerts/settings`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "email_enabled": true,
    "digest_frequency": "immediate",
    "alert_on_price_drop": true,
    "alert_on_price_increase": false,
    "alert_threshold_percent": 7.5
  }
  ```
- **Response `200 OK`**: `AlertSettingsResponse` model.

---

### 11.3 Get Pending Alerts
`GET /api/alerts/pending`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "alerts": [
      {
        "id": "5c6d7e8f-9a0b-1c2d-3e4f-5a6b7c8d9e0f",
        "product_id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
        "product_name": "Premium Headphones",
        "competitor_id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "competitor_url": "https://example-store.myshopify.com/products/headphones-pro",
        "old_price": 299.99,
        "new_price": 269.99,
        "price_change_percent": -10.0,
        "alert_type": "price_drop",
        "old_currency": "USD",
        "new_currency": "USD",
        "created_at": "2026-09-02T02:00:00Z"
      }
    ],
    "total": 1
  }
  ```

---

### 11.4 Get Alert History
`GET /api/alerts/history`
- **Auth**: Required
- **Query Parameters**:
  - `limit` (integer, default: 20)
- **Response `200 OK`**:
  ```json
  {
    "alerts": [
      {
        "id": "9e0f1a2b-3c4d-5e6f-7a8b-9c0d1e2f3a4b",
        "product_id": "8f3b6c2a-9e1d-4f5a-8b7c-1d2e3f4a5b6c",
        "product_name": "Premium Headphones",
        "alert_type": "price_drop",
        "message": "Competitor price drop of -10.0%",
        "sent_at": "2026-09-02T03:00:00Z",
        "email_status": "sent"
      }
    ],
    "total": 1
  }
  ```

---

### 11.5 Send Test Notification Email
`POST /api/alerts/test`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "email": "test-recipient@example.com"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "success": true,
    "message": "Test email sent successfully",
    "email": "test-recipient@example.com"
  }
  ```

---

### 11.6 Accept Currency Change for Competitor
`PATCH /api/alerts/competitors/{competitor_id}/accept-currency`
- **Auth**: Required
- **Request Body**:
  ```json
  {
    "currency": "EUR"
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "success": true,
    "message": "Now tracking prices in EUR",
    "competitor_id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "new_currency": "EUR"
  }
  ```

---

### 11.7 Accept All Pending Currency Changes
`POST /api/alerts/accept-all-currencies`
- **Auth**: Required
- **Response `200 OK`**:
  ```json
  {
    "success": true,
    "message": "Accepted 2 currency changes",
    "updated_count": 2
  }
  ```

---

## 12. CSV Export Endpoints (`/api/export`)

### 12.1 Download Price History as CSV
`GET /api/export/{product_id}/csv`
- **Auth**: Required (Bearer Header or `access_token` Cookie)
- **Response `200 OK`**:
  - `Content-Type`: `text/csv`
  - `Content-Disposition`: `attachment; filename="Premium_Headphones_price_history_20260905.csv"`
- **CSV Format**:
  ```csv
  Date,Time,Competitor,Price,Currency,Status,Error
  2026-09-02,02:00:00,example-store.myshopify.com,289.99,USD,success,
  2026-08-25,02:00:00,example-store.myshopify.com,299.99,USD,success,
  ```

---

## 13. System Health Endpoint (`/api/health`)

### 13.1 Health Check
`GET /api/health`
- **Auth**: None
- **Response `200 OK`**:
  ```json
  {
    "status": "healthy"
  }
  ```
