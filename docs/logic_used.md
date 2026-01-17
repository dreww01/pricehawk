# PriceHawk - Complex Logic Documentation

## Store Discovery System

**Location:** `app/services/stores/` directory and `app/services/store_detector.py`

**Purpose:** Auto-detect store platform and extract products from any e-commerce store

**How it works:**
1. User provides store URL (e.g., `https://example-store.myshopify.com`)
2. `store_detector.py` tries each handler in priority order:
   - ShopifyHandler → checks for `/products.json`
   - WooCommerceHandler → checks for `/wp-json/wc/store/products`
   - AmazonHandler → checks URL patterns (`/stores/`, `/s?`, `/brand/`)
   - EbayHandler → checks URL patterns (`/str/`, `/sch/`)
   - GenericHandler → fallback for any HTTPS URL
3. First matching handler fetches products using platform-specific logic
4. Products are normalized to `DiscoveredProduct` model
5. Optional keyword filtering applied

**Why this approach:**
- Single endpoint handles all platforms
- Each handler encapsulates platform-specific logic
- Priority order ensures most reliable detection first
- Generic fallback catches unknown platforms using common HTML patterns

---

## Platform Detection Patterns

### Shopify Detection
**Location:** `app/services/stores/shopify.py`

```
GET {store_url}/products.json?limit=1
If status 200 and response contains "products" key → Shopify
```

### WooCommerce Detection
**Location:** `app/services/stores/woocommerce.py`

```
Try endpoints in order:
1. /wp-json/wc/store/products (Store API)
2. /wp-json/wc/v3/products (REST API v3)
3. /wp-json/wc/v2/products (REST API v2)
If any returns 200 with array → WooCommerce
```

### Amazon Detection
**Location:** `app/services/stores/amazon.py`

```
1. Check hostname contains amazon.com, amazon.co.uk, etc.
2. Check URL path matches: /stores/, /s?, /brand/, /gp/browse
Both conditions must match → Amazon store/search page
```

---

## Product Extraction Strategies

### Shopify Hybrid API Approach

**Location:** `app/services/stores/shopify.py:32-156` - `fetch_products()`, `_fetch_via_products_json()`, `_fetch_via_storefront_api()`

**Purpose:** Support both classic Shopify stores AND modern Shopify Hydrogen stores (like Fashion Nova, Gymshark) that disable `/products.json`

**How it works:**
1. **Try /products.json API first** (classic Shopify)
   - Fast path for traditional Shopify stores
   - Fetch `GET {store_url}/products.json?limit=250&page=N`
   - Parse JSON directly for: title, price, images, variants
   - Pagination: increment page until empty products array

2. **Fallback to Storefront GraphQL API** (Hydrogen stores)
   - If `/products.json` returns 0 products or fails → try Storefront API
   - Try multiple API versions for 90%+ store compatibility:
     1. `unstable` - Works on Fashion Nova and most Hydrogen stores
     2. `2024-01` - Latest stable version
     3. `2023-10` - Older stable version
     4. `2023-07` - Legacy fallback
   - POST GraphQL query with cursor-based pagination
   - Parse nested response structure (edges/nodes pattern)
   - Extract same fields as products.json

**GraphQL query structure:**
```graphql
{
  products(first: 250, after: "cursor") {
    edges {
      node {
        title, handle, price, images, variants, tags, productType
      }
    }
    pageInfo { hasNextPage, endCursor }
  }
}
```

**Why this approach:**
- Modern Shopify stores (Hydrogen) use custom React storefronts
- `/products.json` is often disabled on Hydrogen stores
- Storefront API is public (no auth required) and works everywhere
- Hybrid approach: fast path for classic stores, fallback for modern stores

**Tradeoffs:**
- Pro: Works for ALL Shopify stores (classic + Hydrogen like Fashion Nova)
- Pro: No breaking changes - keeps current fast path
- Pro: Graceful degradation (simple first, complex if needed)
- Con: More code complexity (~150 lines for GraphQL handling)
- Con: GraphQL response parsing is nested (edges/nodes pattern)
- Con: Cursor-based pagination vs page numbers

**Performance:**
- Classic Shopify: Same as before (~0.7-2s for 500 products)
- Hydrogen Shopify: Similar, GraphQL API is optimized (~1-3s)
- Shopify rate limit: ~2 requests/second (same for both APIs)

**Alternatives considered:**
- **Only Storefront API**: Simpler code, but slower for all stores (always GraphQL)
- **HTML scraping with Playwright**: Unreliable for React apps, slow, fragile
- **Skip Hydrogen stores**: Misses major brands (Fashion Nova, Gymshark, Allbirds)

### WooCommerce (JSON API)
- Store API returns price in cents (divide by minor unit)
- REST API returns price as string
- Both return: name, price, images, sku, stock status

### Amazon/eBay (HTML Scraping)
- Fetch HTML with rotating User-Agent
- Parse with BeautifulSoup
- Use platform-specific CSS selectors for product cards
- Extract: title, price, image, ASIN/item ID, stock status

### Generic (Schema.org + Common Patterns)
- Try common product card selectors: `.product`, `.product-card`, `[data-product]`
- Parse Schema.org JSON-LD for structured data
- Extract using common patterns: `[itemprop='price']`, `.price`, `.product-title`

---

## Multi-Field Keyword Search for B2B/B2C Users

**Location:** `app/services/stores/base.py:87-115` - `filter_by_keyword()` method

**Purpose:** Enable non-technical users to find products using generic category terms (e.g., "lipstick", "powder") instead of exact product names

**How it works:**
1. Split user's search query into individual words (e.g., "red lipstick" → ["red", "lipstick"])
2. For each product, build searchable text from multiple fields:
   - Product name/title
   - Product type (e.g., "Lip Trio", "Eyeshadow Palette")
   - Tags (category/collection tags)
   - Description (HTML body text)
3. Check if ANY word from the query appears in the combined searchable text
4. Return all matching products

**Example:**
- ColourPop product: title="Heavenly Nudes", product_type="Lip Trio", description="liquid lipstick..."
- User searches: "lipstick"
- Match: "lipstick" found in description → product returned

**Why this approach:**
- Beauty products often use branded names ("Heavenly Nudes") not generic terms ("lipstick")
- Product metadata (type, tags, description) contains the category keywords
- Searches where the data actually is, not just the title
- Works for B2B/B2C non-technical users who search by category

**Tradeoffs:**
- Pro: Finds products with generic terms like "lipstick", "eyeshadow", "powder"
- Pro: Searches multiple fields (title, type, tags, description)
- Pro: Split words match independently ("red lipstick" matches "lipstick" or "red")
- Con: May match noise from HTML descriptions
- Con: Could return many results for common words

**Alternative approaches considered:**
- Full-text search (Elasticsearch) - overkill for this use case
- Exact substring match only - too strict for generic terms
- Product taxonomy - requires manual categorization

---

## Fetch-All-Then-Filter Strategy for API Stores

**Location:**
- `app/services/stores/shopify.py:31-87` - `fetch_products()` method
- `app/services/stores/woocommerce.py:41-100` - `fetch_products()` method

**Purpose:** Guarantee products are found regardless of catalog position (e.g., "headphone" at position #99)

**How it works:**
1. Fetch ALL products from store API (up to `max_products_fetch` limit, default 500)
2. Filter products using multi-field keyword search
3. Apply user's limit to filtered results

**Configuration:**
- Add `MAX_PRODUCTS_FETCH=1000` to `.env` to increase limit from 500 to 1000

**Example problem solved:**
- Store has 99 products, "Wireless Headphone" is #99
- User searches "headphone" with limit=50
- Old: Fetched first 50, filtered, found 0 (headphone never fetched)
- New: Fetched all 99, filtered, found 1 ✅

**Why this approach:**
- Shopify/WooCommerce stores typically have <500 products
- API is fast (~0.7-2s for 500 products)
- Ensures complete, predictable search results
- Users never miss products due to pagination

**Tradeoffs:**
- Pro: Always finds all matching products
- Pro: Consistent results (not dependent on product ordering)
- Pro: Better UX for non-technical B2B/B2C users
- Con: Slightly slower for very large stores (500+ products)
- Con: Higher memory usage (loads full catalog)

**Platform differences:**
- **Shopify/WooCommerce**: Fetch-all strategy (API-based, small catalogs)
- **Amazon/eBay**: Pass keyword to platform search (HTML scraping, millions of products)

**Performance:**
- Small store (99 products): ~0.7s
- Medium store (250 products): ~1.5s
- Large store (500 products): ~2-3s

---

## JWT Verification with JWKS (ES256)

**Location:** `app/core/security.py` - `verify_token()` function

**Purpose:** Verify Supabase JWT tokens for authenticated API requests

**How it works:**
1. Supabase uses ES256 (ECDSA) algorithm for JWT signing
2. We fetch the public key from Supabase's JWKS endpoint: `{sb_url}/auth/v1/.well-known/jwks.json`
3. PyJWKClient extracts the correct key based on the token's `kid` (key ID) header
4. Token is decoded and verified with the public key
5. JWKS client is cached with `@lru_cache` to avoid repeated HTTP requests

**Why this approach:**
- Industry standard for asymmetric JWT verification
- JWKS allows key rotation without code changes
- Caching prevents performance overhead

---

## Supabase Client with RLS Authentication

**Location:** `app/db/database.py` - `get_supabase_client()` function

**Purpose:** Create Supabase client that respects Row Level Security policies

**Two modes:**
- With `access_token`: Respects RLS, user sees only their data
- Without (service key): Bypasses RLS, for background tasks only

**Why this approach:**
- RLS is enforced at database level (secure by default)
- Even if API has bugs, users can't access others' data

---

## Price Parsing for Multiple Formats

**Location:** `app/services/scraper_service.py` - `parse_price()` function

**Purpose:** Convert price text from different locales to Decimal

**How it works:**
1. Detect currency symbol ($ → USD, £ → GBP, € → EUR)
2. Remove currency symbols and letters
3. Handle European format: `1.234,56` → `1234.56`
4. Handle US format: `1,234.56` → `1234.56`
5. Return Decimal for precise arithmetic

**Why Decimal:**
- Avoids floating-point errors ($19.99 won't become $19.98999...)
- Required for financial calculations

---

## Manual Scrape vs Whitelisted Scrape

**Discovery Scraping (new):**
- `app/services/stores/*.py` handlers
- Accepts ANY HTTPS URL
- Used for discovering products from store pages
- Returns product list with prices

**Whitelisted Scraping (legacy):**
- `app/services/scraper_service.py`
- Domain whitelist: Amazon, eBay, Walmart only
- Used for tracking individual product prices
- Validates URL against whitelist + blocks private IPs

---

## Celery Background Task Scheduler

**Location:** `app/tasks/celery_app.py` and `app/tasks/scraper_tasks.py`

**Purpose:** Automate daily price scraping without blocking the API server

**How it works:**
1. Beat scheduler triggers `scrape_all_products` daily at 2 AM UTC
2. Each competitor is queued as a separate task
3. Batch processing in groups of 50 to limit memory
4. Retry logic: 3 retries with exponential backoff (60s → 120s → 240s)
5. Idempotency check prevents duplicate scrapes same day

**Key configuration:**
- `task_soft_time_limit=270` - Soft timeout at 4.5 minutes
- `task_time_limit=300` - Hard timeout at 5 minutes
- `task_acks_late=True` - Acknowledge after completion

---

## API Flow: Store Discovery → Track → Monitor

```
1. POST /api/stores/discover
   - User provides store URL + optional keyword filter
   - System auto-detects platform
   - Returns list of products with prices

2. POST /api/stores/track
   - User selects products to track
   - Creates product group + competitors in DB
   - Returns group ID

3. Background: Celery Beat runs daily at 2 AM UTC
   - Scrapes all active competitors
   - Saves prices to price_history table

4. GET /api/prices/{product_id}/history
   - User views price history for tracked products
   - Shows price trends over time
```

---

## Defense in Depth Authorization (Application + Database)

**Location:** `app/api/routes/products.py` and `rls_policies.sql`

**Purpose:** Prevent users from accessing/modifying other users' product data, even if application has bugs

**How it works:**

### Layer 1: Application-Level Checks (FastAPI)
Every product route filters by `user_id`:
```python
client.table("products")
    .select("*")
    .eq("id", product_id)
    .eq("user_id", current_user.id)  # ← Explicit ownership check
    .execute()
```

**Applied in:**
- `GET /api/products/{product_id}` - Line 84
- `PUT /api/products/{product_id}` - Lines 109, 128
- `DELETE /api/products/{product_id}` - Lines 144, 150

**Behavior:** Returns 404 if `product_id` doesn't belong to `current_user.id`

### Layer 2: Database-Level RLS (Supabase)
Postgres Row Level Security policies enforce ownership at database level:

```sql
-- products table
CREATE POLICY "Users can view own products"
    ON products FOR SELECT
    USING (user_id = auth.uid());

-- competitors table (via join)
CREATE POLICY "Users can view own competitors"
    ON competitors FOR SELECT
    USING (
        product_id IN (
            SELECT id FROM products WHERE user_id = auth.uid()
        )
    );
```

**Behavior:** Database literally blocks queries returning rows where `user_id ≠ auth.uid()`

**Why this approach (defense in depth):**
- Application checks = clear intent, easier debugging, explicit 404 errors
- RLS = safety net if application forgets checks or has bugs
- Both together = industry standard for sensitive user data
- If FastAPI route accidentally removes `.eq("user_id")`, RLS still blocks access
- Logs show which layer caught unauthorized access

**Tradeoffs:**
- Pro: Two independent security layers (app + DB)
- Pro: Learning safety net - mistakes don't leak data
- Pro: RLS policies are centralized and auditable
- Pro: Works even if application layer is bypassed (e.g., SQL injection)
- Con: Slightly more setup (10 min: SQL policies + Python filters)
- Con: Need to understand both application and database security

**Alternative approaches:**
- **RLS only:** Simpler code, but errors are cryptic (DB rejection, not 404)
- **Application only:** Easier debugging, but one forgotten check = data leak
- **Service accounts with manual joins:** Complex, error-prone, not scalable

**Testing:**
See `tests/test_product_authorization.py` for cross-user access tests

---

## AI Price Analysis with Groq Llama 3.3 70B

**Location:** `app/services/ai_service.py` - `AIService` class

**Purpose:** Generate actionable pricing insights by analyzing competitor price history using AI pattern detection

**How it works:**
1. **Fetch price history** (30 days for all competitors)
2. **Group and aggregate** data by competitor:
   - Calculate average, min, max prices
   - Extract current vs first price
   - Include last 10 data points per competitor
3. **Format for AI** with structured prompt:
   - Competitor statistics (no URLs, domain only for privacy)
   - Analysis period and data quality metrics
4. **Call Groq API** with Llama 3.3 70B:
   - System: "You are a price analysis expert"
   - User: Formatted price data + instructions
   - Response format: `{"insights": [...]}`
   - Temperature: 0.7 (creative but focused)
   - Max tokens: 500 (concise insights)
5. **Validate AI output** using Pydantic-style validation:
   - Check insight type is in `["pattern", "alert", "recommendation"]`
   - Sanitize text (remove HTML/JS/SQL keywords)
   - Validate confidence score is 0.00-1.00
   - Limit text to 500 characters
   - Cap at 5 insights max
6. **Store insights** in database using service key (bypasses RLS)

**Rate Limiting:**
- Check if insights generated today (query `insights` table for product_id + today's date)
- Reject with error if already generated (prevents duplicate API calls)
- Allows 1 generation per product per day

**Why this approach:**

**Model Selection - Groq Llama 3.3 70B:**
- Free tier: 30 requests/min (sufficient for 1/product/day limit)
- Strong reasoning for pattern detection in time series data
- Reliable JSON output with `response_format={"type": "json_object"}`
- 128K context window (handles 30 days × multiple competitors easily)
- Fast inference via Groq's LPU (Language Processing Unit) technology

**Data Formatting:**
- Domain extraction anonymizes competitor URLs (privacy)
- Last 10 data points balance detail vs token usage
- Statistics provide quick pattern recognition cues for AI

**Output Validation:**
- AI can hallucinate or return malformed JSON
- Sanitization prevents XSS/injection attacks from AI-generated text
- Confidence scoring allows frontend to filter low-quality insights

**Tradeoffs:**
- Pro: Free tier supports production use (30 req/min >> 1/day/product)
- Pro: Strong pattern detection for pricing trends and cycles
- Pro: JSON mode prevents parsing errors
- Pro: Fast responses (~2-4 seconds)
- Con: Rate limit to 1/day (can't regenerate if unsatisfied)
- Con: Quality depends on sufficient price history (needs 7+ days minimum)
- Con: May miss domain-specific nuances (e.g., seasonal beauty trends)

**Alternatives considered:**
- **OpenAI GPT-4o**: Better quality, costs $0.15/1M tokens (~$0.001/request)
- **Mixtral 8x7B (Groq)**: Faster but lower quality for complex reasoning
- **Gemma 2 9B (Groq)**: Fastest but too small for multi-competitor analysis
- **Claude 3.5 Sonnet**: Excellent quality, not available on Groq free tier

---

## Chart Data Formatting Service

**Location:** `app/services/chart_service.py` - `ChartService.get_chart_data()`

**Purpose:** Transform raw price history database rows into structured chart-ready JSON for frontend visualization libraries (Chart.js, Plotly, etc.)

**How it works:**
1. **Fetch product details** with competitors (using RLS-filtered query)
2. **For each competitor**, fetch price history for specified time range (default 30 days)
3. **Build data points array** for time series:
   - Convert ISO timestamps to Python datetime objects
   - Include price, currency, status per data point
   - Track earliest/latest dates for range calculation
4. **Calculate statistics per competitor**:
   - Average price (mean of all successful scrapes)
   - Min/max prices (for chart Y-axis bounds)
   - Current price (latest successful scrape)
   - Price change percentage: `((current - first) / first) × 100`
5. **Return structured response** with:
   - Product metadata (id, name)
   - Array of competitor chart data (one per competitor)
   - Global date range (earliest to latest across all competitors)
   - Total data points count

**Data Structure:**
```json
{
  "product_id": "uuid",
  "product_name": "iPhone 15 Pro",
  "competitors": [
    {
      "competitor_id": "uuid",
      "competitor_name": "amazon.com",
      "data_points": [
        {"timestamp": "2025-01-01T00:00:00", "price": 999.00, "currency": "USD", "status": "success"},
        {"timestamp": "2025-01-02T00:00:00", "price": 989.00, "currency": "USD", "status": "success"}
      ],
      "average_price": 994.00,
      "min_price": 989.00,
      "max_price": 999.00,
      "current_price": 989.00,
      "price_change_percent": -1.00
    }
  ],
  "date_range_start": "2025-01-01T00:00:00",
  "date_range_end": "2025-01-02T00:00:00",
  "total_data_points": 2
}
```

**Why this approach:**

**Frontend-Agnostic Format:**
- Works with Chart.js, Plotly, Recharts, or any charting library
- Includes both raw data (for custom visualizations) and statistics (for annotations)
- Timestamps as ISO strings (universally parsable)

**Pre-Calculated Statistics:**
- Frontend doesn't need to iterate price arrays
- Reduces client-side computation
- Consistent calculations across all clients

**Failed Scrape Handling:**
- Status field allows frontend to show gaps in timeline
- Only successful prices included in statistics
- Preserves data integrity (doesn't interpolate missing values)

**Tradeoffs:**
- Pro: Ready to use for any chart library
- Pro: Includes metadata for annotations (min/max markers, avg lines)
- Pro: Price change % useful for at-a-glance comparison
- Pro: Handles missing data gracefully (failed scrapes)
- Con: Slightly larger payload than raw rows (includes computed fields)
- Con: Frontend can't customize statistics calculation
- Con: 30-day default may be too much data for mobile (100+ points/competitor)

**Frontend Usage Examples:**

**Chart.js:**
```javascript
const chartData = {
  datasets: response.competitors.map(comp => ({
    label: comp.competitor_name,
    data: comp.data_points.map(p => ({x: p.timestamp, y: p.price}))
  }))
}
```

**Plotly:**
```javascript
const traces = response.competitors.map(comp => ({
  x: comp.data_points.map(p => p.timestamp),
  y: comp.data_points.map(p => p.price),
  name: comp.competitor_name
}))
```

**Alternatives considered:**
- **Raw database rows**: Simpler backend, but frontend must parse and calculate
- **Static chart images**: Server-side rendering (Plotly/Matplotlib), but not interactive
- **Aggregated only (no raw points)**: Smaller payload, but can't render time series
- **WebSocket streaming**: Real-time updates, overkill for daily scraping cadence

---

## Email Alert System

### Digest-Based Alert Architecture

**Location:** `app/services/alert_service.py`, `app/services/email_service.py`, `app/tasks/scraper_tasks.py:send_alert_digests()`

**Purpose:** Notify users of price changes via scheduled digest emails instead of immediate per-alert emails, allowing user control over notification frequency (6, 12, or 24 hours)

**How it works:**

#### 1. Alert Detection (After Each Scrape)
**Location:** `app/services/alert_service.py:check_price_change_and_alert()` (called from `app/services/scraper_service.py:scrape_and_check_alerts()`)

```
Scrape → Store Price → Check for Alert Trigger → Create Pending Alert
```

**Flow:**
1. After successful price scrape, compare new price vs previous price
2. Calculate percentage change: `(new_price - old_price) / old_price × 100`
3. Check if change exceeds user's `alert_threshold_percent` (from `competitors` table)
4. **Additional check**: If change is below threshold BUT absolute change ≥ $5, still trigger alert
5. Verify user alert settings:
   - `email_enabled = true`
   - `alert_price_drop = true` (if drop) or `alert_price_increase = true` (if increase)
6. Check pending alerts count < 100 per user (rate limiting at detection level)
7. If all checks pass, insert row into `pending_alerts` table with `included_in_digest = false`
8. Otherwise, skip alert creation (log reason: below threshold, user disabled, rate limited, etc.)

**Edge Cases Handled:**
- No previous price (first scrape): Skip alert
- Previous price was $0: Skip alert (avoid divide-by-zero)
- User has 100+ pending alerts: Skip new alerts until digests are sent
- Scrape failed: No alert detection (only runs on successful scrapes)

**Minimum Significant Change:**
```python
# app/services/alert_service.py:AlertConfig
MIN_SIGNIFICANT_CHANGE_AMOUNT = Decimal("5.00")  # Configurable in AlertConfig class
```
This catches scenarios where threshold is 10% but $100 → $105 (5%) is still significant.

#### 2. Pending Alerts Storage

**Table:** `pending_alerts`
```sql
- id (UUID)
- user_id (UUID) -- Foreign key to auth.users
- product_id (UUID) -- Foreign key to products
- competitor_id (UUID) -- Foreign key to competitors
- alert_type ('price_drop' | 'price_increase')
- old_price (DECIMAL)
- new_price (DECIMAL)
- price_change_percent (DECIMAL)
- threshold_percent (DECIMAL) -- What threshold triggered this alert
- detected_at (TIMESTAMPTZ)
- included_in_digest (BOOLEAN) -- false until sent in email
```

**Why separate pending table:**
- Decouples alert detection from email sending
- Allows batching multiple alerts into one digest
- Supports user-configurable digest frequencies
- Enables retry logic if email fails
- Maintains audit trail of detected alerts even if email fails

#### 3. User Alert Settings

**Table:** `user_alert_settings`
```sql
- user_id (UUID PRIMARY KEY)
- email_enabled (BOOLEAN) -- Master on/off switch
- digest_frequency_hours (INTEGER) -- 6, 12, or 24 (CHECK constraint)
- alert_price_drop (BOOLEAN) -- Notify on drops?
- alert_price_increase (BOOLEAN) -- Notify on increases?
- last_digest_sent_at (TIMESTAMPTZ) -- Tracks when last email sent
- created_at/updated_at (TIMESTAMPTZ)
```

**Default values** (created on first GET /api/alerts/settings):
```python
{
    "email_enabled": true,
    "digest_frequency_hours": 24,  # Daily
    "alert_price_drop": true,
    "alert_price_increase": true
}
```

**Frontend Control:**
Users can change `digest_frequency_hours` via API:
- 6 hours: 4 emails/day max (high-frequency traders)
- 12 hours: 2 emails/day (moderate monitoring)
- 24 hours: 1 email/day (default, low-noise)

**Easy Configuration Block:**
```python
# app/services/alert_service.py:AlertConfig
class AlertConfig:
    MIN_SIGNIFICANT_CHANGE_AMOUNT = Decimal("5.00")  # Change to adjust min alert trigger
    MAX_PENDING_ALERTS_PER_USER = 100  # Max pending before blocking new alerts
    CLEANUP_PENDING_AFTER_DAYS = 7  # Auto-delete old pending alerts
```

```python
# app/services/email_service.py:EmailConfig
class EmailConfig:
    MAX_ALERTS_PER_DIGEST = 50  # Max alerts in one email
    MAX_SUBJECT_LENGTH = 200  # Email subject truncation
    MAX_PRODUCT_NAME_LENGTH = 150  # Product name truncation
    MAX_RETRIES = 2  # Email send retries
    RETRY_DELAY_SECONDS = 5  # Delay between retries
```

#### 4. Digest Scheduling (Celery Beat)

**Location:** `app/tasks/celery_app.py:beat_schedule`

```python
"hourly-send-alert-digests": {
    "task": "app.tasks.scraper_tasks.send_alert_digests",
    "schedule": crontab(minute=0),  # Every hour at :00
}
```

**Why hourly checks:**
- Supports 6-hour digest frequency (check every hour, send every 6 hours)
- Minimal overhead (only queries users with pending alerts)
- Fast execution (~5-10s for 100 users)

#### 5. Digest Sending Logic

**Location:** `app/tasks/scraper_tasks.py:send_alert_digests()`

**Flow:**
1. **Query users due for digest** (`AlertService.get_users_due_for_digest()`):
   - Fetch all users where `email_enabled = true`
   - For each user, check if `(now - last_digest_sent_at) >= digest_frequency_hours`
   - Count pending alerts (WHERE `included_in_digest = false`)
   - Return users with: `pending_count > 0` AND time elapsed
2. **For each user:**
   - Fetch pending alerts from database (up to 50 per digest)
   - Extract user name from email (`user@example.com` → `"user"`)
   - Format alerts for email template
   - Call `EmailService.send_price_alert_digest()`
3. **On email success:**
   - Mark alerts as `included_in_digest = true`
   - Insert row into `alert_history` table (audit trail)
   - Update `last_digest_sent_at` in user settings
4. **On email failure:**
   - Insert row into `alert_history` with `email_status = 'failed'`
   - Leave alerts as `included_in_digest = false` (retry next hour)
   - Log error for monitoring

**Rate Limiting:**
- Detection level: Max 100 pending alerts per user
- Email level: Max 50 alerts per digest
- Send frequency: User-controlled (6/12/24 hours)

**Idempotency:**
- Uses `last_digest_sent_at` + `included_in_digest` flag
- If task runs twice in same hour, won't send duplicate emails
- If email fails, alerts remain pending for next attempt

#### 6. Email Service (Resend SMTP)

**Location:** `app/services/email_service.py:EmailService`

**Why Resend:**
- Free test SMTP for development (no domain required)
- Simple SMTP integration (no complex API)
- Production-ready when you add custom domain
- Good deliverability (SPF/DKIM handled by Resend)

**Email Template:**
- HTML version: Modern design with color-coded price changes (green drops, red increases)
- Plain text version: Fallback for email clients without HTML support
- Both versions include:
  - Product name + competitor name
  - Old price → New price
  - Percentage change with arrow (↓ for drops, ↑ for increases)
  - Digest period (6/12/24 hours)

**Security Features:**
1. **HTML escaping**: All user data (product names, prices) sanitized
2. **No clickable URLs**: Email doesn't include competitor URLs (prevents phishing)
3. **Rate limiting**: Max 50 alerts per email
4. **Input validation**: Email addresses validated, headers checked for injection
5. **Retry logic**: 2 retries with 5s delay
6. **Error logging**: All failures logged with sanitized error messages

**Configuration (Easy to Tweak):**
```python
# app/services/email_service.py:EmailConfig
smtp_host = "smtp.resend.com"  # Change for different provider
smtp_port = 587  # Standard TLS port
smtp_username = "resend"  # Resend default
from_email = "noreply@pricehawk.local"  # Change when you have domain
from_name = "PriceHawk Alerts"  # Shown in email client
```

#### 7. Alert History Tracking

**Table:** `alert_history`
```sql
- id (UUID)
- user_id (UUID)
- digest_sent_at (TIMESTAMPTZ)
- alerts_count (INTEGER) -- How many alerts in this digest
- email_status ('sent' | 'failed' | 'pending')
- error_message (TEXT) -- Null on success, error on failure
- alert_ids (UUID[]) -- Array of pending_alert IDs included
```

**Purpose:**
- Audit trail of all sent digests
- Debugging failed email deliveries
- User can view email history via API
- Allows re-sending specific digest if needed

#### 8. Cleanup Task

**Location:** `app/tasks/scraper_tasks.py:cleanup_old_alerts()`

**Schedule:** Daily at 3 AM UTC

**What it does:**
- Deletes pending alerts WHERE `included_in_digest = true` AND `detected_at < 7 days ago`
- Keeps database size manageable
- Configurable cleanup period: `AlertConfig.CLEANUP_PENDING_AFTER_DAYS`

---

### Alert System Tradeoffs

**Chosen Approach: Digest-Based with User-Controlled Frequency**

**Pros:**
- **User control**: 6/12/24 hour options balance urgency vs noise
- **Reduced email spam**: Batch alerts instead of 1 email per price change
- **Rate limiting at multiple levels**: Detection (100), digest (50), frequency (user choice)
- **Resilient**: Failed emails retry next hour automatically
- **Audit trail**: Full history of alerts and emails sent
- **Decoupled**: Alert detection independent from email sending

**Cons:**
- **Not real-time**: 6-hour minimum delay (vs immediate alerts)
- **Complexity**: 3 tables (pending_alerts, user_alert_settings, alert_history)
- **Hourly task overhead**: Celery Beat checks every hour (minimal cost)
- **No instant "price drop now" alerts**: Cannot notify within seconds

**Why NOT immediate alerts:**
- Email spam: User tracking 50 products could get 50 emails/day
- Email provider limits: SendGrid free tier = 100 emails/day
- Poor UX: Users disable notifications due to volume
- No batching: Can't show "3 price drops today" overview

**Why NOT WebSockets/Push:**
- Overkill for daily scraping cadence
- Users don't need second-by-second updates for e-commerce prices
- Adds significant infrastructure complexity (Redis pub/sub, WebSocket server)

**Why NOT Slack/Discord webhooks:**
- Most users don't have Slack/Discord for business
- Email is universal and expected for price alerts
- Can add webhooks in Milestone 7 as additional channel

**Alternatives Considered:**

1. **Immediate email per alert:**
   - Pro: Real-time notifications
   - Con: Email spam, user disables after 2 days
   - Con: Hits email provider rate limits fast

2. **Daily digest only (no 6/12 hour options):**
   - Pro: Simpler (one schedule)
   - Con: 24 hours too slow for active traders/resellers
   - Con: Less flexible UX

3. **Push notifications (mobile app):**
   - Pro: Native mobile experience, better engagement
   - Con: Requires mobile app development (out of scope)
   - Con: Users must install app

4. **SMS alerts:**
   - Pro: Highest open rate (98% vs 20% email)
   - Con: Costs $0.01-0.05/SMS (expensive at scale)
   - Con: Requires phone number collection (privacy concern)

---

### Security Considerations (Implemented)

**Email Injection Prevention:**
- Email addresses validated (must contain @ and .)
- Headers checked for `\n` and `\r` (injection attempt)
- All user data HTML-escaped before template insertion

**XSS Prevention:**
- Product names, competitor names sanitized
- Dangerous patterns removed: `<script`, `javascript:`, `onerror=`
- Plain text fallback for non-HTML clients

**Rate Limiting:**
- Detection: 100 pending alerts per user max
- Digest: 50 alerts per email max
- Frequency: User-controlled (6/12/24 hours)
- No user can exhaust email quota alone

**Privacy:**
- Competitor URLs NOT included in email (prevents phishing)
- Only domain shown (e.g., "amazon.com" not full URL)
- User emails stored in Supabase (encrypted at rest)
- SMTP password never logged

**RLS Enforcement:**
- Alert detection uses service key (bypasses RLS to access all users)
- API endpoints use user token (RLS enforced)
- Users can only see their own pending alerts and history

**Error Handling:**
- Email failures logged with sanitized messages (no SMTP password exposure)
- Failed digests retried next hour
- Alert history tracks failures for debugging

---

### Email Configuration (Resend)

**Location:** `.env` file

```bash
# Email Configuration (Resend SMTP Service)
# Get your API key from https://resend.com/api-keys
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=re_YourResendAPIKey  # Your Resend API key here
FROM_EMAIL=noreply@yourdomain.com  # Change when you have domain
FROM_NAME=PriceHawk Alerts
```

**Setup Steps:**
1. Sign up at https://resend.com (free tier: 100 emails/day, 3,000/month)
2. Get API key from dashboard
3. Add to `.env` as `SMTP_PASSWORD`
4. For production: Add custom domain in Resend dashboard for better deliverability

**Test SMTP:**
```bash
POST /api/alerts/test
{
  "email": "your-email@example.com"  # Optional, defaults to user's email
}
```

---

### API Endpoints (Milestone 6)

#### GET /api/alerts/settings
Get user's alert settings (creates defaults if none exist)

**Response:**
```json
{
  "user_id": "uuid",
  "email_enabled": true,
  "digest_frequency_hours": 24,
  "alert_price_drop": true,
  "alert_price_increase": true,
  "last_digest_sent_at": "2025-01-06T10:00:00Z",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-06T08:00:00Z"
}
```

#### PUT /api/alerts/settings
Update alert settings (partial updates supported)

**Request:**
```json
{
  "email_enabled": false,  // Disable all emails
  "digest_frequency_hours": 6  // Change to 6-hour digests
}
```

#### GET /api/alerts/pending
View pending alerts not yet sent

**Response:**
```json
{
  "alerts": [
    {
      "id": "uuid",
      "product_name": "iPhone 15 Pro",
      "competitor_name": "amazon.com",
      "alert_type": "price_drop",
      "old_price": 999.00,
      "new_price": 949.00,
      "price_change_percent": -5.01,
      "currency": "USD",
      "detected_at": "2025-01-06T14:30:00Z"
    }
  ],
  "total": 1
}
```

#### GET /api/alerts/history
View sent digest history

**Response:**
```json
{
  "history": [
    {
      "id": "uuid",
      "digest_sent_at": "2025-01-06T10:00:00Z",
      "alerts_count": 5,
      "email_status": "sent",
      "error_message": null
    }
  ],
  "total": 1
}
```

#### POST /api/alerts/test
Send test email to verify configuration

**Request:**
```json
{
  "email": "test@example.com"  // Optional
}
```

**Response:**
```json
{
  "success": true,
  "message": "Test email sent to test@example.com",
  "email": "test@example.com"
}
```

---

### Celery Beat Schedule (Complete)

**Location:** `app/tasks/celery_app.py:beat_schedule`

```python
{
    # Daily scrape at 2 AM UTC
    "daily-scrape-all-products": {
        "task": "app.tasks.scraper_tasks.scrape_all_products",
        "schedule": crontab(hour=2, minute=0),
    },

    # Send alert digests every hour
    "hourly-send-alert-digests": {
        "task": "app.tasks.scraper_tasks.send_alert_digests",
        "schedule": crontab(minute=0),  # Every hour at :00
    },

    # Clean up old alerts daily at 3 AM UTC
    "daily-cleanup-old-alerts": {
        "task": "app.tasks.scraper_tasks.cleanup_old_alerts",
        "schedule": crontab(hour=3, minute=0),
    },
}
```

**Task Execution Flow:**
```
2:00 AM UTC: Scrape all products (triggers alert detection)
3:00 AM UTC: Clean up old pending alerts (7+ days)
Every hour: Check users due for digest, send emails
```

---

### Database Schema (Milestone 6 Tables)

See `database_schema.sql` for complete SQL. Key tables:

1. **pending_alerts** - Stores detected price changes awaiting digest
2. **alert_history** - Audit trail of sent digest emails
3. **user_alert_settings** - User notification preferences

**Indexes for Performance:**
```sql
CREATE INDEX idx_pending_alerts_user_id ON pending_alerts(user_id);
CREATE INDEX idx_pending_alerts_included ON pending_alerts(included_in_digest);
CREATE INDEX idx_alert_history_user_id ON alert_history(user_id);
```

**RLS Policies:**
- Users can SELECT own pending_alerts and alert_history
- Service role can INSERT/UPDATE/DELETE pending_alerts (for background tasks)
- Users can INSERT/UPDATE own user_alert_settings

---

## Lazy Service Initialization

**Location:** `app/services/ai_service.py:15-27`, `app/services/email_service.py:53-67`

**Purpose:** Prevent slow server startup by deferring expensive client creation and config validation until first use

**How it works:**

### AIService (Groq Client)
```python
# Before: Client created at import time (slow)
def __init__(self):
    self.client = Groq(api_key=settings.groq_api_key)  # Runs at import!

# After: Client created on first use (fast startup)
def __init__(self):
    self._client = None

@property
def client(self):
    if self._client is None:
        settings = get_settings()
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY not configured")
        self._client = Groq(api_key=settings.groq_api_key)
    return self._client
```

### EmailService (SMTP Config Validation)
```python
# Before: Validation in __init__ (blocks startup if not configured)
def __init__(self):
    self._validate_config()  # Raises if SMTP_PASSWORD missing

# After: Validation deferred to method calls
def __init__(self):
    self._validated = False

def _ensure_configured(self):
    if self._validated:
        return
    if not self.config.smtp_password:
        raise ValueError("SMTP_PASSWORD not configured")
    self._validated = True

def send_test_email(self, to_email):
    self._ensure_configured()  # Only validates when actually sending
    # ... send email
```

**Why this approach:**
- Services are imported at module level in route files
- `__init__` runs at import time (before server starts)
- Creating API clients or validating config at import blocks startup
- Lazy init defers work until the service is actually used
- Server starts instantly, errors surface when features are used

**Tradeoffs:**
- Pro: Fast server startup (instant vs 2-5s)
- Pro: Server works even if optional services (email, AI) aren't configured
- Pro: Standard pattern for expensive resources (DB connections, API clients)
- Pro: Keeps imports at top of files (clean code)
- Con: Errors appear at runtime instead of startup
- Con: Slightly more code (`@property` wrapper)

**When to use lazy init:**
- API client creation (HTTP connections)
- Config validation that may fail
- Database connection pooling
- Any I/O in `__init__`

**When NOT to use:**
- Simple attribute assignment
- Pure computation
- Required dependencies (fail fast is better)

---

## Dual Authentication (Bearer Token + Cookie) for Export Endpoint

**Location:** `app/api/routes/export.py:69-96` - `get_user_from_request()`

**Purpose:** Support both API clients (Bearer token) and browser downloads (cookie) for the CSV export endpoint

**The Problem:**
- Export endpoint uses Bearer token authentication
- Browser direct downloads (clicking link, typing URL) can't send Authorization headers
- Result: 401 "Not authenticated" when user tries to download CSV from browser

**How it works:**

```python
async def get_user_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> tuple[CurrentUser, str]:
    token = None

    # Priority 1: Bearer token (API clients, fetch with headers)
    if credentials:
        token = credentials.credentials
    # Priority 2: Cookie (browser direct downloads)
    else:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Verify token and return user
    user = await verify_token_string(token)
    return user, token
```

**Key design decisions:**
1. `HTTPBearer(auto_error=False)` - Returns None instead of 401 when no header
2. Cookie name `access_token` - Matches frontend cookie storage
3. Returns tuple `(user, token)` - Token reused for Supabase client RLS
4. Uses existing `verify_token_string()` - Same JWT validation for both paths

**Frontend Integration:**
When user logs in, frontend stores token in both:
- Memory/localStorage (for fetch Authorization header)
- Cookie (for browser direct downloads)

```javascript
// On login
document.cookie = `access_token=${token}; path=/; SameSite=Strict; Secure`;
```

**Why this approach:**
- Works with API clients (Postman, fetch, curl with -H)
- Works with browser downloads (clicking CSV link)
- No frontend changes needed for API calls
- Cookie is HttpOnly-safe (read on server, not client JS)

**Tradeoffs:**
- Pro: Supports both browser and API access
- Pro: Single endpoint, no duplicate routes
- Pro: Uses existing `verify_token_string()` function
- Con: Cookie adds slight complexity
- Con: Must ensure cookie is set on login

**Alternatives considered:**
- **Separate endpoint for browser download**: Duplicates logic, harder to maintain
- **Signed URL with expiry**: More secure but complex, overkill for this use case
- **Query parameter token**: Security risk (token in logs, browser history)

---

## CSV Export with StreamingResponse

**Location:** `app/api/routes/export.py` - `export_price_history_csv()`

**Purpose:** Allow users to download price history as a CSV file for external analysis (Excel, Google Sheets, custom scripts)

**How it works:**
1. **Validate ownership**: Fetch product by ID + user_id (RLS enforced)
2. **Fetch competitors**: Get all competitors for the product
3. **Fetch price history**: Query `price_history` table for all competitor IDs
4. **Generate CSV in-memory**:
   - Use Python's `csv.writer` with `StringIO`
   - Headers: Date, Competitor, Price, Currency, Status, Error
   - Rows sorted by date (newest first from DB query)
5. **Return StreamingResponse**:
   - Content-Type: `text/csv`
   - Content-Disposition: `attachment; filename="{product_name}_price_history_{YYYYMMDD}.csv"`

**Filename Sanitization:**
```python
def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from filename."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
```
- Replaces special characters with underscore
- Prevents path traversal attacks (`../`)
- Ensures valid filename across OS (Windows, Mac, Linux)

**Why StreamingResponse:**
- Memory efficient for large datasets
- Browser starts download immediately
- Works with any file size (no 5MB limit like some approaches)

**Tradeoffs:**
- Pro: Works with any charting tool or spreadsheet
- Pro: No external dependencies (uses stdlib csv module)
- Pro: Memory efficient (streaming)
- Pro: Proper filename with date suffix for versioning
- Con: No Excel formatting (just raw CSV)
- Con: No pagination (downloads all history)

**Alternatives considered:**
- **pandas.to_csv()**: Heavier dependency, more features than needed
- **openpyxl for Excel**: Format-specific, adds complexity
- **Background job + file storage**: Overkill for simple export

---

## FastAPI OpenAPI Documentation

**Location:** `main.py` - FastAPI app configuration

**Purpose:** Auto-generate interactive API documentation (Swagger UI, ReDoc) from code

**How it works:**
1. FastAPI extracts metadata from:
   - Route decorators (`@router.get`, `@router.post`)
   - Pydantic models (request/response schemas)
   - Docstrings (endpoint descriptions)
   - Response models and status codes
2. Generates OpenAPI 3.0 JSON schema at `/api/openapi.json`
3. Serves Swagger UI at `/api/docs`
4. Serves ReDoc at `/api/redoc`

**Configuration:**
```python
app = FastAPI(
    title="PriceHawk API",
    description="...",  # Markdown supported
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={"name": "...", "email": "..."},
    license_info={"name": "MIT"},
)
```

**Why this approach:**
- Zero maintenance (docs always match code)
- Interactive "Try it out" in Swagger UI
- No manual OpenAPI YAML writing
- Industry standard for REST APIs

**Tradeoffs:**
- Pro: Always up-to-date with code
- Pro: Interactive testing built-in
- Pro: Client SDK generation possible
- Con: Less control over formatting than manual docs
- Con: Requires good docstrings for quality docs

---

## Docker Multi-Stage Build

**Location:** `Dockerfile`

**Purpose:** Create optimized production container image with minimal size and security

**How it works:**

### Stage 1: Builder
```dockerfile
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
```
- Uses `uv` for fast dependency installation
- Only installs production dependencies (`--no-dev`)
- Creates `.venv` with all packages

### Stage 2: Production
```dockerfile
FROM python:3.13-slim AS production
COPY --from=builder /app/.venv /app/.venv
COPY --chown=pricehawk:pricehawk . .
USER pricehawk
```
- Fresh slim image (no build tools)
- Copies only the venv from builder
- Non-root user for security
- No uv, pip, or dev dependencies in final image

**Why multi-stage:**
- Smaller final image (no build tools, cache, dev deps)
- Faster deployment (smaller download)
- Reduced attack surface (fewer packages)
- Clear separation of build vs runtime

**Security features:**
- Non-root user (`pricehawk:1000`)
- Health check for orchestrator integration
- No shell access needed for normal operation

**Tradeoffs:**
- Pro: ~200MB smaller than single-stage build
- Pro: No pip/uv in production image
- Pro: Non-root by default
- Con: More complex Dockerfile
- Con: Longer build time (two stages)

---

## Docker Compose Service Orchestration

**Location:** `docker-compose.yml`

**Purpose:** Define and run multi-container application (API, Redis, Celery) with single command

**Services defined:**
1. **api**: FastAPI application
2. **redis**: Message broker for Celery
3. **celery-worker**: Background task processor
4. **celery-beat**: Scheduled task scheduler

**Key patterns:**

### Health Check Dependencies
```yaml
api:
  depends_on:
    redis:
      condition: service_healthy
```
- API waits for Redis to be healthy before starting
- Prevents connection errors on startup

### Environment Variable Injection
```yaml
api:
  env_file:
    - .env
  environment:
    - REDIS_URL=redis://redis:6379/0
```
- `.env` file loaded for secrets
- Docker-specific overrides (e.g., Redis hostname)

### Shared Build Context
```yaml
celery-worker:
  build:
    context: .
    dockerfile: Dockerfile
  command: celery -A app.tasks.celery_app worker
```
- Same image as API, different command
- Reduces build time and storage

**Why docker-compose:**
- Single command: `docker compose up -d`
- Network isolation (services communicate via container names)
- Volume persistence for Redis data
- Easy local development matching production

**Tradeoffs:**
- Pro: One-command deployment
- Pro: Isolated network between services
- Pro: Easy scaling (`docker compose up --scale celery-worker=3`)
- Con: Not for Kubernetes (use Helm charts instead)
- Con: Single-host only (no multi-machine orchestration)

---

## Test Suite Architecture

**Location:** `app/tests/`

**Purpose:** Verify API behavior without requiring real database or external services

**Structure:**
```
app/tests/
├── __init__.py
├── conftest.py      # Shared fixtures
├── test_health.py   # Health check tests
├── test_export.py   # CSV export tests
└── test_products.py # Products CRUD tests
```

**Key patterns:**

### Dependency Override for Auth
```python
@pytest.fixture
def mock_auth(mock_user):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield
    app.dependency_overrides.clear()
```
- Replaces real JWT verification with mock user
- Tests run without Supabase connection
- Isolates tests from auth system

### Database Mocking
```python
@pytest.fixture
def mock_db_success(sample_product):
    mock_client = MagicMock()
    # Configure mock responses...
    with patch("app.api.routes.export.get_supabase_client", return_value=mock_client):
        yield mock_client
```
- Patches Supabase client at route level
- Returns predefined data
- No real database calls

**Why this approach:**
- Fast tests (no network calls)
- Deterministic (same results every run)
- CI-friendly (no external services needed)
- Tests business logic, not database

**Tradeoffs:**
- Pro: Fast, isolated, reliable
- Pro: Can test error scenarios easily
- Pro: No test database setup required
- Con: Mocks may drift from real behavior
- Con: Doesn't catch database-specific bugs
- Con: Integration tests still needed for full coverage

**Running tests:**
```bash
# Install dev dependencies
uv sync --extra dev

# Run all tests
pytest

# With coverage
pytest --cov=app --cov-report=html
```

---

## Dashboard API Endpoints (Milestone 8 - Phase 3)

**Location:** `app/api/routes/pages.py:170-309`

**Purpose:** Provide optimized, aggregated endpoints for the dashboard to minimize API calls and improve load time

**Endpoints Created:**

### GET /api/dashboard/stats

**Location:** `app/api/routes/pages.py:170-225`

**Purpose:** Single endpoint returning all dashboard stat counts

**How it works:**
1. Query `products` table with `count="exact"` for products count
2. Query `competitors` table with inner join to `products` for competitors count
3. Query `pending_alerts` with `detected_at >= 7 days ago` for weekly alerts count
4. Query `insights` table with inner join to `products` for insights count
5. Return JSON with all four counts

**Response:**
```json
{
  "products": 5,
  "competitors": 12,
  "alerts": 3,
  "insights": 2
}
```

**Why single endpoint:**
- Dashboard previously made 3+ separate API calls
- Single call reduces latency (1 round-trip vs 3+)
- Server-side aggregation is faster than client-side

### GET /api/dashboard/activity

**Location:** `app/api/routes/pages.py:228-267`

**Purpose:** Fetch recent price change activity for the dashboard activity feed

**How it works:**
1. Query `pending_alerts` table with joins to `products` and `competitors`
2. Order by `detected_at DESC` (most recent first)
3. Limit to 10 items
4. Transform data for frontend display:
   - Extract product_id, product_name, retailer
   - Convert prices to float for JSON serialization
   - Include change_percent and detected_at

**Response:**
```json
{
  "activity": [
    {
      "id": "uuid",
      "type": "price_drop",
      "product_id": "uuid",
      "product_name": "iPhone 15",
      "retailer": "Amazon",
      "old_price": 999.00,
      "new_price": 949.00,
      "change_percent": -5.01,
      "detected_at": "2025-01-09T10:00:00Z"
    }
  ]
}
```

### GET /api/dashboard/products

**Location:** `app/api/routes/pages.py:270-309`

**Purpose:** Fetch recent products with competitor counts for dashboard display

**How it works:**
1. Query `products` table ordered by `created_at DESC`
2. Limit to 5 products
3. For each product, query `competitors` table with `count="exact"`
4. Return products with embedded competitor_count

**Response:**
```json
{
  "products": [
    {
      "id": "uuid",
      "product_name": "iPhone 15 Pro",
      "is_active": true,
      "competitor_count": 3
    }
  ]
}
```

**Tradeoffs:**
- Pro: Optimized for dashboard use case (single call per section)
- Pro: Returns only fields needed for display (not full models)
- Pro: Parallel loading on frontend (3 independent endpoints)
- Con: N+1 query in /dashboard/products (1 query per product for competitor count)
- Con: Duplicates some logic from /api/products endpoint

**Frontend Integration:**

The dashboard template (`app/templates/dashboard/index.html`) loads data using:
```javascript
await Promise.all([
    loadStats(),        // GET /api/dashboard/stats
    loadRecentProducts(), // GET /api/dashboard/products
    loadRecentActivity()  // GET /api/dashboard/activity
]);
```

**UI Enhancements:**
- Stats cards now have loading spinners
- Stats cards are clickable (links to respective pages)
- Empty states with icons and action buttons
- Activity feed shows price change icons (up/down arrows)
- Time ago formatting (e.g., "5m ago", "2h ago")

**Alternatives considered:**
- **Single mega-endpoint**: Returns everything, but slower if user only needs part
- **GraphQL**: More flexible, but adds complexity for simple dashboard
- **Server-side rendering**: Pass data in template context, but slower page load

---

## Windows Playwright Fallback (OS-Aware Async/Sync)

**Location:** `app/services/scraper_service.py:337-417`

**Purpose:** Handle Windows asyncio subprocess limitation by automatically switching between async and sync Playwright based on OS

**The Problem:**
- Windows `ProactorEventLoop` doesn't support `asyncio.create_subprocess_exec()`
- Playwright async API spawns browser subprocess via asyncio
- Results in `NotImplementedError` on Windows when using `async_playwright()`

**How it works:**

### OS Detection
```python
if sys.platform == "win32":
    # Use sync Playwright in thread pool
else:
    # Use async Playwright directly (Linux/macOS)
```

### Windows Path (Sync + ThreadPool)
1. Lazy-initialize `ThreadPoolExecutor` with 3 workers max
2. Run sync Playwright function in executor via `loop.run_in_executor()`
3. Sync function uses `sync_playwright()` context manager
4. Returns HTML content back to async caller

```python
async def fetch_with_playwright(url, proxy):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _get_playwright_executor(),
        _playwright_sync,
        url, proxy, user_agent
    )
```

### Linux/macOS Path (Async Native)
1. Use `async_playwright()` directly
2. All operations use `await`
3. No thread pool overhead

```python
async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    # ... async operations
```

**Why this approach:**
- Zero code changes needed for Linux deployment (Cloud Run)
- Windows development works without errors
- No performance penalty on production (Linux uses async)
- Fallback is automatic (no config needed)

**Tradeoffs:**
- Pro: Works on all platforms (Windows, macOS, Linux)
- Pro: No configuration required (auto-detects OS)
- Pro: Production (Linux) uses optimal async path
- Pro: Development on Windows doesn't crash
- Con: Windows path uses threads (limited to 3 concurrent browsers)
- Con: More code (~80 lines vs 30 lines)
- Con: Two code paths to maintain

**Scaling considerations:**
- Windows (dev): Limited to 3 concurrent Playwright instances (thread pool size)
- Linux (prod): Full async concurrency, limited only by memory
- For 10K products: Deploy on Linux, use Celery workers, not thread pool

**Key functions:**
- `_get_playwright_executor()`: Lazy thread pool initialization
- `_parse_proxy_config()`: Shared proxy parsing (used by both paths)
- `_playwright_sync()`: Sync implementation for Windows
- `_playwright_async()`: Async implementation for Linux/macOS
- `fetch_with_playwright()`: Main entry point with OS detection

---

## Price Reuse Optimization (Discovery → Track)

**Location:** `app/api/routes/discovery.py:64-131`

**Purpose:** Eliminate redundant scraping by reusing prices already fetched during discovery phase

### The Problem (Before)

```
Discovery: Scrape store → Get 1000 products with prices (in memory)
Track: User selects 50 → Scrape AGAIN → Store prices
```

**Impact:**
- Every URL scraped twice (discovery + track)
- 1000 products = ~5 minutes of redundant scraping
- Background jobs, polling, progress tracking complexity

### The Solution (After)

```
Discovery: Scrape store → Get 1000 products with prices (in memory)
Track: User selects 50 → Store discovered prices directly → Done
```

**Key insight:** Discovery already has the prices. Don't throw them away.

### Implementation

#### Request Model (TrackProductItem)
```python
class TrackProductItem(BaseModel):
    url: str
    price: Decimal | None = None  # Price from discovery
    currency: str = "USD"
```

Frontend sends discovered prices with the track request.

#### Track Endpoint (discovery.py)
```python
@router.post("/track")
async def track_products(body: TrackProductsRequest, ...):
    # 1. Create product group
    group_result = client.table("products").insert(group_data).execute()

    # 2. Insert competitors
    competitors_result = client.table("competitors").insert(competitors_data).execute()

    # 3. Store discovered prices directly (NO SCRAPING)
    prices_stored = 0
    for product, competitor in zip(body.products, competitors_result.data):
        if product.price is not None:
            price_data = {
                "competitor_id": competitor["id"],
                "price": float(product.price),
                "currency": product.currency,
                "scrape_status": "success",
            }
            client.table("price_history").insert(price_data).execute()
            prices_stored += 1

    return TrackProductsResponse(
        group_id=group_id,
        products_added=len(competitors),
        prices_stored=prices_stored,  # New field
    )
```

### Performance Comparison

| Products | Background Job Approach | Price Reuse | Speedup |
|----------|------------------------|-------------|---------|
| 50       | ~8 seconds             | instant     | ~80x    |
| 250      | ~1 minute              | instant     | ~600x   |
| 1000     | ~5 minutes             | instant     | ~3000x  |

"Instant" = ~200ms database insert

### Data Flow

```
                    ┌─────────────────────────────────────────┐
                    │         Discovery (in memory)           │
                    │                                         │
                    │  Product A: $10  ───┐                   │
                    │  Product B: $20  ───┼── User tracks     │
                    │  Product C: $15  ───┘                   │
                    │  Product D: $25  ─── Not tracked        │
                    │                      (discarded)        │
                    └─────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │           Track (database)              │
                    │                                         │
                    │  competitors: A, B, C (3 rows)          │
                    │  price_history: $10, $20, $15 (3 rows)  │
                    │                                         │
                    │  Product D never touches database       │
                    └─────────────────────────────────────────┘
```

### What Was Removed

- `app/api/routes/jobs.py` - Job polling endpoint (deleted)
- `app/services/background_tasks.py` - Background scraping (deleted)
- `TrackingJobResponse` model - No longer needed (deleted)
- `job_id` from `TrackProductsResponse` - No background job

### What Stays the Same

- Daily Celery Beat scrape at 2 AM (monitors tracked products)
- Alert detection comparing prices in price_history
- Email digest system
- All price_history queries for charts/insights

### Why This Approach

**Pros:**
- ~3000x faster (instant vs 5 minutes)
- Simpler architecture (no background jobs, polling, progress tracking)
- Zero database bloat (untracked products never stored)
- Scrapes reduced from 2x to 1x per product

**Cons:**
- Price is slightly stale (seconds old from discovery)
- Frontend must pass price data in track request

### Tradeoffs

**Chosen:** Reuse discovered prices

**Alternative 1: Background jobs with batched scraping**
- Pro: Always fresh prices at track time
- Con: Redundant (discovery already has prices)
- Con: Complex (jobs, polling, progress UI)
- Con: Slow (~5 min for 1000 products)

**Alternative 2: Store all discovered prices, filter later**
- Pro: Simple frontend (just send URLs)
- Con: Database bloat (1000 rows even if user tracks 50)
- Con: Wasted storage for products never tracked

### The Lesson

Before optimizing HOW you do something, ask if you should be doing it at all.

The fastest code is the code that doesn't run.

---

## Non-Blocking Manual Scrape with SSE

**Location:**
- `app/api/routes/scraper.py:35-124` - Endpoints
- `app/tasks/scraper_tasks.py:65-178` - Celery task
- `app/templates/products/detail.html:548-654` - Frontend

**Purpose:** Prevent server blocking during manual scrape operations by offloading work to Celery and streaming progress via Server-Sent Events (SSE)

### The Problem (Before)

```
User clicks "Get Current Prices"
    ↓
POST /scrape/manual/{product_id}
    ↓
Server blocks for 5-10+ minutes (scraping each competitor sequentially)
    ↓
Other requests timeout, server appears frozen
    ↓
User navigates away, loses progress
```

**Impact:**
- Server blocked for entire scrape duration
- Other users' requests timeout
- No progress visibility
- Navigation cancels scrape

### The Solution (After)

```
User clicks "Get Current Prices"
    ↓
POST /scrape/manual/{product_id}
    ↓
Server queues Celery task, returns task_id in ~100ms
    ↓
Frontend opens SSE connection to /scrape/stream/{task_id}
    ↓
Celery worker scrapes competitors, updates Redis after each
    ↓
SSE endpoint reads Redis, streams events to frontend:
  → {"status": "scraping", "completed": 1, "total": 5, "current": "amazon.com"}
  → {"status": "scraping", "completed": 2, "total": 5, "current": "walmart.com"}
  → {"status": "completed", "results": [...]}
    ↓
Frontend updates UI in real-time
```

### Implementation Details

#### 1. Redis Progress Tracking

**Location:** `app/tasks/scraper_tasks.py:31-41`

```python
def set_scrape_progress(task_id: str, data: dict, ttl: int = 300):
    """Store scrape progress in Redis with TTL (default 5 min)."""
    client = _get_redis_client()
    client.setex(f"scrape:{task_id}", ttl, json.dumps(data))

def get_scrape_progress(task_id: str) -> dict | None:
    """Get scrape progress from Redis."""
    client = _get_redis_client()
    data = client.get(f"scrape:{task_id}")
    return json.loads(data) if data else None
```

**Key Design Decisions:**
- Redis key format: `scrape:{task_id}`
- TTL: 5 minutes (auto-cleanup after scrape completes)
- Lazy Redis client initialization (doesn't block imports)

#### 2. Celery Task with Progress Updates

**Location:** `app/tasks/scraper_tasks.py:65-178`

```python
@celery_app.task(bind=True)
def scrape_product_manual(self, product_id: str) -> dict:
    task_id = self.request.id  # Celery provides unique task ID

    # Initialize progress
    set_scrape_progress(task_id, {
        "status": "scraping",
        "completed": 0,
        "total": total,
        "results": []
    })

    for i, competitor in enumerate(competitors):
        # Update progress before scraping
        set_scrape_progress(task_id, {
            "status": "scraping",
            "completed": i,
            "total": total,
            "current": retailer,
            "results": results
        })

        # Scrape and store result
        scrape_result = asyncio.run(scrape_url(url))
        results.append(result)

        # Update progress after scraping
        set_scrape_progress(task_id, {
            "status": "scraping",
            "completed": i + 1,
            "total": total,
            "results": results
        })

    # Final status
    set_scrape_progress(task_id, {
        "status": "completed",
        "completed": total,
        "total": total,
        "results": results
    })
```

#### 3. SSE Streaming Endpoint

**Location:** `app/api/routes/scraper.py:78-124`

```python
@router.get("/scrape/stream/{task_id}")
async def stream_scrape_progress(task_id: str):
    async def event_generator():
        timeout_seconds = 300
        poll_interval = 1.0
        elapsed = 0

        while elapsed < timeout_seconds:
            progress = get_scrape_progress(task_id)

            if progress is None:
                yield f"data: {json.dumps({'status': 'queued'})}\n\n"
            else:
                yield f"data: {json.dumps(progress)}\n\n"
                if progress.get("status") in ("completed", "error"):
                    break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
```

**SSE Format:**
```
data: {"status": "scraping", "completed": 2, "total": 5, "current": "amazon.com"}\n\n
```

#### 4. Frontend EventSource

**Location:** `app/templates/products/detail.html:548-654`

```javascript
// Step 1: Queue the task
const response = await fetch(`/api/scraper/scrape/manual/${productId}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${accessToken}` }
});
const { task_id } = await response.json();

// Step 2: Connect to SSE stream
scrapeEventSource = new EventSource(`/api/scraper/scrape/stream/${task_id}`);

scrapeEventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.status === 'scraping') {
        btnText.textContent = `Scraping ${data.completed}/${data.total}...`;
        // Update price cells as results arrive
    } else if (data.status === 'completed') {
        scrapeEventSource.close();
        // Reset UI
    }
};

scrapeEventSource.onerror = () => {
    scrapeEventSource.close();
    // Handle error gracefully
};
```

### Why SSE Over Alternatives

| Aspect | SSE | WebSocket | Polling |
|--------|-----|-----------|---------|
| **Direction** | Server → Client | Bidirectional | Client → Server |
| **Our need** | Perfect (one-way) | Overkill | Wasteful |
| **Complexity** | Simple HTTP | Protocol upgrade | Many requests |
| **Auto-reconnect** | Built-in | Manual | N/A |
| **Proxy support** | Good | Poor | Good |
| **Browser support** | All modern | All modern | All |

**SSE wins because:**
1. We only need server → client updates
2. Built-in auto-reconnect on connection loss
3. Just HTTP - no WebSocket protocol upgrade
4. Works through most proxies
5. Native browser `EventSource` API

### Why Redis for Progress

1. **Already have it** - Celery uses Redis as broker
2. **Fast** - In-memory key-value store
3. **TTL support** - Auto-cleanup after 5 minutes
4. **Atomic operations** - No race conditions
5. **Shared state** - SSE endpoint reads what Celery writes

### Tradeoffs

**Pros:**
- Server responds in ~100ms (non-blocking)
- Real-time progress updates (user sees 2/5, 3/5...)
- User can navigate away (scraping continues in background)
- Scales horizontally (multiple Celery workers)
- Other requests not blocked

**Cons:**
- More complexity (Celery task + Redis + SSE)
- Requires Celery worker running
- Slight latency on first result (~2-5s for scraper startup)
- SSE connection per active scrape

### Performance Comparison

| Competitors | Blocking (Before) | SSE (After) |
|-------------|-------------------|-------------|
| 1 | ~10-30s blocking | ~100ms + background |
| 5 | ~1-3 min blocking | ~100ms + background |
| 10 | ~3-5 min blocking | ~100ms + background |
| 50 | ~15-30 min blocking | ~100ms + background |

**Key insight:** Response time is now constant (~100ms) regardless of competitor count.

### Alternatives Considered

1. **asyncio.gather() in endpoint:**
   - Pro: Faster than sequential (parallel scraping)
   - Con: Still blocks the request
   - Con: High memory for many competitors

2. **Polling endpoint:**
   - Pro: Simpler than SSE
   - Con: Higher server load (N requests vs 1 stream)
   - Con: Delayed updates (poll interval)

3. **WebSocket:**
   - Pro: Bidirectional communication
   - Con: Overkill (we don't need client → server)
   - Con: More complex (protocol upgrade, connection management)

4. **Background task + email notification:**
   - Pro: No real-time connection needed
   - Con: Poor UX (user waits for email)
   - Con: No progress visibility

---

## Currency Mismatch Detection & Resolution

**Location:**
- `app/services/alert_service.py:119-144` - Detection logic
- `app/api/routes/alerts.py:328-450` - Accept endpoints

**Purpose:** Prevent false price alerts when a store changes its currency display (e.g., USD → EUR) by detecting mismatches and allowing users to accept new currencies

### The Problem

```
Day 1: Scrape Amazon UK → $100 USD stored
Day 2: User switches region → £80 GBP scraped
System: "Price dropped 20%!" → FALSE (actually increased ~13% after conversion)
```

**Impact:**
- False price drop/increase alerts
- User loses trust in system
- No way to fix without re-adding competitor

### The Solution: Currency Guard + User Notification

#### 1. Detection (alert_service.py)

**Location:** `app/services/alert_service.py:119-144`

```python
# Get previous price AND currency
old_price = Decimal(str(prev_response.data[1]["price"]))
old_currency = prev_response.data[1].get("currency", "USD")

# Currency mismatch check
if old_currency != currency:
    # Create currency_changed alert instead of price alert
    alert_data = {
        "alert_type": "currency_changed",
        "old_currency": old_currency,
        "new_currency": currency,
        ...
    }
    sb.table("pending_alerts").insert(alert_data).execute()

    return {
        "alert_created": True,
        "alert_type": "currency_changed",
        "message": f"Currency changed: {old_currency} → {currency}"
    }
```

**Behavior:**
- Compares new currency with previous price_history record
- If mismatch: Creates `currency_changed` alert instead of `price_drop`/`price_increase`
- Blocks false price change calculation
- Logs warning for monitoring

#### 2. Accept Single Currency (API)

**Location:** `app/api/routes/alerts.py:328-387`

**Endpoint:** `PATCH /api/alerts/competitors/{competitor_id}/accept-currency`

**Request:**
```json
{
  "currency": "GBP"
}
```

**Flow:**
1. Verify user owns competitor (via product ownership check)
2. Update `competitors.expected_currency = "GBP"`
3. Dismiss pending `currency_changed` alerts for this competitor
4. Next scrape uses GBP as baseline

**Response:**
```json
{
  "success": true,
  "message": "Now tracking prices in GBP",
  "competitor_id": "uuid",
  "new_currency": "GBP"
}
```

#### 3. Accept All Currencies (Bulk)

**Location:** `app/api/routes/alerts.py:390-450`

**Endpoint:** `POST /api/alerts/accept-all-currencies`

**Purpose:** One-click to accept all pending currency changes

**Flow:**
1. Fetch all `currency_changed` alerts for user where `included_in_digest = false`
2. For each alert:
   - Update competitor's `expected_currency` to `new_currency`
   - Mark alert as `included_in_digest = true`
3. Return count of updated competitors

**Response:**
```json
{
  "success": true,
  "message": "Accepted 3 currency changes",
  "updated_count": 3
}
```

### Database Changes

**competitors table:**
```sql
ALTER TABLE competitors ADD COLUMN expected_currency VARCHAR(3) DEFAULT 'USD';
```

**pending_alerts table:**
```sql
-- New columns
old_currency VARCHAR(3),
new_currency VARCHAR(3)

-- Updated constraint
CHECK (alert_type IN ('price_drop', 'price_increase', 'currency_changed'))
```

### User Flow

```
1. Scraper runs → detects EUR instead of USD
           ↓
2. Currency mismatch → creates "currency_changed" alert
           ↓
3. User sees alert: "Store changed from USD to EUR"
           ↓
4a. User clicks [Accept EUR] for single competitor
    OR
4b. User clicks [Accept All] for bulk update
           ↓
5. API updates competitor.expected_currency
           ↓
6. Next scrape uses EUR as baseline → normal tracking resumes
```

### Tradeoffs

**Chosen Approach:** Currency guard with user notification + accept action

**Pros:**
- No false price alerts
- User stays informed of currency changes
- Simple fix (one click)
- No external API dependency (no currency conversion)
- Bulk action for multiple currency changes

**Cons:**
- Requires user action to resolve
- Doesn't auto-convert currencies
- Creates additional alert type to handle

**Alternatives Considered:**

1. **Silent skip (just log warning):**
   - Pro: Simpler, no UI needed
   - Con: User doesn't know why no alerts
   - Con: Currency change never resolved

2. **Auto-accept new currency:**
   - Pro: No user action needed
   - Con: User may not notice the change
   - Con: Could accept wrong currency (geo-redirect)

3. **Currency conversion API:**
   - Pro: Compare prices accurately across currencies
   - Con: External API dependency (cost, latency)
   - Con: Exchange rates fluctuate (introduces noise)
   - Con: More complex implementation

4. **Hardcoded exchange rates:**
   - Pro: No API needed
   - Con: Rates go stale quickly
   - Con: Maintenance burden

---

## Multi-Page Password Reset Flow (Security Enhancement)

**Location:**
- `app/api/routes/auth.py:123-244` - API endpoints
- `app/api/routes/pages.py:105-124` - Page routes
- `app/templates/auth/forgot_password.html` - Step 1: Email input
- `app/templates/auth/verify_reset_code.html` - Step 2: OTP verification
- `app/templates/auth/reset_password.html` - Step 3: New password

**Purpose:** Improve security by separating OTP verification from password reset, preventing password form exposure until identity is confirmed

### The Problem (Before)

```
Single page shows: Email → OTP → New Password fields
                   ↓
User can see password fields before OTP verified
                   ↓
Security risk: Password form exposed prematurely
```

**Issues:**
- Password fields visible before identity verification
- Single-page approach allows form manipulation
- No clear separation of authentication steps

### The Solution: 3-Step Flow with Token Handoff

```
Step 1: /forgot-password
    User enters email → OTP sent → Redirect to step 2
                   ↓
Step 2: /verify-reset-code?email=xxx
    User enters 6-digit OTP → Backend verifies → Returns reset_token
                   ↓
Step 3: /reset-password?token=xxx
    User enters new password → Backend validates token → Password updated
```

### Implementation Details

#### API Endpoints

**POST /api/auth/verify-reset-otp** (Step 2)
```python
class VerifyResetOTPRequest(BaseModel):
    email: EmailStr
    otp: str  # No password field anymore

@router.post("/verify-reset-otp")
async def verify_reset_otp(reset_data: VerifyResetOTPRequest):
    response = client.auth.verify_otp({
        "email": reset_data.email,
        "token": reset_data.otp,
        "type": "recovery"
    })

    # Return session token as reset_token for next step
    return {
        "message": "Code verified successfully",
        "reset_token": response.session.access_token
    }
```

**POST /api/auth/reset-password** (Step 3)
```python
class ResetPasswordRequest(BaseModel):
    reset_token: str  # Token from OTP verification
    new_password: str

@router.post("/reset-password")
async def reset_password(reset_data: ResetPasswordRequest):
    # Use token to establish session and update password
    client = get_supabase_client_with_session(reset_data.reset_token)
    client.auth.update_user({"password": reset_data.new_password})

    return {"message": "Password has been reset successfully."}
```

#### Token Security

**How the token works:**
1. OTP verification returns Supabase session `access_token`
2. Token passed to Step 3 via URL parameter
3. `get_supabase_client_with_session()` establishes auth session with token
4. `update_user()` only works with valid session

**Token properties:**
- Valid for ~1 hour (Supabase default session timeout)
- Single-use conceptually (user completes reset)
- Tied to user's email via Supabase auth
- Cannot be reused after password change (session invalidated)

#### Rate Limiting with Countdown Timer

**Location:** `app/templates/auth/forgot_password.html:98-131`

**How it works:**
1. Supabase enforces 60-second cooldown between reset requests
2. Backend catches rate limit error, extracts remaining seconds
3. Returns 429 status with `X-Retry-After` header
4. Frontend stores countdown end time in localStorage
5. Button shows "Wait Xs" countdown
6. Persists across page refresh

```javascript
function startCountdown(seconds) {
    const endTime = Date.now() + (seconds * 1000);
    localStorage.setItem('resetCountdownEnd', endTime);
    submitBtn.disabled = true;
    countdownInterval = setInterval(updateCountdownDisplay, 1000);
}
```

**Why localStorage:**
- Persists across page refresh
- Prevents circumventing by reloading page
- Synced with backend rate limit window

### Security Benefits

1. **Password form isolation:** Password fields only shown after OTP verified
2. **Token-based authorization:** Step 3 requires valid token from Step 2
3. **Defense in depth:** Each step validates independently
4. **No form manipulation:** Can't skip OTP by modifying HTML
5. **Rate limiting:** 60-second cooldown prevents brute force

### Tradeoffs

**Chosen Approach:** Multi-page flow with token handoff

**Pros:**
- Better security (password form hidden until identity verified)
- Clear user journey (3 distinct steps)
- Token can have short expiry
- Each step is independently validated
- Follows OWASP best practices for password reset

**Cons:**
- More pages (3 vs 1)
- Slightly longer user journey
- Token in URL (mitigated by HTTPS + short expiry)
- Need localStorage for email state between pages

**Alternatives Considered:**

1. **Single page with JS-controlled reveal:**
   - Pro: Fewer page loads
   - Con: Password form in DOM (can be revealed via DevTools)
   - Con: Client-side security (can be bypassed)

2. **Session cookie instead of URL token:**
   - Pro: Token not visible in URL
   - Con: More complex session management
   - Con: Cookie handling across pages

3. **Email link with token:**
   - Pro: No OTP entry needed
   - Con: Email can be forwarded (security risk)
   - Con: Users expect OTP for password reset

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Step 1: /forgot-password                  │
│                                                              │
│   User: enters email@example.com                             │
│   API:  POST /api/auth/forgot-password                       │
│   Supabase: Sends OTP to email                               │
│   Frontend: Saves email to localStorage, redirects           │
│                                                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│          Step 2: /verify-reset-code?email=xxx                │
│                                                              │
│   User: enters 6-digit OTP (051022)                          │
│   API:  POST /api/auth/verify-reset-otp                      │
│   Supabase: Verifies OTP, returns session token              │
│   Frontend: Receives reset_token, redirects                  │
│                                                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│            Step 3: /reset-password?token=xxx                 │
│                                                              │
│   User: enters new password (only NOW visible)               │
│   API:  POST /api/auth/reset-password                        │
│   Supabase: Validates token, updates password                │
│   Frontend: Shows success, redirects to login                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```
