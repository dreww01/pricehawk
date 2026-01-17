# PriceHawk Architecture Documentation

## Table of Contents
1. [System Overview](#system-overview)
2. [Design Philosophy](#design-philosophy)
3. [File Structure](#file-structure)
4. [User Flow](#user-flow)
5. [Data Flow](#data-flow)
6. [Platform Detection Strategy](#platform-detection-strategy)
7. [Store Handler Architecture](#store-handler-architecture)
8. [Database Schema](#database-schema)
9. [Security Model](#security-model)
10. [Background Task System](#background-task-system)

---

## System Overview

**PriceHawk** is a multi-platform price monitoring system that discovers products from competitor stores, tracks their prices over time, and provides automated alerts on price changes.

### Core Components

```
┌─────────────────┐
│   FastAPI App   │  ← HTTP API endpoints
└────────┬────────┘
         │
         ├──► Store Discovery Engine
         ├──► Product Tracking
         ├──► Price Scraper
         └──► Background Tasks
                    │
                    ├──► Celery Worker
                    ├──► Celery Beat (Scheduler)
                    └──► Redis (Broker)
```

### Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **API Framework** | FastAPI | Async HTTP endpoints, auto-generated docs |
| **Database** | Supabase (PostgreSQL) | Managed database + auth + RLS |
| **Authentication** | Supabase Auth | JWT-based user auth |
| **Task Queue** | Celery + Redis | Background scraping tasks |
| **Web Scraping** | httpx, BeautifulSoup, Playwright | Multi-platform data extraction |
| **Data Validation** | Pydantic | Type-safe request/response models |

---

## Design Philosophy

### 1. Separation of Concerns

**Discovery vs. Tracking vs. Scraping**

- **Discovery**: One-time exploration of stores to find products
- **Tracking**: Ongoing monitoring of selected products
- **Scraping**: Price extraction from tracked URLs

These are separate workflows with different requirements and lifetimes.

### 2. Plugin Architecture

Store handlers follow the **Strategy Pattern**:
- Each platform (Shopify, WooCommerce, etc.) = separate handler class
- All handlers implement `BaseStoreHandler` interface
- New platforms can be added without modifying core logic
- Fallback handler for unknown platforms

### 3. Async-First Design

- All I/O operations are async (`async/await`)
- HTTP requests use `httpx.AsyncClient`
- Concurrent scraping without blocking
- Scales to thousands of products

### 4. Defense in Depth (Security)

- **Application Layer**: Input validation (Pydantic), HTTPS-only URLs
- **Database Layer**: Row Level Security (RLS) policies
- **Transport Layer**: JWT token verification on all protected endpoints
- **Infrastructure Layer**: Environment variables, no hardcoded secrets

---

## File Structure

```
pricehawk/
│
├── app/
│   ├── __init__.py
│   │
│   ├── api/
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── discovery.py       # POST /api/stores/discover, /track
│   │       ├── products.py        # CRUD for tracking groups
│   │       ├── scraper.py         # POST /api/scrape/manual, /worker-health
│   │       ├── insights.py        # GET/POST /api/insights (AI analysis)
│   │       ├── alerts.py          # Alert settings & history
│   │       └── export.py          # CSV export
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings (Pydantic BaseSettings)
│   │   └── security.py            # JWT verification, get_current_user()
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py            # get_supabase_client() factory
│   │   └── models.py              # Pydantic models (request/response)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── store_discovery.py     # discover_products() orchestrator
│   │   ├── store_detector.py      # detect_platform() logic
│   │   ├── scraper_service.py     # Price extraction service
│   │   ├── ai_service.py          # Groq AI integration
│   │   ├── alert_service.py       # Alert detection logic
│   │   ├── email_service.py       # Email sending (SMTP)
│   │   └── stores/                # Handler plugins
│   │       ├── __init__.py
│   │       ├── base.py            # BaseStoreHandler (abstract)
│   │       ├── shopify.py         # ShopifyHandler
│   │       ├── woocommerce.py     # WooCommerceHandler
│   │       └── generic.py         # GenericHandler (fallback)
│   │
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── celery_app.py          # Celery config + Beat schedule
│   │   └── scraper_tasks.py       # Background tasks (scrape, alerts, cleanup)
│   │
│   └── tests/                     # Test suite
│       ├── __init__.py
│       ├── conftest.py            # Pytest fixtures
│       ├── test_health.py
│       ├── test_products.py
│       └── test_export.py
│
├── main.py                        # FastAPI app creation + route registration
├── run.py                         # Development server (uvicorn)
├── pyproject.toml                 # UV dependencies
├── .env.example                   # Environment template
├── database_schema.sql            # Database setup SQL
├── Dockerfile                     # Production container
├── docker-compose.yml             # Service orchestration
├── architecture.md                # This file
├── logic_used.md                  # Complex algorithm explanations
├── prd.md                         # Product requirements
└── README.md                      # Setup instructions
```

### Key Files Explained

**`app/services/store_discovery.py`**
- Main entry point for discovery
- Calls `detect_platform()` to get handler
- Calls `handler.fetch_products()`
- Returns `DiscoveryResult`

**`app/services/store_detector.py`**
- Tries each handler's `detect()` method in priority order
- Returns first matching handler
- Falls back to `GenericHandler` if none match

**`app/services/stores/base.py`**
- Defines `BaseStoreHandler` abstract class
- Defines `DiscoveredProduct` dataclass (unified product model)
- Provides `filter_by_keyword()` helper

**`app/services/stores/shopify.py` (example handler)**
- Implements `detect()`: checks for `/products.json` endpoint
- Implements `fetch_products()`: fetches JSON, parses variants, filters by keyword
- Returns list of `DiscoveredProduct` objects

**`app/tasks/scraper_tasks.py`**
- `scrape_single_competitor(competitor_id)`: Scrapes one URL, stores price
- `scrape_all_products()`: Daily task that scrapes all active products
- Uses Celery retry logic + idempotency checks

**`app/core/security.py`**
- `verify_jwt()`: Verifies Supabase JWT token
- `get_current_user()`: FastAPI dependency that extracts user from token
- Returns `CurrentUser` object with `id` and `email`

---

## User Flow

### End-to-End Journey

```
1. DISCOVER PRODUCTS
   User → POST /api/stores/discover
       ├─ Input: Store URL (e.g., "https://example.myshopify.com")
       ├─ Input: Keyword (optional, e.g., "laptop")
       ├─ Input: Limit (default 50)
       │
       └─ Output: List of discovered products with prices

2. REVIEW RESULTS
   User selects products to track from discovery results

3. TRACK PRODUCTS
   User → POST /api/stores/track
       ├─ Input: group_name ("My Laptops")
       ├─ Input: product_urls (array of URLs from step 1)
       ├─ Input: alert_threshold_percent (default 10%)
       │
       └─ Output: group_id, products_added count

4. VIEW TRACKED PRODUCTS
   User → GET /api/products
       └─ Output: List of tracking groups with competitors

5. SCRAPE PRICES (MANUAL)
   User → POST /api/scrape/manual/{product_id}
       └─ Queues Celery tasks to scrape all competitors

6. VIEW PRICE HISTORY
   User → GET /api/prices/{product_id}/history
       └─ Output: Price history for all competitors in group

7. AUTOMATED MONITORING
   Celery Beat → Runs daily at 2 AM UTC
       └─ Scrapes all active products automatically
```

### Discovery Workflow (Detailed)

```
POST /api/stores/discover
    │
    ├─ 1. Validate request (Pydantic)
    │      ├─ URL must be HTTPS
    │      ├─ Limit 1-250
    │      └─ Keyword max 100 chars
    │
    ├─ 2. Call discover_products(url, keyword, limit)
    │      │
    │      ├─ 3. detect_platform(url)
    │      │      │
    │      │      ├─ Try ShopifyHandler.detect()
    │      │      ├─ Try WooCommerceHandler.detect()
    │      │      └─ Fallback: GenericHandler
    │      │
    │      ├─ 4. handler.fetch_products(url, keyword, limit)
    │      │      │
    │      │      ├─ Platform-specific API/scraping
    │      │      ├─ Parse products into DiscoveredProduct objects
    │      │      └─ Filter by keyword if provided
    │      │
    │      └─ 5. Return DiscoveryResult
    │             ├─ platform: "shopify"
    │             ├─ store_url: original URL
    │             ├─ total_found: count
    │             ├─ products: [DiscoveredProduct, ...]
    │             └─ error: null or error message
    │
    └─ 6. Convert to StoreDiscoveryResponse (Pydantic)
       └─ Return JSON to user
```

---

## Data Flow

### Request → Response Lifecycle

```
┌──────────────────────────────────────────────────────────────┐
│ 1. CLIENT REQUEST                                            │
│    POST /api/stores/discover                                 │
│    Headers: Authorization: Bearer <jwt>                      │
│    Body: {"url": "...", "keyword": "...", "limit": 50}       │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 2. FASTAPI MIDDLEWARE                                        │
│    ├─ CORS middleware                                        │
│    ├─ Security middleware (HTTPBearer)                       │
│    └─ Extracts JWT token from header                         │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 3. SECURITY LAYER                                            │
│    get_current_user(credentials)                             │
│    ├─ Verify JWT signature with Supabase                     │
│    ├─ Check token expiration                                 │
│    ├─ Extract user_id and email from token                   │
│    └─ Return CurrentUser object                              │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 4. VALIDATION LAYER                                          │
│    Pydantic: StoreDiscoveryRequest                           │
│    ├─ Validate URL format (HTTPS only)                       │
│    ├─ Validate limit (1-250)                                 │
│    └─ Validate keyword length                                │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 5. ROUTE HANDLER                                             │
│    app/api/routes/discovery.py                               │
│    └─ Calls discover_products(url, keyword, limit)           │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 6. BUSINESS LOGIC                                            │
│    app/services/store_discovery.py                           │
│    ├─ detect_platform(url) → handler                         │
│    ├─ handler.fetch_products()                               │
│    └─ Return DiscoveryResult                                 │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 7. PLATFORM HANDLER                                          │
│    app/services/stores/shopify.py (example)                  │
│    ├─ HTTP request to /products.json                         │
│    ├─ Parse JSON response                                    │
│    ├─ Convert to DiscoveredProduct objects                   │
│    └─ Filter by keyword                                      │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 8. RESPONSE FORMATTING                                       │
│    Convert DiscoveryResult → StoreDiscoveryResponse          │
│    (Pydantic serialization)                                  │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│ 9. CLIENT RESPONSE                                           │
│    HTTP 200 OK                                               │
│    Content-Type: application/json                            │
│    Body: {                                                   │
│      "platform": "shopify",                                  │
│      "store_url": "...",                                     │
│      "total_found": 15,                                      │
│      "products": [...]                                       │
│    }                                                         │
└──────────────────────────────────────────────────────────────┘
```

---

## Platform Detection Strategy

### Detection Flow

Each handler implements a `detect(url: str) -> bool` method that determines if it can process the URL.

**Priority Order** (most specific → least specific):
1. ShopifyHandler
2. WooCommerceHandler
3. GenericHandler (always matches)

### Handler Detection Logic

**ShopifyHandler**
```python
async def detect(self, url: str) -> bool:
    """Check if /products.json endpoint exists."""
    try:
        response = await client.head(f"{url}/products.json")
        return response.status_code == 200
    except:
        return False
```

**WooCommerceHandler**
```python
async def detect(self, url: str) -> bool:
    """Check if WooCommerce API endpoint exists."""
    try:
        response = await client.head(f"{url}/wp-json/wc/store/products")
        return response.status_code in [200, 401]  # 401 = exists but needs auth
    except:
        return False
```

**GenericHandler**
```python
async def detect(self, url: str) -> bool:
    """Always matches (fallback handler)."""
    return True
```

### Why This Approach?

**Tradeoffs:**
- **Pro**: Extensible - add new platforms without modifying core code
- **Pro**: Graceful degradation - unknown stores still work via generic handler
- **Pro**: Explicit priority - more reliable handlers tried first
- **Con**: Sequential detection may be slow (mitigated with HTTP HEAD requests)

**Industry Alternatives:**
- **URL pattern matching only**: Faster but less reliable (sites can change domains)
- **Manual platform selection**: User specifies platform (more accurate but worse UX)
- **ML-based detection**: Overkill for this use case, adds complexity

---

## Store Handler Architecture

### BaseStoreHandler Interface

```python
class BaseStoreHandler(ABC):
    """Abstract base class for all store handlers."""

    platform_name: str = "unknown"  # Override in subclass

    @abstractmethod
    async def detect(self, url: str) -> bool:
        """Return True if this handler can process the URL."""
        pass

    @abstractmethod
    async def fetch_products(
        self,
        url: str,
        keyword: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredProduct]:
        """Fetch products from store, return unified format."""
        pass

    async def close(self) -> None:
        """Clean up resources (HTTP clients, browser instances)."""
        pass
```

### Unified Product Model

All handlers return products in the same format:

```python
@dataclass
class DiscoveredProduct:
    # Required fields
    name: str
    price: Decimal | None
    currency: str
    image_url: str | None
    product_url: str
    platform: str

    # Optional platform-specific fields
    variant_id: str | None = None  # Shopify variant ID, platform-specific ID, etc.
    sku: str | None = None
    in_stock: bool = True
    product_type: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    raw_data: dict = field(default_factory=dict)  # Platform-specific extras
```

### Handler Implementation Pattern

**Example: ShopifyHandler**

```python
class ShopifyHandler(BaseStoreHandler):
    platform_name = "shopify"

    async def detect(self, url: str) -> bool:
        # Check for /products.json endpoint
        ...

    async def fetch_products(
        self, url: str, keyword: str | None, limit: int
    ) -> list[DiscoveredProduct]:
        # 1. Fetch from Shopify JSON API
        products_url = f"{url}/products.json?limit={limit}"
        response = await client.get(products_url)
        data = response.json()

        # 2. Parse each product
        discovered = []
        for product in data["products"]:
            for variant in product["variants"]:
                discovered.append(DiscoveredProduct(
                    name=f"{product['title']} - {variant['title']}",
                    price=Decimal(variant["price"]),
                    currency="USD",  # or parse from shop config
                    image_url=product["images"][0]["src"] if product["images"] else None,
                    product_url=f"{url}/products/{product['handle']}",
                    platform="shopify",
                    variant_id=str(variant["id"]),
                    sku=variant.get("sku"),
                    in_stock=variant["available"],
                    product_type=product.get("product_type"),
                    tags=product.get("tags", "").split(","),
                ))

        # 3. Filter by keyword
        return self.filter_by_keyword(discovered, keyword)
```

---

## Database Schema

### Tables

**`products`** (Tracking groups)
```sql
id                UUID PRIMARY KEY
user_id           UUID REFERENCES auth.users(id)
product_name      VARCHAR(255)  -- Group name, e.g., "My Laptops"
is_active         BOOLEAN DEFAULT true
created_at        TIMESTAMP DEFAULT NOW()
updated_at        TIMESTAMP DEFAULT NOW()
```

**`competitors`** (URLs to monitor)
```sql
id                         UUID PRIMARY KEY
product_id                 UUID REFERENCES products(id) ON DELETE CASCADE
url                        TEXT
retailer_name              VARCHAR(100)  -- Optional, extracted from URL
alert_threshold_percent    DECIMAL(5,2) DEFAULT 10.00
created_at                 TIMESTAMP DEFAULT NOW()
```

**`price_history`** (Price snapshots)
```sql
id              UUID PRIMARY KEY
competitor_id   UUID REFERENCES competitors(id) ON DELETE CASCADE
price           DECIMAL(10,2)  -- NULL if scrape failed
currency        VARCHAR(3) DEFAULT 'USD'
scraped_at      TIMESTAMP DEFAULT NOW()
scrape_status   VARCHAR(20)  -- 'success' or 'failed'
error_message   TEXT  -- NULL on success, error details on failure
```

### Relationships

```
users (Supabase auth.users)
   │
   └─── 1:N ───> products
                    │
                    └─── 1:N ───> competitors
                                     │
                                     └─── 1:N ───> price_history
```

### Indexes

```sql
CREATE INDEX idx_products_user_id ON products(user_id);
CREATE INDEX idx_products_is_active ON products(is_active);
CREATE INDEX idx_competitors_product_id ON competitors(product_id);
CREATE INDEX idx_price_history_competitor_id ON price_history(competitor_id);
CREATE INDEX idx_price_history_scraped_at ON price_history(scraped_at DESC);
```

**Why these indexes?**
- `idx_products_user_id`: Fast lookup of user's products (most common query)
- `idx_products_is_active`: Filter active products for daily scraping
- `idx_competitors_product_id`: Join products → competitors
- `idx_price_history_competitor_id`: Join competitors → price history
- `idx_price_history_scraped_at`: Sort by date for charts (DESC = newest first)

---

## Security Model

### Row Level Security (RLS)

**Principle**: Database enforces user isolation, not application logic.

**Products Table Policies**
```sql
-- Users can only SELECT their own products
CREATE POLICY products_select ON products FOR SELECT
USING (auth.uid() = user_id);

-- Users can only INSERT with their own user_id
CREATE POLICY products_insert ON products FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- Users can only UPDATE their own products
CREATE POLICY products_update ON products FOR UPDATE
USING (auth.uid() = user_id);

-- Users can only DELETE their own products
CREATE POLICY products_delete ON products FOR DELETE
USING (auth.uid() = user_id);
```

**Competitors Table Policies**
```sql
-- Users can only SELECT competitors of their own products
CREATE POLICY competitors_select ON competitors FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM products
        WHERE products.id = competitors.product_id
        AND products.user_id = auth.uid()
    )
);
```

**Price History Table Policies**
```sql
-- Users can only SELECT price history of their own products
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

### JWT Token Flow

```
1. User logs in via Supabase Auth
2. Supabase returns JWT token
3. User includes token in API requests: Authorization: Bearer <token>
4. FastAPI extracts token via HTTPBearer dependency
5. get_current_user() verifies token with Supabase
6. Token contains user_id in payload
7. Supabase client uses token to enforce RLS policies
```

### Service Key vs. Anon Key

**Anon Key** (User requests)
- Used in frontend/API requests from authenticated users
- Enforces RLS policies
- Limited to user's own data

**Service Key** (Background tasks)
- Used in Celery tasks
- Bypasses RLS policies
- Full database access (needed to scrape all users' products)

**Security:**
- Anon key can be public (safe to expose)
- Service key MUST be secret (stored in .env, never committed)

---

## Background Task System

### Celery Architecture

```
┌───────────────┐       ┌──────────────┐       ┌───────────────┐
│  Celery Beat  │──────>│    Redis     │<──────│ Celery Worker │
│  (Scheduler)  │       │   (Broker)   │       │               │
└───────────────┘       └──────────────┘       └───────┬───────┘
                                                        │
        Triggers daily task                  Executes tasks
        at 2 AM UTC                                     │
                                                        ▼
                                              ┌─────────────────┐
                                              │    Supabase     │
                                              │    (Database)   │
                                              └─────────────────┘
```

### Task Flow

**Daily Scraping (`scrape_all_products`)**

```
1. Celery Beat triggers at 2 AM UTC
2. Task fetches all active products from database (using service key)
3. For each product:
   ├─ Fetch all competitors
   └─ Queue scrape_single_competitor(competitor_id) task
4. Tasks execute in parallel (worker pool)
5. Each task:
   ├─ Check idempotency (already scraped today?)
   ├─ Fetch competitor URL from database
   ├─ Detect platform, scrape price
   ├─ Store result in price_history
   └─ Return success/failure
```

**Manual Scraping (`POST /api/scrape/manual/{product_id}`)**

```
1. User sends request
2. Endpoint fetches competitors for product
3. Queues scrape_single_competitor task for each
4. Returns immediately (async)
5. User can poll /api/prices/{product_id}/history to see results
```

### Idempotency

**Problem**: Task may retry if worker crashes. Don't want duplicate price records for same day.

**Solution**: Check before scraping:
```python
def scrape_single_competitor(competitor_id: str):
    # Check if already scraped today
    today = datetime.utcnow().date()
    existing = db.query(PriceHistory).filter(
        PriceHistory.competitor_id == competitor_id,
        PriceHistory.scraped_at >= today
    ).first()

    if existing:
        return existing  # Skip, already scraped

    # Proceed with scrape
    ...
```

### Retry Logic

```python
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60  # 60s, then 120s, then 240s (exponential)
)
def scrape_single_competitor(self, competitor_id: str):
    try:
        # Scraping logic
        ...
    except Exception as exc:
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
```

---

## Usage Flow Examples

### Example 1: Discover Shopify Store

**Request:**
```bash
POST /api/stores/discover
Authorization: Bearer <jwt>

{
  "url": "https://example.myshopify.com",
  "keyword": "laptop",
  "limit": 20
}
```

**Response:**
```json
{
  "platform": "shopify",
  "store_url": "https://example.myshopify.com",
  "total_found": 12,
  "products": [
    {
      "name": "MacBook Pro 14\" - Silver",
      "price": 1999.00,
      "currency": "USD",
      "image_url": "https://...",
      "product_url": "https://example.myshopify.com/products/macbook-pro",
      "platform": "shopify",
      "variant_id": "12345",
      "sku": "MBP14-SLV",
      "in_stock": true
    },
    ...
  ],
  "error": null
}
```

### Example 2: Track Discovered Products

**Request:**
```bash
POST /api/stores/track
Authorization: Bearer <jwt>

{
  "group_name": "My Laptops",
  "product_urls": [
    "https://example.myshopify.com/products/macbook-pro",
    "https://competitor.com/products/similar-laptop"
  ],
  "alert_threshold_percent": 15
}
```

**Response:**
```json
{
  "group_id": "uuid-123",
  "group_name": "My Laptops",
  "products_added": 2
}
```

### Example 3: View Price History

**Request:**
```bash
GET /api/prices/uuid-123/history
Authorization: Bearer <jwt>
```

**Response:**
```json
{
  "prices": [
    {
      "id": "uuid-456",
      "competitor_id": "uuid-789",
      "price": 1899.00,
      "currency": "USD",
      "scraped_at": "2024-01-15T02:00:00Z",
      "scrape_status": "success",
      "error_message": null
    },
    {
      "id": "uuid-457",
      "competitor_id": "uuid-789",
      "price": 1999.00,
      "currency": "USD",
      "scraped_at": "2024-01-14T02:00:00Z",
      "scrape_status": "success",
      "error_message": null
    }
  ],
  "total": 2
}
```

---

## Summary

**PriceHawk** separates product discovery from price monitoring, using a plugin-based architecture to support multiple e-commerce platforms. The system enforces security at the database level via RLS, handles background scraping with Celery, and provides a clean async API via FastAPI.

**Key Design Decisions:**
- **Plugin handlers**: Easy to add new platforms
- **Async-first**: Scales to thousands of products
- **RLS enforcement**: Database-level security
- **Idempotent tasks**: Safe retries without duplicates
- **Unified product model**: Consistent data across platforms
