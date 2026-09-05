# REST API & Endpoint Reference

## Overview

The PriceHawk REST API exposes the routes registered by the FastAPI application in `main.py`. This guide is reconciled with the generated OpenAPI schema at `/api/openapi.json`; endpoints not listed here are not live APIs.

- **Base URL**: `http://localhost:8000` (dev) / `https://your-domain.railway.app` (prod)
- **Swagger UI**: `/api/docs`
- **ReDoc**: `/api/redoc`
- **OpenAPI schema**: `/api/openapi.json`

All `/api/*` routes require a valid Supabase Bearer token unless the endpoint explicitly says otherwise. Browser helper endpoints that accept an `access_token` cookie are called out separately.

## Global Request and Error Conventions

### Standard JSON Headers

```http
Authorization: Bearer <supabase_jwt_access_token>
Content-Type: application/json
Accept: application/json
```

### Common Error Responses

FastAPI automatically returns `422 Unprocessable Entity` for request-body, path, and query validation errors:

```json
{"detail":[{"loc":["body","email"],"msg":"value is not a valid email address","type":"value_error"}]}
```

Authenticated routes can also return:

| Status | Meaning | Example |
|---|---|---|
| `400 Bad Request` | Business rule or upstream service failure | `{"detail":"Unable to create account. Please try again."}` |
| `401 Unauthorized` | Missing, invalid, or expired Bearer token | `{"detail":"Not authenticated"}` or `{"detail":"Invalid or expired token"}` |
| `403 Forbidden` | Authenticated user cannot access resource | `{"detail":"Not authorized to modify this competitor"}` |
| `404 Not Found` | Resource not found or hidden by tenant scoping | `{"detail":"Product not found"}` |
| `429 Too Many Requests` | Rate limit exceeded | `{"error":"Rate limit exceeded: 5 per 1 minute"}` |
| `500 Internal Server Error` | Masked server exception | `{"detail":"An unexpected error occurred. Please try again.","error_id":"a4df02e0"}` |

### Rate Limits

| Scope | Limit |
|---|---|
| `/api/auth/login`, `/api/auth/signup`, `/api/auth/forgot-password`, `/api/auth/verify-reset-otp`, `/api/auth/reset-password` | 5 requests/minute |
| `/api/scraper/scrape/manual/{product_id}` | 10 requests/minute |
| Other routes | Standard application/global limits when configured |

## Registered `/api` Route Inventory

This is the live API route list generated from the application router configuration:

| Method | Route | Auth | Primary success status |
|---|---|---:|---:|
| `POST` | `/api/auth/login` | No | `200` |
| `POST` | `/api/auth/signup` | No | `200` |
| `GET` | `/api/auth/me` | Yes | `200` |
| `POST` | `/api/auth/forgot-password` | No | `200` |
| `POST` | `/api/auth/verify-reset-otp` | No | `200` |
| `POST` | `/api/auth/reset-password` | No | `200` |
| `GET` | `/api/tracked-products` | Yes | `200` |
| `GET` | `/api/tracked-products/{product_id}` | Yes | `200` |
| `PUT` | `/api/tracked-products/{product_id}` | Yes | `200` |
| `DELETE` | `/api/tracked-products/{product_id}` | Yes | `204` |
| `POST` | `/api/scraper/scrape/manual/{product_id}` | Yes | `202` |
| `GET` | `/api/scraper/scrape/stream/{task_id}` | No (task ID bearer) | `200` |
| `GET` | `/api/scraper/prices/{product_id}/history` | Yes | `200` |
| `GET` | `/api/scraper/prices/latest/{competitor_id}` | Yes | `200` |
| `GET` | `/api/scraper/prices/{product_id}/chart-data` | Yes | `200` |
| `GET` | `/api/scraper/scrape/worker-health` | No | `200` |
| `POST` | `/api/stores/discover` | Yes | `200` |
| `POST` | `/api/stores/track` | Yes | `201` |
| `GET` | `/api/insights/{product_id}` | Yes | `200` |
| `POST` | `/api/insights/generate/{product_id}` | Yes | `200` |
| `GET` | `/api/alerts/settings` | Yes | `200` |
| `PUT` | `/api/alerts/settings` | Yes | `200` |
| `GET` | `/api/alerts/pending` | Yes | `200` |
| `GET` | `/api/alerts/history` | Yes | `200` |
| `POST` | `/api/alerts/test` | Yes | `200` |
| `PATCH` | `/api/alerts/competitors/{competitor_id}/accept-currency` | Yes | `200` |
| `POST` | `/api/alerts/accept-all-currencies` | Yes | `200` |
| `GET` | `/api/export/{product_id}/csv` | Bearer or cookie | `200` |
| `GET` | `/api/charts/{product_id}` | Yes | `200` |
| `POST` | `/api/account/change-password` | Yes | `200` |
| `POST` | `/api/account/change-email` | Yes | `200` |
| `GET` | `/api/account/settings` | Yes | `200` |
| `DELETE` | `/api/account/delete` | Yes | `200` |
| `GET` | `/api/dashboard/stats` | Yes | `200` |
| `GET` | `/api/dashboard/activity` | Yes | `200` |
| `GET` | `/api/dashboard/products` | Yes | `200` |
| `GET` | `/api/insights` | Yes | `200` |
| `GET` | `/api/health` | No | `200` |

> Not live: `/api/auth/refresh`, `/api/products/*`, `/api/competitors/*`, and `/api/products/{id}/competitors` are not registered in the current FastAPI application. Use `/api/tracked-products/*` for tracked-product management and `/api/stores/track` to create a product group with competitor URLs.

---

## 1. Authentication (`/api/auth`)

### 1.1 Login

- **Method / Route**: `POST /api/auth/login`
- **Auth Required**: No
- **Success**: `200 OK`
- **Request model**: `LoginRequest`

```json
{"email":"user@example.com","password":"SecurePassword123!"}
```

**Response model**: `AuthResponse`

```json
{"access_token":"eyJhbGciOi...","token_type":"bearer","user_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email":"user@example.com"}
```

### 1.2 Signup

- **Method / Route**: `POST /api/auth/signup`
- **Auth Required**: No
- **Success**: `200 OK`
- **Request model**: `SignupRequest`

```json
{"email":"newuser@example.com","password":"SecurePassword123!"}
```

**Response example**:

```json
{"message":"Account created successfully","user_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email":"newuser@example.com","email_confirmed":false}
```

### 1.3 Forgot Password

- **Method / Route**: `POST /api/auth/forgot-password`
- **Auth Required**: No
- **Success**: `200 OK`
- **Request model**: `ForgotPasswordRequest`

```json
{"email":"user@example.com"}
```

**Response example**:

```json
{"message":"If an account exists with this email, a reset code has been sent."}
```

### 1.4 Verify Reset OTP

- **Method / Route**: `POST /api/auth/verify-reset-otp`
- **Auth Required**: No
- **Success**: `200 OK`
- **Request model**: `VerifyResetOTPRequest`

```json
{"email":"user@example.com","otp":"654321"}
```

**Response example**:

```json
{"message":"Code verified successfully","reset_token":"eyJhbGciOi..."}
```

### 1.5 Reset Password

- **Method / Route**: `POST /api/auth/reset-password`
- **Auth Required**: No
- **Success**: `200 OK`
- **Request model**: `ResetPasswordRequest`

```json
{"reset_token":"eyJhbGciOi...","new_password":"BrandNewSecurePassword123!"}
```

**Response example**:

```json
{"message":"Password has been reset successfully. You can now log in."}
```

### 1.6 Current User

- **Method / Route**: `GET /api/auth/me`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `CurrentUser`

```json
{"id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email":"user@example.com","role":"authenticated"}
```

---

## 2. Tracked Products (`/api/tracked-products`)

Tracked products are product groups with zero or more competitor URLs attached. There is no live `/api/products` router in this application.

### 2.1 List Tracked Products

- **Method / Route**: `GET /api/tracked-products`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `ProductListResponse`

```json
{"products":[{"id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","is_active":true,"created_at":"2025-01-10T12:00:00Z","updated_at":"2025-01-10T12:00:00Z","competitors":[{"id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","url":"https://example-store.myshopify.com/products/pro-laptop-14","retailer_name":"example-store.myshopify.com","alert_threshold_percent":"10.00","created_at":"2025-01-10T12:00:00Z"}]}],"total":1}
```

### 2.2 Get Tracked Product

- **Method / Route**: `GET /api/tracked-products/{product_id}`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `ProductResponse`

```json
{"id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","is_active":true,"created_at":"2025-01-10T12:00:00Z","updated_at":"2025-01-10T12:00:00Z","competitors":[]}
```

### 2.3 Update Tracked Product

- **Method / Route**: `PUT /api/tracked-products/{product_id}`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `ProductUpdate`

```json
{"product_name":"Enterprise Ultrabooks 2025","is_active":true}
```

**Response model**: `ProductResponse`

```json
{"id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Enterprise Ultrabooks 2025","is_active":true,"created_at":"2025-01-10T12:00:00Z","updated_at":"2025-01-10T14:30:00Z","competitors":[]}
```

### 2.4 Delete Tracked Product

- **Method / Route**: `DELETE /api/tracked-products/{product_id}`
- **Auth Required**: Yes
- **Success**: `204 No Content`
- **Response body**: empty

---

## 3. Store Discovery and Tracking (`/api/stores`)

### 3.1 Discover Products

- **Method / Route**: `POST /api/stores/discover`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `StoreDiscoveryRequest`

```json
{"url":"https://example-store.myshopify.com","keyword":"laptop","limit":50}
```

**Response model**: `StoreDiscoveryResponse`

```json
{"platform":"shopify","store_url":"https://example-store.myshopify.com","total_found":1,"products":[{"name":"Pro Laptop 14 - Space Gray","price":"1499.00","currency":"USD","image_url":"https://example-store.myshopify.com/cdn/image.jpg","product_url":"https://example-store.myshopify.com/products/pro-laptop-14","platform":"shopify","variant_id":"40123984","sku":"LAP-14-SG","in_stock":true}],"error":null}
```

### 3.2 Track Discovered Products

- **Method / Route**: `POST /api/stores/track`
- **Auth Required**: Yes
- **Success**: `201 Created`
- **Request model**: `TrackProductsRequest`

```json
{"group_name":"Premium Ultrabooks","products":[{"url":"https://example-store.myshopify.com/products/pro-laptop-14","price":"1499.00","currency":"USD"}],"alert_threshold_percent":"10.00"}
```

**Response model**: `TrackProductsResponse`

```json
{"group_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","group_name":"Premium Ultrabooks","products_added":1,"prices_stored":1}
```

---

## 4. Scraping and Price History (`/api/scraper`)

### 4.1 Trigger Manual Scrape

- **Method / Route**: `POST /api/scraper/scrape/manual/{product_id}`
- **Auth Required**: Yes
- **Success**: `202 Accepted`
- **Response model**: `ScrapeTaskResponse`

```json
{"task_id":"c56d0a7a-8b1e-4c5a-b678-831d683709f1","status":"queued","message":"Scraping 3 competitors"}
```

### 4.2 Stream Scrape Progress

- **Method / Route**: `GET /api/scraper/scrape/stream/{task_id}`
- **Auth Required**: No Bearer-token dependency in the current implementation; possession of the opaque Celery `task_id` authorizes access to that task's stream.
- **Success**: `200 OK`
- **Response content type**: `text/event-stream`
- **Security note**: Treat scrape task IDs as sensitive, unguessable bearer values. The stream can expose progress payloads and completed scrape results, including competitor domains or URLs and prices. Do not log, share, or embed task IDs outside the authenticated manual scrape session that created them.

```http
data: {"status":"scraping","completed":1,"total":3,"current":"example-store.myshopify.com"}

data: {"status":"completed","completed":3,"total":3,"results":[]}
```

### 4.3 Price History

- **Method / Route**: `GET /api/scraper/prices/{product_id}/history?limit=100&offset=0`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `PriceHistoryListResponse`

```json
{"prices":[{"id":"1fa85f64-5717-4562-b3fc-2c963f66afe1","competitor_id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","price":"1499.00","currency":"USD","scraped_at":"2025-01-10T12:00:00Z","scrape_status":"success","error_message":null}],"total":1}
```

### 4.4 Latest Competitor Price

- **Method / Route**: `GET /api/scraper/prices/latest/{competitor_id}`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `PriceHistoryResponse` or `null`

```json
{"id":"1fa85f64-5717-4562-b3fc-2c963f66afe1","competitor_id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","price":"1499.00","currency":"USD","scraped_at":"2025-01-10T12:00:00Z","scrape_status":"success","error_message":null}
```

### 4.5 Chart Data Through Scraper Router

- **Method / Route**: `GET /api/scraper/prices/{product_id}/chart-data?days=30`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `ChartDataResponse`

```json
{"product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","competitors":[],"date_range_start":null,"date_range_end":null,"total_data_points":0}
```

### 4.6 Worker Health

- **Method / Route**: `GET /api/scraper/scrape/worker-health`
- **Auth Required**: No
- **Success**: `200 OK`
- **Response model**: `WorkerHealthResponse`

```json
{"worker_status":"online","ping_response":"pong","active_tasks":0,"error":null}
```

---

## 5. Charts and Export

### 5.1 Chart Data

- **Method / Route**: `GET /api/charts/{product_id}?days=30`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `ChartDataResponse`

```json
{"product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","competitors":[{"competitor_id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","competitor_name":"example-store.myshopify.com","url":"https://example-store.myshopify.com/products/pro-laptop-14","data_points":[{"timestamp":"2025-01-10T02:00:00Z","price":"1499.00","currency":"USD","status":"success"}],"average_price":"1499.00","min_price":"1499.00","max_price":"1499.00","current_price":"1499.00","price_change_percent":"0.00"}],"date_range_start":"2025-01-10T02:00:00Z","date_range_end":"2025-01-10T02:00:00Z","total_data_points":1}
```

### 5.2 Export Price History CSV

- **Method / Route**: `GET /api/export/{product_id}/csv`
- **Auth Required**: Yes, via Bearer token or `access_token` cookie
- **Success**: `200 OK`
- **Not Found**: `404 Not Found`
- **Response content type**: `text/csv`
- **Header**: `Content-Disposition: attachment; filename="<product>_price_history_<YYYYMMDD>.csv"`

```csv
Date,Time,Competitor,Price,Currency,Status,Error
2025-01-10,02:00:00,example-store.myshopify.com,1499.00,USD,success,
```

---

## 6. AI Insights (`/api/insights`)

### 6.1 Product Insights

- **Method / Route**: `GET /api/insights/{product_id}`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `InsightListResponse`

```json
{"insights":[{"id":"2da85f64-5717-4562-b3fc-2c963f66aff3","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","insight_text":"Competitor pricing has decreased by 6.25% over the last 10 days.","insight_type":"pattern","confidence_score":"0.94","generated_at":"2025-01-10T03:00:00Z"}],"total":1}
```

### 6.2 Generate Product Insights

- **Method / Route**: `POST /api/insights/generate/{product_id}`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `GenerateInsightRequest`

```json
{"force_regenerate":false}
```

**Response model**: `InsightListResponse`

```json
{"insights":[{"id":"2da85f64-5717-4562-b3fc-2c963f66aff3","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","insight_text":"Price stabilized around $1499.00 across retailers.","insight_type":"recommendation","confidence_score":"0.89","generated_at":"2025-01-10T16:00:00Z"}],"total":1}
```

### 6.3 All Insights Dashboard Feed

- **Method / Route**: `GET /api/insights`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"insights":[{"id":"2da85f64-5717-4562-b3fc-2c963f66aff3","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","insight_text":"Price stabilized around $1499.00 across retailers.","insight_type":"recommendation","generated_at":"2025-01-10T16:00:00Z"}],"total":1}
```

---

## 7. Alerts (`/api/alerts`)

### 7.1 Get Alert Settings

- **Method / Route**: `GET /api/alerts/settings`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `AlertSettingsResponse`

```json
{"user_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email_enabled":true,"digest_frequency_hours":24,"alert_price_drop":true,"alert_price_increase":true,"last_digest_sent_at":null,"created_at":"2025-01-10T02:00:00Z","updated_at":"2025-01-10T02:00:00Z"}
```

### 7.2 Update Alert Settings

- **Method / Route**: `PUT /api/alerts/settings`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `AlertSettingsUpdate`

```json
{"email_enabled":true,"digest_frequency_hours":24,"alert_price_drop":true,"alert_price_increase":false}
```

**Response model**: `AlertSettingsResponse`

```json
{"user_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email_enabled":true,"digest_frequency_hours":24,"alert_price_drop":true,"alert_price_increase":false,"last_digest_sent_at":"2025-01-10T00:00:00Z","created_at":"2025-01-10T02:00:00Z","updated_at":"2025-01-10T16:00:00Z"}
```

### 7.3 Pending Alerts

- **Method / Route**: `GET /api/alerts/pending`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `PendingAlertsListResponse`

```json
{"alerts":[{"id":"5ea85f64-5717-4562-b3fc-2c963f66afe5","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","competitor_id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","competitor_url":"https://example-store.myshopify.com/products/pro-laptop-14","old_price":"1599.00","new_price":"1499.00","price_change_percent":"-6.25","alert_type":"price_drop","old_currency":"USD","new_currency":"USD","created_at":"2025-01-10T02:00:00Z"}],"total":1}
```

### 7.4 Alert History

- **Method / Route**: `GET /api/alerts/history?limit=20`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Response model**: `AlertHistoryListResponse`

```json
{"alerts":[{"id":"6ea85f64-5717-4562-b3fc-2c963f66afe5","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","alert_type":"price_drop","message":"Premium Ultrabooks dropped 6.25%","sent_at":"2025-01-10T03:00:00Z","email_status":"sent"}],"total":1}
```

### 7.5 Send Test Email

- **Method / Route**: `POST /api/alerts/test`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `TestEmailRequest` or `null`

```json
{"email":"user@example.com"}
```

**Response example**:

```json
{"success":true,"message":"Test email sent successfully","email":"user@example.com"}
```

### 7.6 Accept One Currency Change

- **Method / Route**: `PATCH /api/alerts/competitors/{competitor_id}/accept-currency`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `AcceptCurrencyRequest`

```json
{"currency":"GBP"}
```

**Response example**:

```json
{"success":true,"message":"Now tracking prices in GBP","competitor_id":"9ca85f64-5717-4562-b3fc-2c963f66afc1","new_currency":"GBP"}
```

### 7.7 Accept All Currency Changes

- **Method / Route**: `POST /api/alerts/accept-all-currencies`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request body**: none

```json
{"success":true,"message":"Accepted 2 currency changes","updated_count":2}
```

---

## 8. Account (`/api/account`)

### 8.1 Change Password

- **Method / Route**: `POST /api/account/change-password`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `ChangePasswordRequest`

```json
{"current_password":"OldPassword123!","new_password":"NewSecurePassword456!"}
```

**Response example**:

```json
{"message":"Password updated successfully"}
```

### 8.2 Change Email

- **Method / Route**: `POST /api/account/change-email`
- **Auth Required**: Yes
- **Success**: `200 OK`
- **Request model**: `ChangeEmailRequest`

```json
{"new_email":"updated-email@example.com"}
```

**Response example**:

```json
{"message":"Verification email sent to your new address. Please check your inbox."}
```

### 8.3 Account Settings

- **Method / Route**: `GET /api/account/settings`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"user_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6","email":"user@example.com"}
```

### 8.4 Delete Account Data

- **Method / Route**: `DELETE /api/account/delete`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"message":"Account data deleted successfully. Please log out."}
```

---

## 9. Dashboard Helper APIs

These JSON endpoints support the HTML dashboard.

### 9.1 Dashboard Stats

- **Method / Route**: `GET /api/dashboard/stats`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"products":5,"competitors":12,"alerts":3,"insights":2}
```

### 9.2 Dashboard Activity

- **Method / Route**: `GET /api/dashboard/activity`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"activity":[{"id":"5ea85f64-5717-4562-b3fc-2c963f66afe5","type":"price_drop","product_id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","retailer":"example-store.myshopify.com","old_price":1599.0,"new_price":1499.0,"change_percent":-6.25,"detected_at":"2025-01-10T02:00:00Z"}]}
```

### 9.3 Dashboard Products

- **Method / Route**: `GET /api/dashboard/products`
- **Auth Required**: Yes
- **Success**: `200 OK`

```json
{"products":[{"id":"4fa85f64-5717-4562-b3fc-2c963f66afb2","product_name":"Premium Ultrabooks","is_active":true,"competitor_count":3}]}
```

---

## 10. System Health

### 10.1 Health Check

- **Method / Route**: `GET /api/health`
- **Auth Required**: No
- **Success**: `200 OK`

```json
{"status":"healthy"}
```
