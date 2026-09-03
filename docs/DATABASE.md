# Database Schema & Persistence Guide

## Overview

PriceHawk utilizes **Supabase PostgreSQL** as its primary persistence and identity engine. The deployed schema uses native PostgreSQL **Row-Level Security (RLS)** on the product, competitor, price-history, insights, and tracking-job tables, plus application/service-role filtering for alert tables. Operators must treat alert table tenant isolation as application-enforced unless additional RLS policies are added.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               Relational Hierarchy Overview                            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│                                 auth.users (Supabase Identity)                         │
│                                       │                                                │
│        ┌──────────────────────────────┼──────────────────────────────┐                 │
│        ▼                              ▼                              ▼                 │
│     products                 user_alert_settings               alert_history           │
│        │                                                             │                 │
│   ┌────┴────────────────────────┐                                    │                 │
│   ▼                             ▼                                    ▼                 │
│ competitors                  insights                          pending_alerts          │
│   │                                                                  │                 │
│   ▼                                                                  │                 │
│ price_history ◄──────────────────────────────────────────────────────┘                 │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Entity-Relationship Diagram (ERD)

```mermaid
erDiagram
    USERS ||--o{ PRODUCTS : "creates (1:N)"
    USERS ||--o| USER_ALERT_SETTINGS : "configures (1:1)"
    USERS ||--o{ PENDING_ALERTS : "receives (1:N)"
    USERS ||--o{ ALERT_HISTORY : "audits (1:N)"
    USERS ||--o{ TRACKING_JOBS : "owns (1:N)"

    PRODUCTS ||--o{ COMPETITORS : "contains (1:N)"
    PRODUCTS ||--o{ INSIGHTS : "generates (1:N)"
    PRODUCTS ||--o{ PENDING_ALERTS : "references (1:N)"

    COMPETITORS ||--o{ PRICE_HISTORY : "tracks (1:N)"
    COMPETITORS ||--o{ PENDING_ALERTS : "monitors (1:N)"

    USERS {
        uuid id PK "Supabase Auth UID"
        string email "User email address"
        timestamptz created_at "Registration timestamp"
    }

    PRODUCTS {
        uuid id PK "gen_random_uuid()"
        uuid user_id FK "auth.users(id) ON DELETE CASCADE"
        string product_name "Tracking group title"
        boolean is_active "Active tracking flag"
        timestamptz created_at "Creation timestamp"
        timestamptz updated_at "Auto-updated timestamp"
    }

    COMPETITORS {
        uuid id PK "gen_random_uuid()"
        uuid product_id FK "products(id) ON DELETE CASCADE"
        text url "Competitor product URL"
        string retailer_name "Extracted retailer name"
        decimal alert_threshold_percent "Threshold percentage"
        string expected_currency "ISO currency code (USD, EUR, GBP)"
        timestamptz created_at "Creation timestamp"
    }

    PRICE_HISTORY {
        uuid id PK "gen_random_uuid()"
        uuid competitor_id FK "competitors(id) ON DELETE CASCADE"
        decimal price "Extracted price amount"
        string currency "ISO currency code"
        timestamptz scraped_at "Scrape execution time"
        string scrape_status "'success' or 'failed'"
        text error_message "Diagnostic error detail"
    }

    INSIGHTS {
        uuid id PK "gen_random_uuid()"
        uuid product_id FK "products(id) ON DELETE CASCADE"
        text insight_text "AI generated insight summary"
        string insight_type "'pattern', 'alert', 'recommendation'"
        decimal confidence_score "Model confidence (0.00 - 1.00)"
        timestamptz generated_at "Generation timestamp"
    }

    PENDING_ALERTS {
        uuid id PK "gen_random_uuid()"
        uuid user_id FK "auth.users(id) ON DELETE CASCADE"
        uuid product_id FK "products(id) ON DELETE CASCADE"
        uuid competitor_id FK "competitors(id) ON DELETE CASCADE"
        string alert_type "'price_drop', 'price_increase', 'currency_changed'"
        decimal old_price "Previous recorded price"
        decimal new_price "Newly scraped price"
        decimal price_change_percent "Percentage delta"
        decimal threshold_percent "Triggering threshold"
        string old_currency "Previous currency code"
        string new_currency "New currency code"
        boolean included_in_digest "Sent in digest flag"
        timestamptz detected_at "Detection timestamp"
    }

    USER_ALERT_SETTINGS {
        uuid id PK "gen_random_uuid()"
        uuid user_id FK "auth.users(id) UNIQUE ON DELETE CASCADE"
        boolean email_enabled "Master notification toggle"
        integer digest_frequency_hours "6, 12, or 24 hours"
        boolean alert_price_drop "Notify on price drop"
        boolean alert_price_increase "Notify on price increase"
        timestamptz last_digest_sent_at "Last dispatch timestamp"
        timestamptz created_at "Creation timestamp"
        timestamptz updated_at "Update timestamp"
    }

    ALERT_HISTORY {
        uuid id PK "gen_random_uuid()"
        uuid user_id FK "auth.users(id) ON DELETE CASCADE"
        timestamptz digest_sent_at "Dispatch timestamp"
        integer alerts_count "Number of alerts in digest"
        string email_status "'pending', 'sent', 'failed'"
        text error_message "Dispatch error detail"
    }

    TRACKING_JOBS {
        uuid id PK "gen_random_uuid()"
        uuid user_id FK "auth.users(id) ON DELETE CASCADE"
        uuid product_group_id FK "products(id) ON DELETE CASCADE"
        integer total_items "Total items to scrape"
        integer completed_items "Completed scrape count"
        integer failed_items "Failed scrape count"
        string status "'pending', 'processing', 'completed', 'failed'"
        timestamptz created_at "Job creation timestamp"
        timestamptz updated_at "Job update timestamp"
    }
```

---

## Data Dictionary & Schema Specifications

### 1. `products`
Root entity representing user-defined tracking groups (e.g., "Flagship Ultrabooks").

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique product tracking group ID |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES auth.users(id) ON DELETE CASCADE` | Owner user identifier |
| `product_name` | `VARCHAR(255)` | `NOT NULL` | Human-readable product name |
| `is_active` | `BOOLEAN` | Default `true` | When `false`, excluded from Celery Beat daily scrapes |
| `created_at` | `TIMESTAMPTZ` | Default `now()` | Entity creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Default `now()` | Auto-updated via PostgreSQL trigger |

### 2. `competitors`
Competitor store listings mapped to a parent product tracking group.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique competitor identifier |
| `product_id` | `UUID` | `NOT NULL`, `REFERENCES products(id) ON DELETE CASCADE` | Associated product group |
| `url` | `TEXT` | `NOT NULL` | Full target URL |
| `retailer_name` | `VARCHAR(100)` | Nullable | Extracted domain or retailer name |
| `alert_threshold_percent` | `DECIMAL(5,2)` | Default `10.00` | Percentage price delta required to trigger alert |
| `expected_currency` | `VARCHAR(3)` | Default `'USD'` | Base ISO currency code for price comparisons |
| `created_at` | `TIMESTAMPTZ` | Default `now()` | Registration timestamp |

### 3. `price_history`
Append-only time-series ledger of scraped price observations.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique record identifier |
| `competitor_id` | `UUID` | `NOT NULL`, `REFERENCES competitors(id) ON DELETE CASCADE` | Observed competitor |
| `price` | `DECIMAL(10,2)` | Nullable | Normalized price (NULL when scrape fails) |
| `currency` | `VARCHAR(3)` | Default `'USD'` | Currency of scraped price |
| `scraped_at` | `TIMESTAMPTZ` | Default `now()` | Execution timestamp |
| `scrape_status` | `VARCHAR(20)` | `NOT NULL`, `CHECK (scrape_status IN ('success', 'failed'))` | Scrape execution status |
| `error_message` | `TEXT` | Nullable | Detailed failure trace if `failed` |

### 4. `insights`
AI-generated market trends and pricing recommendations produced by Groq Llama 3.3 70B.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique insight record ID |
| `product_id` | `UUID` | `NOT NULL`, `REFERENCES products(id) ON DELETE CASCADE` | Analyzed product group |
| `insight_text` | `TEXT` | `NOT NULL` | Sanitized AI recommendation text |
| `insight_type` | `VARCHAR(50)` | `NOT NULL`, `CHECK (insight_type IN ('pattern', 'alert', 'recommendation'))` | Category of insight |
| `confidence_score` | `DECIMAL(3,2)` | `NOT NULL`, `CHECK (confidence_score >= 0.00 AND confidence_score <= 1.00)` | Statistical confidence metric |
| `generated_at` | `TIMESTAMPTZ` | Default `now()` | Generation timestamp |

### 5. `pending_alerts`
Staging queue for detected price movements awaiting digest compilation.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique alert ID |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES auth.users(id) ON DELETE CASCADE` | Recipient user ID |
| `product_id` | `UUID` | `NOT NULL`, `REFERENCES products(id) ON DELETE CASCADE` | Associated product |
| `competitor_id` | `UUID` | `NOT NULL`, `REFERENCES competitors(id) ON DELETE CASCADE` | Associated competitor |
| `alert_type` | `VARCHAR(20)` | `NOT NULL`, `CHECK (alert_type IN ('price_drop', 'price_increase', 'currency_changed'))` | Movement classification |
| `old_price` | `DECIMAL(10,2)` | Nullable | Baseline price |
| `new_price` | `DECIMAL(10,2)` | Nullable | Newly recorded price |
| `price_change_percent` | `DECIMAL(5,2)` | Nullable | Relative percentage change |
| `threshold_percent` | `DECIMAL(5,2)` | Nullable | Trigger threshold at detection |
| `old_currency` | `VARCHAR(3)` | Nullable | Pre-detection currency |
| `new_currency` | `VARCHAR(3)` | Nullable | Detected currency |
| `included_in_digest` | `BOOLEAN` | Default `false` | True once dispatched in an email digest |
| `detected_at` | `TIMESTAMPTZ` | Default `now()` | Detection timestamp |

### 6. `user_alert_settings`
User-level notification delivery preferences and schedule.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Settings identifier |
| `user_id` | `UUID` | `NOT NULL`, `UNIQUE`, `REFERENCES auth.users(id) ON DELETE CASCADE` | Target user (1:1 relationship) |
| `email_enabled` | `BOOLEAN` | Default `true` | Master email notification toggle |
| `digest_frequency_hours` | `INTEGER` | Default `24`, Options: `6, 12, 24` | Batch window frequency in hours |
| `alert_price_drop` | `BOOLEAN` | Default `true` | Enable price drop notifications |
| `alert_price_increase` | `BOOLEAN` | Default `true` | Enable price increase notifications |
| `last_digest_sent_at` | `TIMESTAMPTZ` | Nullable | Timestamp of previous email dispatch |
| `created_at` | `TIMESTAMPTZ` | Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Default `now()` | Modification timestamp |

### 7. `alert_history`
Immutable audit log of all transmitted email digests.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY`, Default `gen_random_uuid()` | Unique audit log ID |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES auth.users(id) ON DELETE CASCADE` | Recipient user |
| `digest_sent_at` | `TIMESTAMPTZ` | Default `now()` | Dispatch timestamp |
| `alerts_count` | `INTEGER` | `NOT NULL` | Total alert events compiled into digest |
| `email_status` | `VARCHAR(20)` | Default `'pending'`, `CHECK (email_status IN ('pending', 'sent', 'failed'))` | Delivery status |
| `error_message` | `TEXT` | Nullable | SMTP delivery error message on failure |

---

## Indexing Strategy

High-frequency query paths utilize dedicated B-tree indexes:

```sql
-- Product lookup by tenant & active status (used during Celery Beat daily crawl)
CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_products_is_active ON products(is_active);

-- Competitor relation joins
CREATE INDEX IF NOT EXISTS idx_competitors_product_id ON competitors(product_id);

-- Price history time-series queries (charts, latest price lookups, export)
CREATE INDEX IF NOT EXISTS idx_price_history_competitor_id ON price_history(competitor_id);
CREATE INDEX IF NOT EXISTS idx_price_history_scraped_at ON price_history(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_history_status ON price_history(scrape_status);

-- AI insights lookup by product
CREATE INDEX IF NOT EXISTS idx_insights_product_id ON insights(product_id);
CREATE INDEX IF NOT EXISTS idx_insights_generated_at ON insights(generated_at DESC);

-- Alert queue processing (Celery Beat digest batch job)
CREATE INDEX IF NOT EXISTS idx_pending_alerts_user_id ON pending_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_alerts_included ON pending_alerts(included_in_digest);
CREATE INDEX IF NOT EXISTS idx_pending_alerts_detected_at ON pending_alerts(detected_at DESC);

-- Audit log indexing
CREATE INDEX IF NOT EXISTS idx_alert_history_user_id ON alert_history(user_id);
CREATE INDEX IF NOT EXISTS idx_alert_history_sent_at ON alert_history(digest_sent_at DESC);
```

---

## Row-Level Security (RLS) & Tenant Isolation

The deployed `docs/database_schema.sql` enables RLS on exactly these tables:

- `products`
- `competitors`
- `price_history`
- `insights`
- `tracking_jobs`

RLS is **not** currently enabled for `user_alert_settings`, `pending_alerts`, or `alert_history`, and the schema does not create policies for those alert-related tables. Current API code filters these tables by `user_id` and uses service-role clients for background alert processing, but direct database/API access to those tables is not isolated by PostgreSQL RLS. If direct client access to alert tables is required, add and verify RLS policies before deployment.

```mermaid
flowchart LR
    subgraph ClientRequest["Authenticated HTTP Request"]
        Token["Supabase JWT (claims.sub = user_uuid)"]
    end

    subgraph RlsTables["RLS-enabled tables"]
        Products["products: user_id = auth.uid()"]
        Children["competitors / price_history / insights: product ownership"]
        Jobs["tracking_jobs: user_id = auth.uid() for SELECT"]
    end

    subgraph AlertTables["Alert tables without deployed RLS"]
        AppFilter["API filters user_id in application queries"]
    end

    Token --> Products
    Products --> Children
    Token --> Jobs
    Token --> AppFilter
```

### 1. Direct Ownership Policies (`products`)
Users can manipulate only product rows explicitly tagged with their `user_id`:
```sql
CREATE POLICY "Users can view own products"
    ON products FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert own products"
    ON products FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own products"
    ON products FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can delete own products"
    ON products FOR DELETE
    USING (user_id = auth.uid());
```

### 2. Join-Based Cascading Policies (`competitors`, `price_history`, `insights`)
Access to child entities is gated by ownership of the ancestor `products` record:
```sql
CREATE POLICY "Users can view own competitors"
    ON competitors FOR SELECT
    USING (
        product_id IN (
            SELECT id FROM products WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Users can view own price history"
    ON price_history FOR SELECT
    USING (
        competitor_id IN (
            SELECT c.id FROM competitors c
            JOIN products p ON c.product_id = p.id
            WHERE p.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can view own insights"
    ON insights FOR SELECT
    USING (
        product_id IN (
            SELECT id FROM products WHERE user_id = auth.uid()
        )
    );
```

`competitors` also has insert, update, and delete policies using the same product-ownership predicate. `price_history` is read-only for authenticated clients. `insights` allows authenticated users to delete their own product insights and includes a permissive insert policy for backend/service writes.

### 3. Tracking Job Policies (`tracking_jobs`)
Authenticated users can view only their own tracking jobs, while background workers use the service role to manage all jobs:
```sql
CREATE POLICY "Users can view own tracking jobs"
    ON tracking_jobs FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Service can manage tracking jobs"
    ON tracking_jobs FOR ALL
    USING (true)
    WITH CHECK (true);
```

### 4. Alert Table Limitation (`user_alert_settings`, `pending_alerts`, `alert_history`)
These tables have `user_id` foreign keys and supporting indexes, and the FastAPI routes query them with `user_id = current_user.id`. However, the SQL schema does not execute `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` for them and does not create PostgreSQL policies. This is a known deployed-schema limitation rather than a guaranteed database-level tenant boundary.

### 5. Service Role Privileges (Background Tasks)
Background Celery workers utilize the **Supabase Service Key**, which bypasses RLS at the PostgreSQL connection level. This enables asynchronous workers to write price records, compute AI insights across products, manage tracking jobs, and query pending alerts across all tenants.

---

## Schema Deployment & Verification

To initialize or migrate the database, execute `docs/database_schema.sql` within the Supabase SQL Editor.

### Verification Queries
```sql
-- 1. Verify table presence
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'products', 'competitors', 'price_history', 'insights', 'tracking_jobs',
    'pending_alerts', 'user_alert_settings', 'alert_history'
  )
ORDER BY table_name;

-- 2. Confirm exactly which documented tables have RLS enabled
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'products', 'competitors', 'price_history', 'insights', 'tracking_jobs',
    'pending_alerts', 'user_alert_settings', 'alert_history'
  )
ORDER BY tablename;

-- Expected rowsecurity=true: products, competitors, price_history, insights, tracking_jobs.
-- Expected rowsecurity=false: pending_alerts, user_alert_settings, alert_history.

-- 3. Verify deployed RLS policies only for RLS-enabled tables
SELECT tablename, policyname, cmd FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('products', 'competitors', 'price_history', 'insights', 'tracking_jobs')
ORDER BY tablename, cmd, policyname;

-- 4. Confirm alert tables currently have no deployed RLS policies
SELECT tablename, policyname, cmd FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('pending_alerts', 'user_alert_settings', 'alert_history')
ORDER BY tablename, cmd, policyname;
```
