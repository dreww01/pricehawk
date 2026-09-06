# PriceHawk REST API & Endpoint Reference

This document provides a comprehensive specification of all REST API endpoints provided by PriceHawk.

---

## 1. Global API Conventions

### Base URL & Ingress
- Development: `http://localhost:8000/api`
- Production: `https://<your-railway-domain>/api`
- Interactive OpenAPI Swagger UI: `/api/docs`
- ReDoc Documentation: `/api/redoc`
- OpenAPI JSON Specification: `/api/openapi.json`

### Authentication Scheme
All protected endpoints require an HTTP Bearer JSON Web Token (JWT) in the `Authorization` header:
```http
Authorization: Bearer <SUPABASE_JWT_ACCESS_TOKEN>
```
*Note: Browser-based endpoints such as `/api/export/{product_id}/csv` also accept the `access_token` cookie.*

### Standard Error Response Models

| HTTP Code | Error Model | Description |
| :--- | :--- | :--- |
| **400 Bad Request** | `{"detail": "Unable to process request..."}` | Validation failure or malformed payload. |
| **401 Unauthorized**| `{"detail": "Invalid or expired token"}` | Missing, malformed, or expired JWT token. |
| **403 Forbidden**   | `{"detail": "Not authorized to access this resource"}` | Tenant ownership mismatch. |
| **404 Not Found**   | `{"detail": "Resource not found"}` | Entity ID does not exist for the authenticated user. |
| **422 Unprocessable Entity** | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | Pydantic schema validation error. |
| **429 Too Many Requests** | `{"detail": "Too many requests. Please try again later.", "retry_after": "Rate limit exceeded: 5 per 1 minute"}` | Request threshold exceeded via `slowapi`. |
| **500 Internal Error** | `{"detail": "An unexpected error occurred. Please try again.", "error_id": "81d18ffa"}` | Unhandled server error with masked diagnostics. |

### Rate Limiting Policy
Rate limits are enforced at the application layer via `slowapi` using client IP resolution (supporting `X-Forwarded-For`). Exceeding a limit returns HTTP `429 Too Many Requests` with the standard response:
```json
{
  "detail": "Too many requests. Please try again later.",
  "retry_after": "Rate limit exceeded: 5 per 1 minute"
}
```

| Scope | Limit | Config Constant | Applicable Endpoints |
| :--- | :--- | :--- | :--- |
| **Authentication** | 5 req / min | `AUTH_RATE_LIMIT` | `/api/auth/login`, `/api/auth/signup`, `/api/auth/forgot-password`, `/api/auth/verify-reset-otp`, `/api/auth/reset-password` |
| **Scraping** | 10 req / min | `SCRAPE_RATE_LIMIT` | `/api/scraper/scrape/manual/{product_id}` |
| **General API** | 100 req / min | `API_RATE_LIMIT` | Default limit across all endpoints |

---

## 2. Authentication Endpoints (`/api/auth`)

Rate Limited to 5 requests per minute (`AUTH_RATE_LIMIT`).

### 2.1 Login
- **Method & Route:** `POST /api/auth/login`
- **Auth Required:** No
- **Request Body (`application/json`):**
  ```json
  {
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }
  ```
- **Response `200 OK` (`application/json`):**
  ```json
  {
    "access_token": "eyJhbGciOiJFUzI1NiIs...",
    "token_type": "bearer",
    "user_id": "b3e0c0a1-8d2a-4c28-97f2-1a2b3c4d5e6f",
    "email": "user@example.com"
  }
  ```

### 2.2 Sign Up
- **Method & Route:** `POST /api/auth/signup`
- **Auth Required:** No
- **Request Body (`application/json`):**
  ```json
  {
    "email": "newuser@example.com",
    "password": "SecurePassword123!"
  }
  ```
- **Response `200 OK` (`application/json`):**
  ```json
  {
    "message": "Account created successfully",
    "user_id": "b3e0c0a1-8d2a-4c28-97f2-1a2b3c4d5e6f",
    "email": "newuser@example.com",
    "email_confirmed": false
  }
  ```

### 2.3 Get Current User Identity
- **Method & Route:** `GET /api/auth/me`
- **Auth Required:** Bearer Token
- **Response `200 OK` (`application/json`):**
  ```json
  {
    "id": "b3e0c0a1-8d2a-4c28-97f2-1a2b3c4d5e6f",
    "email": "user@example.com",
    "role": "authenticated"
  }
  ```

### 2.4 Forgot Password
- **Method & Route:** `POST /api/auth/forgot-password`
- **Auth Required:** No
- **Request Body:** `{"email": "user@example.com"}`
- **Response `200 OK`:** `{"message": "If an account exists with this email, a reset code has been sent."}`

### 2.5 Verify Reset OTP
- **Method & Route:** `POST /api/auth/verify-reset-otp`
- **Auth Required:** No
- **Request Body:** `{"email": "user@example.com", "otp": "123456"}`
- **Response `200 OK`:** `{"message": "Code verified successfully", "reset_token": "eyJhbGci..."}`

### 2.6 Reset Password
- **Method & Route:** `POST /api/auth/reset-password`
- **Auth Required:** No
- **Request Body:** `{"reset_token": "eyJhbGci...", "new_password": "NewSecurePassword123!"}`
- **Response `200 OK`:** `{"message": "Password has been reset successfully. You can now log in."}`

---

## 3. Store Discovery & Tracking (`/api/stores`)

### 3.1 Discover Products from URL
- **Method & Route:** `POST /api/stores/discover`
- **Auth Required:** Bearer Token
- **Request Body (`application/json`):**
  ```json
  {
    "url": "https://example-store.myshopify.com",
    "keyword": "lipstick",
    "limit": 50
  }
  ```
- **Response `200 OK`:**
  ```json
  {
    "platform": "shopify",
    "store_url": "https://example-store.myshopify.com",
    "total_found": 1,
    "products": [
      {
        "name": "Velvet Matte Lipstick - Ruby",
        "price": 24.00,
        "currency": "USD",
        "image_url": "https://cdn.shopify.com/s/files/.../ruby.jpg",
        "product_url": "https://example-store.myshopify.com/products/ruby-lipstick",
        "platform": "shopify",
        "variant_id": "41234567890",
        "sku": "LIP-RUBY-01",
        "in_stock": true
      }
    ],
    "error": null
  }
  ```

### 3.2 Track Discovered Products
- **Method & Route:** `POST /api/stores/track`
- **Auth Required:** Bearer Token
- **Request Body (`application/json`):**
  ```json
  {
    "group_name": "Premium Matte Lipsticks",
    "products": [
      {
        "url": "https://example-store.myshopify.com/products/ruby-lipstick",
        "price": 24.00,
        "currency": "USD"
      }
    ],
    "alert_threshold_percent": 10.00
  }
  ```
- **Response `201 Created`:**
  ```json
  {
    "group_id": "c1f2e3d4-0000-4000-8000-112233445566",
    "group_name": "Premium Matte Lipsticks",
    "products_added": 1,
    "prices_stored": 1
  }
  ```

---

## 4. Tracked Product Management (`/api/tracked-products`)

### 4.1 List Tracked Products
- **Method & Route:** `GET /api/tracked-products`
- **Auth Required:** Bearer Token
- **Response `200 OK`:**
  ```json
  {
    "products": [
      {
        "id": "c1f2e3d4-0000-4000-8000-112233445566",
        "product_name": "Premium Matte Lipsticks",
        "is_active": true,
        "created_at": "2025-01-15T10:00:00Z",
        "updated_at": "2025-01-15T10:00:00Z",
        "competitors": [
          {
            "id": "e4f5a6b7-1111-4000-8000-998877665544",
            "url": "https://example-store.myshopify.com/products/ruby-lipstick",
            "retailer_name": "example-store.myshopify.com",
            "alert_threshold_percent": 10.00,
            "created_at": "2025-01-15T10:00:00Z"
          }
        ]
      }
    ],
    "total": 1
  }
  ```

### 4.2 Get Tracked Product by ID
- **Method & Route:** `GET /api/tracked-products/{product_id}`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** Returns single `ProductResponse` schema.

### 4.3 Update Tracked Product
- **Method & Route:** `PUT /api/tracked-products/{product_id}`
- **Auth Required:** Bearer Token
- **Request Body:** `{"product_name": "Updated Lipsticks", "is_active": true}`
- **Response `200 OK`:** Returns updated `ProductResponse`.

### 4.4 Delete Tracked Product (Soft Delete)
- **Method & Route:** `DELETE /api/tracked-products/{product_id}`
- **Auth Required:** Bearer Token
- **Response `204 No Content`**

---

## 5. Scraping & Price History (`/api/scraper`)

### 5.1 Queue Manual Scrape
- **Method & Route:** `POST /api/scraper/scrape/manual/{product_id}`
- **Auth Required:** Bearer Token
- **Rate Limit:** 10 requests per minute (`SCRAPE_RATE_LIMIT`)
- **Response `202 Accepted`:**
  ```json
  {
    "task_id": "f8d34b22-5555-4444-9999-001122334455",
    "status": "queued",
    "message": "Scraping 3 competitors"
  }
  ```

### 5.2 Stream Scrape Progress (Server-Sent Events)
- **Method & Route:** `GET /api/scraper/scrape/stream/{task_id}`
- **Auth Required:** No (task ID correlation token)
- **Media Type:** `text/event-stream`
- **Stream Event Format:**
  ```http
  data: {"status": "scraping", "completed": 1, "total": 3, "current": "example-store.com"}
  
  data: {"status": "completed", "completed": 3, "total": 3, "results": [...]}
  ```

### 5.3 Get Product Price History
- **Method & Route:** `GET /api/scraper/prices/{product_id}/history?limit=100&offset=0`
- **Auth Required:** Bearer Token
- **Response `200 OK`:**
  ```json
  {
    "prices": [
      {
        "id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
        "competitor_id": "e4f5a6b7-1111-4000-8000-998877665544",
        "price": 22.50,
        "currency": "USD",
        "scraped_at": "2025-01-16T02:00:00Z",
        "scrape_status": "success",
        "error_message": null
      }
    ],
    "total": 1
  }
  ```

### 5.4 Get Competitor Latest Price
- **Method & Route:** `GET /api/scraper/prices/latest/{competitor_id}`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** Returns latest `PriceHistoryResponse` object.

### 5.5 Celery Worker Health Check
- **Method & Route:** `GET /api/scraper/scrape/worker-health`
- **Auth Required:** No
- **Response `200 OK`:**
  ```json
  {
    "worker_status": "healthy",
    "ping_response": "pong",
    "active_tasks": 0,
    "error": null
  }
  ```

---

## 6. AI Insights (`/api/insights`)

### 6.1 Get AI Insights for Product
- **Method & Route:** `GET /api/insights/{product_id}`
- **Auth Required:** Bearer Token
- **Response `200 OK`:**
  ```json
  {
    "insights": [
      {
        "id": "99a8b7c6-d5e4-4321-1234-abcdefabcdef",
        "product_id": "c1f2e3d4-0000-4000-8000-112233445566",
        "insight_text": "Competitor dropped prices by 6.25% ahead of the weekend. Recommend matching to maintain search ranking.",
        "insight_type": "recommendation",
        "confidence_score": 0.92,
        "generated_at": "2025-01-16T02:05:00Z"
      }
    ],
    "total": 1
  }
  ```

### 6.2 Trigger AI Insight Generation
- **Method & Route:** `POST /api/insights/generate/{product_id}`
- **Auth Required:** Bearer Token
- **Request Body:** `{"force_regenerate": false}`
- **Response `200 OK`:** Returns newly generated `InsightListResponse`.

---

## 7. Alerts & Notification Management (`/api/alerts`)

### 7.1 Get Alert Settings
- **Method & Route:** `GET /api/alerts/settings`
- **Auth Required:** Bearer Token
- **Response `200 OK`:**
  ```json
  {
    "user_id": "b3e0c0a1-8d2a-4c28-97f2-1a2b3c4d5e6f",
    "email_enabled": true,
    "digest_frequency_hours": 24,
    "alert_price_drop": true,
    "alert_price_increase": true,
    "last_digest_sent_at": "2025-01-15T00:00:00Z",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-15T00:00:00Z"
  }
  ```

### 7.2 Update Alert Settings
- **Method & Route:** `PUT /api/alerts/settings`
- **Auth Required:** Bearer Token
- **Request Body:** `{"email_enabled": true, "digest_frequency_hours": 6, "alert_price_drop": true}`
- **Response `200 OK`:** Returns updated `AlertSettingsResponse`.

### 7.3 Get Pending Alerts
- **Method & Route:** `GET /api/alerts/pending`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** Returns list of alerts awaiting digest dispatch.

### 7.4 Get Alert Dispatch History
- **Method & Route:** `GET /api/alerts/history?limit=20`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** Returns list of sent email digest summaries.

### 7.5 Send Test Alert Email
- **Method & Route:** `POST /api/alerts/test`
- **Auth Required:** Bearer Token
- **Request Body:** `{"email": "user@example.com"}`
- **Response `200 OK`:** `{"success": true, "message": "Test email sent successfully", "email": "user@example.com"}`

### 7.6 Accept Currency Change
- **Method & Route:** `PATCH /api/alerts/competitors/{competitor_id}/accept-currency`
- **Auth Required:** Bearer Token
- **Request Body:** `{"currency": "EUR"}`
- **Response `200 OK`:** `{"success": true, "message": "Now tracking prices in EUR", "competitor_id": "...", "new_currency": "EUR"}`

---

## 8. Charts & Visualizations (`/api/charts`)

### 8.1 Get Formatted Price History for Chart.js
- **Method & Route:** `GET /api/charts/{product_id}?days=30`
- **Auth Required:** Bearer Token
- **Response `200 OK`:**
  ```json
  {
    "product_id": "c1f2e3d4-0000-4000-8000-112233445566",
    "product_name": "Premium Matte Lipsticks",
    "competitors": [
      {
        "competitor_id": "e4f5a6b7-1111-4000-8000-998877665544",
        "competitor_name": "example-store.myshopify.com",
        "url": "https://example-store.myshopify.com/products/ruby-lipstick",
        "data_points": [
          {
            "timestamp": "2025-01-01T02:00:00Z",
            "price": 24.00,
            "currency": "USD",
            "status": "success"
          },
          {
            "timestamp": "2025-01-16T02:00:00Z",
            "price": 22.50,
            "currency": "USD",
            "status": "success"
          }
        ],
        "average_price": 23.25,
        "min_price": 22.50,
        "max_price": 24.00,
        "current_price": 22.50,
        "price_change_percent": -6.25
      }
    ],
    "date_range_start": "2025-01-01T02:00:00Z",
    "date_range_end": "2025-01-16T02:00:00Z",
    "total_data_points": 2
  }
  ```

---

## 9. CSV Export (`/api/export`)

### 9.1 Download Price History CSV
- **Method & Route:** `GET /api/export/{product_id}/csv`
- **Auth Required:** Bearer Token OR `access_token` Cookie
- **Response `200 OK`:**
  - **Headers:** `Content-Type: text/csv`, `Content-Disposition: attachment; filename="Premium_Matte_Lipsticks_price_history_20250116.csv"`
  - **Payload:**
    ```csv
    Date,Time,Competitor,Price,Currency,Status,Error
    2025-01-16,02:00:00,example-store.myshopify.com,22.50,USD,success,
    2025-01-01,02:00:00,example-store.myshopify.com,24.00,USD,success,
    ```

---

## 10. Account Management (`/api/account`)

### 10.1 Get Account Settings
- **Method & Route:** `GET /api/account/settings`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** `{"user_id": "b3e0c0a1-8d2a-4c28-97f2-1a2b3c4d5e6f", "email": "user@example.com"}`

### 10.2 Change Password
- **Method & Route:** `POST /api/account/change-password`
- **Auth Required:** Bearer Token
- **Request Body:** `{"current_password": "OldPassword123!", "new_password": "NewPassword123!"}`
- **Response `200 OK`:** `{"message": "Password updated successfully"}`

### 10.3 Change Email
- **Method & Route:** `POST /api/account/change-email`
- **Auth Required:** Bearer Token
- **Request Body:** `{"new_email": "updated@example.com"}`
- **Response `200 OK`:** `{"message": "Verification email sent to your new address. Please check your inbox."}`

### 10.4 Delete Account
- **Method & Route:** `DELETE /api/account/delete`
- **Auth Required:** Bearer Token
- **Response `200 OK`:** `{"message": "Account data deleted successfully. Please log out."}`

---

## 11. System Health Check (`/api/health`)

- **Method & Route:** `GET /api/health`
- **Auth Required:** No
- **Response `200 OK`:** `{"status": "healthy"}`
