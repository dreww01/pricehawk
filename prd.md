# Product Requirements Document (PRD)

**Product:** PriceHawk
**Version:** 1.0
**Last Updated:** January 2026
**Status:** In Development (Milestones 1-4 Complete)

---

## Executive Summary

**PriceHawk** is a multi-platform price monitoring system that discovers products from competitor stores (Shopify, WooCommerce, Amazon, eBay, and custom sites), tracks their prices over time, and provides automated alerts on price changes. The system uses a plugin-based architecture to support any e-commerce platform and scales to monitor thousands of products.

---

## Product Overview

### Vision

Enable e-commerce businesses to make data-driven pricing decisions by providing real-time competitor price intelligence across all major platforms.

### Target Users

- E-commerce businesses
- Retailers and resellers
- Dropshippers
- Price analysts

### Value Proposition

- **Multi-platform support**: Discover products from any store (Shopify, Amazon, eBay, WooCommerce, custom)
- **Automated monitoring**: Daily price scraping with zero manual effort
- **AI insights**: Pattern detection and pricing recommendations (Milestone 5)
- **Instant alerts**: Email notifications on significant price changes (Milestone 6)

---

## Technical Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **API Framework** | FastAPI | High-performance async HTTP API |
| **Database** | Supabase (PostgreSQL) | Managed database with built-in auth and RLS |
| **Authentication** | Supabase Auth | JWT-based user authentication |
| **Task Queue** | Celery + Redis | Background scraping and scheduling |
| **Web Scraping** | httpx, BeautifulSoup, Playwright | Multi-platform data extraction |
| **Data Validation** | Pydantic | Type-safe request/response models |
| **AI Analysis** | Groq API | Price pattern detection (Milestone 5) |
| **Frontend** | Jinja2, HTMX, Tailwind CSS | Server-rendered UI (Milestone 8) |

---

## Core Features

1. **Multi-platform product discovery** - Find products from any e-commerce store
2. **Competitor tracking** - Monitor selected products across multiple stores
3. **Automated price scraping** - Daily background scraping via Celery
4. **Price history** - Track price changes over time
5. **AI-powered analysis** - Detect pricing patterns and trends (Milestone 5)
6. **Alert system** - Email notifications on price changes (Milestone 6)
7. **Data export** - CSV export of price history (Milestone 7)

---

## Milestone-Based Implementation

**Development Status:**
- ✅ Milestone 1: Foundation Setup (Complete)
- ✅ Milestone 2: Store Discovery System (Complete)
- ✅ Milestone 3: Product Tracking Management (Complete)
- ✅ Milestone 4: Price Scraping & Background Tasks (Complete)
- ⏳ Milestone 5: AI Price Analysis (Planned)
- ⏳ Milestone 6: Alert System (Planned)
- ⏳ Milestone 7: Data Export & Production Readiness (Planned)
- ⏳ Milestone 8: Frontend UI (Planned)

---

### **MILESTONE 1: Foundation Setup (Supabase + FastAPI)**

**Deliverables:**

- Supabase project with PostgreSQL database
- Database schema (products, competitors, price_history)
- Supabase authentication enabled (Email/Password)
- FastAPI application with modular structure
- JWT token verification middleware
- Environment configuration (.env)
- Health check endpoint

**Database Schema:**

```sql
-- Products table (tracking groups)
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    product_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Competitors table (URLs to monitor)
CREATE TABLE competitors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    retailer_name VARCHAR(100),
    alert_threshold_percent DECIMAL(5,2) DEFAULT 10.00,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Price history table
CREATE TABLE price_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    competitor_id UUID NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    price DECIMAL(10,2),
    currency VARCHAR(3) DEFAULT 'USD',
    scraped_at TIMESTAMP DEFAULT NOW(),
    scrape_status VARCHAR(20) NOT NULL, -- 'success' or 'failed'
    error_message TEXT
);

-- Indexes
CREATE INDEX idx_products_user_id ON products(user_id);
CREATE INDEX idx_products_is_active ON products(is_active);
CREATE INDEX idx_competitors_product_id ON competitors(product_id);
CREATE INDEX idx_price_history_competitor_id ON price_history(competitor_id);
CREATE INDEX idx_price_history_scraped_at ON price_history(scraped_at DESC);
```

**RLS Policies:**

```sql
-- Products: Users can only access their own products
ALTER TABLE products ENABLE ROW LEVEL SECURITY;

CREATE POLICY products_select ON products FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY products_insert ON products FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY products_update ON products FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY products_delete ON products FOR DELETE
    USING (auth.uid() = user_id);

-- Competitors: Users can only access competitors of their products
ALTER TABLE competitors ENABLE ROW LEVEL SECURITY;

CREATE POLICY competitors_select ON competitors FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM products
            WHERE products.id = competitors.product_id
            AND products.user_id = auth.uid()
        )
    );

-- Price history: Users can only read price history of their products
ALTER TABLE price_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY price_history_select ON price_history FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM competitors
            JOIN products ON products.id = competitors.product_id
            WHERE competitors.id = price_history.competitor_id
            AND products.user_id = auth.uid()
        )
    );
```

**API Endpoints:**

```
GET /api/health
```

**FastAPI Structure:**

```
/app
  /api/routes         # API endpoints
  /core               # Config, security, auth
  /db                 # Database models, Supabase client
  /services           # Business logic (discovery, scraping)
  /tasks              # Celery background tasks
main.py               # FastAPI app entry point
```

**Environment Variables:**

```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx
SUPABASE_SERVICE_KEY=xxx (for backend tasks only)
```

**Testing Checklist:**

- [ ]  Supabase project created and accessible
- [ ]  Database tables created with correct schema
- [ ]  RLS policies enabled and tested
- [ ]  User can signup/login via Supabase
- [ ]  FastAPI verifies Supabase JWT tokens
- [ ]  Protected endpoints reject invalid tokens
- [ ]  Environment variables load correctly
- [ ]  Health check endpoint returns 200

**🔒 SECURITY CHECKPOINT:**

- **Issue:** Supabase service key exposed in version control
- **Fix Required:** Store in .env file only, add .env to .gitignore, never commit
- **Issue:** RLS not enabled on tables
- **Fix Required:** Enable RLS on ALL tables, verify with test queries
- **Issue:** No HTTPS enforcement
- **Fix Required:** Use HTTPS in production, configure reverse proxy
- **Issue:** Weak JWT configuration
- **Fix Required:** Use Supabase-managed JWT with reasonable expiration (24h)

---

### **MILESTONE 2: Store Discovery System**

**Deliverables:**

- Multi-platform product discovery engine
- Platform detection system (Shopify, WooCommerce, Amazon, eBay, Generic)
- Plugin-based store handler architecture
- Unified product data model across all platforms
- Keyword filtering across product fields
- Product tracking workflow (discovery → selection → tracking)

**Architecture Pattern:**

Plugin-based handler system with strategy pattern:
1. BaseStoreHandler (abstract class defines interface)
2. Concrete handlers (ShopifyHandler, WooCommerceHandler, etc.)
3. Platform detector (tries handlers in priority order)
4. Fallback to GenericHandler for unknown stores

**API Endpoints:**

```
POST /api/stores/discover (protected - discover products from any store)
POST /api/stores/track (protected - add discovered products to tracking)
```

**Store Handlers Implemented:**

| Platform | Detection Method | Data Source |
|----------|-----------------|-------------|
| Shopify | `/products.json` endpoint | JSON API |
| WooCommerce | `/wp-json/wc/store/products` | REST API |
| Amazon | URL patterns (`/stores/`, `/s?`, `/brand/`) | HTML scraping |
| eBay | URL patterns (`/str/`, `/sch/`) | HTML scraping |
| Generic | Fallback for any HTTPS URL | HTML scraping (schema.org) |

**Unified Product Model:**

```python
@dataclass
class DiscoveredProduct:
    name: str
    price: Decimal | None
    currency: str
    image_url: str | None
    product_url: str
    platform: str              # shopify, woocommerce, amazon, ebay, custom
    variant_id: str | None     # Platform-specific variant ID
    sku: str | None
    in_stock: bool
    product_type: str | None   # Category/type
    tags: list[str]            # Product tags for filtering
    description: str | None
```

**Discovery Flow:**

1. User sends store URL + optional keyword + limit
2. System detects platform type
3. Appropriate handler fetches products
4. Products filtered by keyword (matches name, type, tags, description)
5. Returns unified product list
6. User selects products to track
7. System creates product group + competitors

**Handler Interface:**

```python
class BaseStoreHandler(ABC):
    @abstractmethod
    async def detect(self, url: str) -> bool:
        """Check if URL belongs to this platform."""

    @abstractmethod
    async def fetch_products(
        self, url: str, keyword: str | None, limit: int
    ) -> list[DiscoveredProduct]:
        """Fetch products from store."""
```

**Platform-Specific Logic:**

**Shopify:**
- Detection: HEAD request to `/products.json`
- Fetches from JSON API (fast, structured)
- Supports pagination, keyword filtering
- Extracts variants, SKUs, tags

**WooCommerce:**
- Detection: HEAD request to `/wp-json/wc/store/products`
- Uses WooCommerce REST API
- Keyword search via API parameter
- Extracts price, stock status, categories

**Amazon:**
- Detection: URL pattern matching (`amazon.com/stores/`, `/s?`)
- Playwright browser automation (handles JS rendering)
- Scrapes store/search pages (not product pages)
- Extracts price, image, ASIN from HTML

**eBay:**
- Detection: URL pattern matching (`ebay.com/str/`, `/sch/`)
- HTML scraping with BeautifulSoup
- Scrapes store/search pages
- Extracts price, item ID, condition

**Generic:**
- Detection: Always matches (fallback)
- Scrapes HTML looking for schema.org microdata
- Attempts to find price, image from structured data
- Less reliable but works on custom stores

**Testing Checklist:**

- [ ]  Detects Shopify stores correctly
- [ ]  Detects WooCommerce stores correctly
- [ ]  Detects Amazon store pages correctly
- [ ]  Detects eBay store pages correctly
- [ ]  Falls back to generic handler for unknown stores
- [ ]  Keyword filtering works across all platforms
- [ ]  Returns unified product format from all handlers
- [ ]  Handles API errors gracefully (returns error message)
- [ ]  Respects limit parameter (max 250)
- [ ]  HTTPS-only validation enforced
- [ ]  Timeout after 30 seconds
- [ ]  User-agent rotation prevents blocking

**🔒 SECURITY CHECKPOINT:**

- **Issue:** SSRF attacks via malicious URLs (localhost, internal IPs)
- **Fix Required:** Validate URLs, block private IP ranges, only allow HTTPS
- **Issue:** DoS via large/slow responses
- **Fix Required:** 30s timeout, 5MB response size limit, connection pooling
- **Issue:** XSS via unsanitized product names from external sources
- **Fix Required:** Sanitize all scraped data before storing/displaying
- **Issue:** User scrapes competitor sites too frequently
- **Fix Required:** Rate limit discovery endpoint (10 requests/minute per user)
- **Issue:** Scraper blocked by target sites
- **Fix Required:** User-agent rotation, random delays, respectful scraping practices

---

### **MILESTONE 3: Product Tracking Management**

**Deliverables:**

- Product group CRUD endpoints
- Track discovered products workflow
- Competitor URL management
- User isolation via RLS policies
- Input validation and sanitization
- Price history retrieval

**API Endpoints:**

```
GET /api/products (protected - list user's tracking groups)
GET /api/products/{product_id} (protected - get single group with competitors)
PUT /api/products/{product_id} (protected - update group name/status)
DELETE /api/products/{product_id} (protected - soft delete group)
GET /api/prices/{product_id}/history (protected - price history for all competitors)
GET /api/prices/latest/{competitor_id} (protected - latest price for one competitor)
```

**Workflow: Discovery → Tracking**

1. User discovers products via `/api/stores/discover`
2. User selects products to track
3. POST `/api/stores/track` with:
   - `group_name`: Tracking group name
   - `product_urls`: Array of discovered product URLs
   - `alert_threshold_percent`: Price change threshold
4. System creates:
   - One `products` record (the group)
   - Multiple `competitors` records (one per URL)
5. User can view/manage tracked products

**Pydantic Models:**

```python
class ProductUpdate(BaseModel):
    product_name: str | None  # Update group name
    is_active: bool | None    # Enable/disable tracking

class ProductResponse(BaseModel):
    id: str
    product_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    competitors: list[CompetitorResponse]

class CompetitorResponse(BaseModel):
    id: str
    url: str
    retailer_name: str | None
    alert_threshold_percent: Decimal
    created_at: datetime
```

**Security: RLS Enforcement**

All queries use user's JWT token (anon key):
- Products table: `WHERE user_id = auth.uid()`
- Competitors table: Joins through products table
- Price history table: Joins through competitors → products

Background tasks use service key to bypass RLS for scraping.

**Testing Checklist:**

- [ ]  User can create tracking group via `/api/stores/track`
- [ ]  User can list all their tracking groups
- [ ]  User can view single group with all competitors
- [ ]  User can update group name
- [ ]  User can soft delete group (sets `is_active = false`)
- [ ]  User cannot access another user's groups (404/403)
- [ ]  User can retrieve price history for their groups
- [ ]  Price history shows all competitors in group
- [ ]  Updated_at timestamp auto-updates
- [ ]  XSS protection: product names are sanitized

**🔒 SECURITY CHECKPOINT:**

- **Issue:** User can access other users' data by guessing UUIDs
- **Fix Required:** RLS policies enforce user_id = auth.uid() on all queries
- **Issue:** XSS via unsanitized product names
- **Fix Required:** Pydantic validators sanitize input (escape `<>` characters)
- **Issue:** User creates unlimited tracking groups (resource exhaustion)
- **Fix Required:** Enforce limit of 50 products per user (application layer)
- **Issue:** SQL injection via unvalidated inputs
- **Fix Required:** Use Supabase client (parameterized queries), never raw SQL with user input

---

### **MILESTONE 4: Price Scraping & Background Tasks**

**Deliverables:**

- Price scraper service for tracked competitor URLs
- Manual scrape endpoint for immediate scraping
- Celery worker setup with Redis broker
- Celery Beat scheduler for daily automated scraping
- Price history storage with status tracking
- Worker health monitoring endpoint
- Idempotency and retry logic

**API Endpoints:**

```
POST /api/scrape/manual/{product_id} (protected - scrape all competitors now)
GET /api/scrape/worker-health (public - check Celery worker status)
```

**Price Scraper Service:**

Uses existing store handlers from discovery system:
- Detects platform from competitor URL
- Uses same handler logic (Shopify JSON, WooCommerce API, etc.)
- Extracts current price from product page
- Handles errors gracefully (404, timeout, parsing errors)
- Stores result in `price_history` table

**Scraper Architecture:**

```python
async def scrape_competitor(competitor_id: str) -> ScrapeResult:
    1. Fetch competitor URL from database
    2. Detect platform (reuse discovery handlers)
    3. Extract price using handler
    4. Store result in price_history
    5. Return success/failure status
```

**Price History Model:**

```sql
INSERT INTO price_history (
    competitor_id,
    price,
    currency,
    scraped_at,
    scrape_status,  -- 'success' or 'failed'
    error_message   -- NULL on success, error details on failure
)
```

**Celery Background Tasks:**

**Task: `scrape_single_competitor(competitor_id)`**
- Scrapes one competitor URL
- Stores result in price_history
- Retries on failure (max 3 retries)
- Exponential backoff: 60s, 120s, 240s
- Timeout: 5 minutes

**Task: `scrape_all_products()`**
- Runs daily at 2 AM UTC via Celery Beat
- Fetches all active products
- Fetches all competitors for each product
- Queues individual scrape tasks
- Batch processing (50 at a time to avoid memory issues)
- Uses Supabase service key (bypasses RLS)

**Celery Configuration:**

```python
# app/tasks/celery_app.py
from celery import Celery
from celery.schedules import crontab

app = Celery('pricehawk', broker=REDIS_URL)

app.conf.beat_schedule = {
    'scrape-all-daily': {
        'task': 'app.tasks.scraper_tasks.scrape_all_products',
        'schedule': crontab(hour=2, minute=0),  # 2 AM UTC
    },
}
```

**Idempotency Logic:**

Before scraping, check if already scraped today:
```sql
SELECT * FROM price_history
WHERE competitor_id = ?
AND scraped_at >= CURRENT_DATE
```
If found, skip scrape and return cached result. Prevents duplicate scrapes on retry.

**Manual Scrape Flow:**

1. User calls POST `/api/scrape/manual/{product_id}`
2. Backend fetches all competitors for product
3. Queues Celery tasks for each competitor
4. Returns immediate response with task IDs
5. Tasks execute in background
6. User can check results via `/api/prices/{product_id}/history`

**Worker Health Check:**

```python
GET /api/scrape/worker-health

Response:
{
    "worker_status": "healthy",
    "ping_response": "['celery@hostname']",
    "active_tasks": 2,
    "error": null
}
```

**Testing Checklist:**

- [ ]  Manual scrape endpoint triggers scraping
- [ ]  Scraper successfully extracts prices from tracked URLs
- [ ]  Failed scrapes store error message in database
- [ ]  Celery worker starts and connects to Redis
- [ ]  Celery Beat scheduler configured correctly
- [ ]  Daily task runs at 2 AM UTC
- [ ]  Tasks retry 3 times with exponential backoff
- [ ]  Idempotency prevents duplicate scrapes same day
- [ ]  Worker health endpoint returns status
- [ ]  Batch processing prevents memory overflow
- [ ]  Service key used for background tasks (not anon key)

**🔒 SECURITY & INTEGRITY CHECKPOINT:**

- **Issue:** Celery uses anon key instead of service key
- **Fix Required:** Configure `SUPABASE_SERVICE_KEY` for Celery tasks
- **Issue:** No monitoring if worker crashes
- **Fix Required:** Health check endpoint + external monitoring (e.g., uptime checker)
- **Issue:** Memory leak when scraping thousands of URLs
- **Fix Required:** Batch processing (50 at a time), clear variables after each batch
- **Issue:** Tasks retry infinitely on permanent failures
- **Fix Required:** max_retries=3, mark as failed after exhausting retries
- **Issue:** Redis credentials exposed
- **Fix Required:** Store in .env, use strong password, TLS in production
- **Issue:** No rate limiting on scraping
- **Fix Required:** Delay between requests (2-5s), respect robots.txt
- **Issue:** Scraper blocked by target sites
- **Fix Required:** User-agent rotation, random delays, respectful scraping

**Environment Variables:**

```
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

---

### **MILESTONE 5: AI Price Analysis (Days 10-11)**

**Deliverables:**

- Groq API integration for AI analysis
- Price pattern detection (trends, cycles, anomalies)
- Actionable insights generation
- Insights stored in database
- Manual and automatic insight generation

**API Endpoints:**

```
GET /api/insights/{product_id} (protected - get all insights)
POST /api/insights/generate/{product_id} (protected - manual trigger)
```

**Database Schema:**

```sql
insights table:
- id (UUID, primary key)
- product_id (UUID, references products)
- insight_text (TEXT)
- insight_type (VARCHAR 50: 'pattern', 'alert', 'recommendation')
- confidence_score (DECIMAL 3,2, range 0.00-1.00)
- generated_at (TIMESTAMP)
```

**RLS Policy:**

- Users can only SELECT insights for their own products

**AI Analysis Requirements:**

- Fetch last 30 days of price history for all competitors
- Calculate: average price, min price, max price, price variance
- Detect patterns: weekly cycles, weekend drops, seasonal trends
- Compare competitor pricing strategies
- Generate 3-5 actionable insights per product

**AI Prompt Structure:**

```
Context: Price history data for [product_name] from [competitors]
Data: [price_history_json]
Task: Analyze pricing patterns and provide insights
Output: JSON with insights array containing {type, text, confidence}
```

**Insight Types:**

- Pattern: “Competitor A drops prices 15% every Friday”
- Alert: “Competitor B is consistently 20% cheaper”
- Recommendation: “Optimal time to adjust pricing is Thursday 6 PM”

**Testing Checklist:**

- [ ]  Groq API connection successful
- [ ]  Can generate insights for product with 30+ days of data
- [ ]  AI correctly identifies price drop pattern
- [ ]  AI detects competitor pricing strategy
- [ ]  Insights stored in database with correct product_id
- [ ]  Confidence score is between 0.00 and 1.00
- [ ]  Can retrieve insights via GET endpoint
- [ ]  Insights are in user’s language (English)
- [ ]  AI output is valid JSON (no parsing errors)
- [ ]  Rate limiting works (max 1 analysis per product per day)

**🔒 SECURITY & INTEGRITY CHECKPOINT:**

- **Issue:** Groq API key exposed in logs or error messages
- **Fix Required:** Never log API keys, sanitize error messages, store key in environment variables only
- **Issue:** AI hallucinates prices or data that doesn’t exist
- **Fix Required:** Only send real price_history data to AI, validate AI output against actual data, flag hallucinations
- **Issue:** Unlimited AI API calls drain budget
- **Fix Required:** Rate limit to 1 analysis per product per day, track API usage, set monthly budget cap
- **Issue:** AI returns malicious code or scripts in insights
- **Fix Required:** Sanitize AI output, strip HTML/JS/SQL, only allow plain text, validate JSON structure
- **Issue:** AI exposes competitor URLs or sensitive data
- **Fix Required:** Don’t include URLs in insights, only reference “Competitor A/B/C”
- **Issue:** Large AI responses consume memory
- **Fix Required:** Set max_tokens limit (500), truncate responses if needed
- **Issue:** No validation of AI output format
- **Fix Required:** Use Pydantic to validate AI JSON response, handle parsing errors gracefully

**AI Output Validation:**

- Must be valid JSON
- Must contain insights array
- Each insight must have: type, text, confidence
- Text must be < 500 characters
- Confidence must be 0.00-1.00

---

### **MILESTONE 6: Alert System (Days 12-13)**

**Deliverables:**

- Email notification service integration (SendGrid or SMTP)
- Price change detection logic
- Threshold-based alert triggering
- Alert history tracking
- Test email endpoint
- Email templates (plain text and HTML)

**API Endpoints:**

```
GET /api/alerts (protected - get user's alert history)
POST /api/alerts/test (protected - send test email)
GET /api/alerts/settings (protected - get notification preferences)
PUT /api/alerts/settings (protected - update preferences)
```

**Database Schema:**

```sql
alerts table:
- id (UUID, primary key)
- user_id (UUID, references auth.users)
- product_id (UUID, references products)
- alert_type (VARCHAR 50: 'price_drop', 'price_increase', 'pattern_detected')
- message (TEXT)
- sent_at (TIMESTAMP)
- email_status (VARCHAR 20: 'sent', 'failed', 'pending')

user_alert_settings table:
- user_id (UUID, primary key, references auth.users)
- email_enabled (BOOLEAN, default true)
- max_alerts_per_day (INTEGER, default 10)
- alert_price_drop (BOOLEAN, default true)
- alert_price_increase (BOOLEAN, default true)
- alert_patterns (BOOLEAN, default true)
```

**Alert Trigger Logic:**

- Run after each successful price scrape
- Compare new price vs previous price
- If change >= alert_threshold_percent: trigger alert
- Check user hasn’t exceeded max_alerts_per_day
- Check user has email_enabled = true

**Email Template Requirements:**

- Subject: “[Product Name] - Price Alert”
- Body includes: Product name, old price, new price, percentage change, competitor name
- Call-to-action: “View Price History”
- Unsubscribe link at bottom
- Plain text and HTML versions

**Testing Checklist:**

- [ ]  Email sent when price drops below threshold
- [ ]  Email sent when price increases above threshold
- [ ]  Email NOT sent if change is below threshold
- [ ]  Email contains correct product name and prices
- [ ]  Email contains percentage change calculation
- [ ]  No duplicate emails for same price change
- [ ]  Failed emails retry once after 5 minutes
- [ ]  Max 10 alerts per user per day enforced
- [ ]  User can disable email alerts via settings endpoint
- [ ]  Test email endpoint sends successfully
- [ ]  Unsubscribe link works (sets email_enabled = false)

**🔒 SECURITY CHECKPOINT:**

- **Issue:** Email contains clickable links that could be exploited for phishing
- **Fix Required:** Only include plain text URLs, no HTML forms, validate all links
- **Issue:** Email exposes competitor URLs to spam scrapers
- **Fix Required:** Use URL shorteners or reference “Competitor A” instead of full URL
- **Issue:** Email service credentials exposed
- **Fix Required:** Store SendGrid API key or SMTP credentials in environment variables
- **Issue:** Sending thousands of emails marks system as spam
- **Fix Required:** Rate limit to max 10 alerts per user per day, implement unsubscribe
- **Issue:** User email not verified, could spam others
- **Fix Required:** Implement email verification on signup (send confirmation link)
- **Issue:** Email content includes unescaped user input
- **Fix Required:** Escape all user-generated content (product names) in emails
- **Issue:** No SPF/DKIM records, emails go to spam
- **Fix Required:** Configure proper email authentication (SPF, DKIM, DMARC)

**Environment Variables:**

```
SENDGRID_API_KEY=...
OR
SMTP_HOST=...
SMTP_PORT=...
SMTP_USERNAME=...
SMTP_PASSWORD=...
FROM_EMAIL=noreply@yourdomain.com
```

---

### **MILESTONE 7: Data Export & Production Readiness (Days 14)**

**Deliverables:**

- CSV export endpoint for price history
- API documentation (auto-generated with FastAPI)
- Complete test suite (unit and integration tests)
- Docker configuration
- Production deployment checklist
- README with setup instructions

**API Endpoints:**

```
GET /api/export/{product_id}/csv (protected - export price history)
GET /api/docs (auto-generated Swagger UI)
GET /api/redoc (auto-generated ReDoc)
```

**CSV Export Requirements:**

- Columns: Date, Competitor, Price, Currency, Status
- Sorted by date (newest first)
- Proper CSV formatting (escaped commas, quotes)
- Filename: product_name_price_history_YYYYMMDD.csv
- Returns file download response

**Documentation Requirements:**

- All endpoints documented with descriptions
- Request/response examples for each endpoint
- Authentication instructions
- Error codes and meanings
- Rate limiting information

**Testing Requirements:**

- Unit tests for scraper price parsing
- Integration tests for each API endpoint
- Test authentication flow
- Test RLS policies
- Test Celery tasks
- Minimum 80% code coverage

**Docker Configuration:**

- Dockerfile for FastAPI app
- docker-compose.yml with: FastAPI, PostgreSQL (local dev), Redis, Celery worker, Celery beat
- Environment variables via .env file
- Health checks for all services

**Production Checklist:**

- [ ]  All environment variables set
- [ ]  Database migrations run
- [ ]  RLS policies enabled
- [ ]  HTTPS enforced
- [ ]  CORS configured for allowed origins only
- [ ]  Rate limiting enabled
- [ ]  Logging configured (errors, warnings)
- [ ]  Monitoring setup (uptime, error rates)
- [ ]  Backups automated
- [ ]  Secrets rotated from defaults

**Testing Checklist:**

- [ ]  CSV export downloads successfully
- [ ]  CSV contains all price history
- [ ]  CSV opens correctly in Excel/Google Sheets
- [ ]  API docs accessible at /docs
- [ ]  All endpoints documented
- [ ]  All tests pass
- [ ]  Docker containers start successfully
- [ ]  Can run entire system with docker-compose up
- [ ]  README instructions work for new setup

**🔒 FINAL SECURITY AUDIT:**

- **Issue:** No HTTPS enforcement in production
- **Fix Required:** Configure reverse proxy (nginx) to force HTTPS, redirect HTTP to HTTPS
- **Issue:** CORS allows requests from any origin
- **Fix Required:** Whitelist only your frontend domain(s), reject all others
- **Issue:** No database backups
- **Fix Required:** Configure automated daily backups to S3 or Supabase automated backups
- **Issue:** Error messages leak internal information (stack traces, database schema)
- **Fix Required:** Return generic error messages to users, log detailed errors server-side only
- **Issue:** No rate limiting on API endpoints
- **Fix Required:** Implement rate limiting: 100 requests per minute per IP for public endpoints, 1000 for authenticated
- **Issue:** No logging or monitoring for suspicious activity
- **Fix Required:** Log all auth failures, unusual scraping patterns, repeated failed requests
- **Issue:** Secrets and keys in version control
- **Fix Required:** Audit git history, remove any committed secrets, rotate all keys, use .env and .gitignore
- **Issue:** No dependency vulnerability scanning
- **Fix Required:** Run `pip-audit` or `safety check`, update vulnerable packages
- **Issue:** Debug mode enabled in production
- **Fix Required:** Set DEBUG=False in production environment
- **Issue:** Weak SSL/TLS configuration
- **Fix Required:** Use TLS 1.2+, strong cipher suites only

**Deployment Environment Variables:**

```
# Core
DEBUG=False
ENVIRONMENT=production

# Database
DATABASE_URL=postgresql://...
SUPABASE_URL=https://...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...

# Redis/Celery
REDIS_URL=redis://...

# AI
GROQ_API_KEY=...

# Email
SENDGRID_API_KEY=...
FROM_EMAIL=...

# Security
JWT_SECRET_KEY=...
ALLOWED_ORIGINS=https://yourdomain.com

# Monitoring
SENTRY_DSN=... (optional)
```

---

### **MILESTONE 8: Frontend UI (Days 15-18)**

**Deliverables:**

- Base HTML template with Tailwind CSS + DaisyUI
- Authentication pages (login, signup, logout)
- Dashboard with product overview
- Product management UI (add, edit, delete)
- Price history display with charts
- Insights display page
- Alert settings page
- Responsive design (mobile-friendly)

**Pages:**

```
/ (redirect to /dashboard or /login)
/login
/signup
/dashboard (main product list)
/products/{product_id} (product detail with price history)
/insights (AI insights overview)
/alerts/settings (notification preferences)
```

**Template Structure:**

```
/templates
  base.html (layout with navbar, footer)
  /auth
    login.html
    signup.html
  /dashboard
    index.html
  /products
    list.html
    detail.html
    _product_card.html (partial for HTMX)
  /insights
    index.html
  /alerts
    settings.html
  /components
    navbar.html
    flash_messages.html
```

**HTMX Interactions:**

- Add product form → submits via HTMX, appends new card
- Delete product → HTMX DELETE, removes card from DOM
- Edit product → inline editing with HTMX swap
- Load price history → HTMX lazy load on product detail page
- Refresh prices → manual scrape button with loading indicator

**Tailwind + DaisyUI Components:**

- Navbar with user menu (DaisyUI dropdown)
- Product cards (DaisyUI card)
- Forms with validation states (DaisyUI input, alert)
- Data tables for price history (DaisyUI table)
- Modal for confirmations (DaisyUI modal)
- Toast notifications (DaisyUI toast)
- Dark theme by default

**Static Files:**

```
/static
  /css
    output.css (compiled Tailwind)
  /js
    htmx.min.js
```

**Testing Checklist:**

- [ ]  Login page renders correctly
- [ ]  Signup page renders correctly
- [ ]  User can login and is redirected to dashboard
- [ ]  User can logout
- [ ]  Dashboard shows user's products
- [ ]  Can add new product via form (HTMX)
- [ ]  Can delete product (HTMX removes from DOM)
- [ ]  Product detail page shows price history
- [ ]  Price history chart renders correctly
- [ ]  Insights page displays AI insights
- [ ]  Alert settings page allows toggling preferences
- [ ]  Flash messages display for success/error
- [ ]  UI is responsive on mobile
- [ ]  All pages require authentication (redirect to /login if not)

**🔒 SECURITY CHECKPOINT:**

- **Issue:** Session tokens stored insecurely in browser
- **Fix Required:** Use HTTP-only cookies for session, set Secure flag in production
- **Issue:** CSRF attacks on form submissions
- **Fix Required:** Implement CSRF tokens on all forms
- **Issue:** XSS via user-generated content
- **Fix Required:** Jinja2 auto-escapes by default, verify all outputs use {{ }} not |safe
- **Issue:** Clickjacking attacks
- **Fix Required:** Set X-Frame-Options: DENY header
- **Issue:** Sensitive data in URL parameters
- **Fix Required:** Use POST for sensitive operations, never pass tokens in URLs

---

## **CRITICAL SUCCESS METRICS**

**Technical Metrics:**

- API response time: < 500ms (95th percentile)
- Scraping success rate: > 95%
- System uptime: > 99.5%
- Email delivery rate: > 98%
- Zero critical security vulnerabilities

**Quality Metrics:**

- All tests passing: 100%
- Code coverage: > 80%
- All milestones completed
- Zero hardcoded secrets
- Documentation complete

---

## **FOLDER STRUCTURE**

```
/app
  /api
    /routes
      __init__.py
      discovery.py         # Store discovery endpoints
      products.py          # Product tracking CRUD
      scraper.py           # Manual scrape + worker health
  /core
    __init__.py
    config.py            # Environment settings (Pydantic)
    security.py          # JWT verification middleware
  /db
    __init__.py
    models.py            # Pydantic request/response models
    database.py          # Supabase client factory
  /services
    __init__.py
    store_discovery.py   # Main discovery orchestrator
    store_detector.py    # Platform detection logic
    scraper_service.py   # Price scraping service
    /stores              # Handler plugins
      __init__.py
      base.py            # BaseStoreHandler abstract class
      shopify.py         # Shopify handler
      woocommerce.py     # WooCommerce handler
      amazon.py          # Amazon handler
      ebay.py            # eBay handler
      generic.py         # Fallback generic handler
  /tasks
    __init__.py
    celery_app.py        # Celery configuration
    scraper_tasks.py     # Background scraping tasks
  /tests
    __init__.py
    test_discovery.py    # Discovery system tests
    test_products.py     # Product endpoints tests
    test_scraper.py      # Scraper tests
    conftest.py          # Pytest fixtures
  __init__.py
/templates                 # (Future: Milestone 8)
  base.html
  /auth
    login.html
    signup.html
  /dashboard
    index.html
/static                    # (Future: Milestone 8)
  /css
    output.css
  /js
    htmx.min.js
main.py                    # FastAPI app entry point
run.py                     # Development server runner
pyproject.toml             # UV dependencies
uv.lock
.env                       # Environment variables (gitignored)
.env.example               # Template for .env
.gitignore
README.md
database_schema.sql        # Database setup SQL
```

---

## **POST-LAUNCH OPTIMIZATION (Optional - Week 3+)**

**Performance Improvements:**

- Implement caching for frequently accessed data (Redis)
- Database query optimization (explain analyze)
- Add pagination to list endpoints
- Compress API responses (gzip)

**Feature Enhancements:**

- Multi-currency support
- Webhook notifications
- Slack integration
- Price prediction using historical data
- Bulk import competitors via CSV

**Scaling Preparation:**

- Load testing (1000+ concurrent users)
- Database connection pooling
- CDN for static assets
- Horizontal scaling strategy

---

**This PRD is complete. Follow it milestone by milestone. Test each checkpoint before moving forward. Build the API right, then add UI later.**