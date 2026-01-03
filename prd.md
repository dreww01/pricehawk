**PRODUCT REQUIREMENTS DOCUMENT (PRD)** **PriceHawk**

---

## **OVERVIEW**

**Product:** API service that monitors competitor product prices, detects changes, analyzes patterns with AI, and sends alerts.

**Target Users:** E-commerce businesses, retailers, dropshippers

**Timeline:** 2 weeks (API) + 3-4 days (UI)

**Tech Stack:**
- **Backend:** FastAPI, Supabase (Auth + PostgreSQL), Celery, Redis, BeautifulSoup/Scrapy, Groq API
- **Frontend:** Jinja2, HTMX, Tailwind CSS, DaisyUI

---

## **CORE FEATURES**

1. User authentication via Supabase
2. Add/manage competitor products to monitor
3. Automated daily price scraping
4. AI-powered price analysis and pattern detection
5. Alert system (email notifications)
6. Price history tracking and retrieval
7. Export data functionality

---

## **MILESTONE-BASED IMPLEMENTATION**

---

### **MILESTONE 1: Supabase Setup & FastAPI Integration (Days 1-2)**

**Deliverables:**

- Supabase project created with PostgreSQL database
- Supabase Email/Password authentication enabled
- FastAPI project with proper folder structure
- JWT token verification middleware implemented
- Environment variables configuration
- Health check and protected test endpoint

**API Endpoints:**

```
GET /api/health
GET /api/auth/me (protected - returns current user)
```

**Database Setup:**

- Create Supabase project
- Enable Row Level Security (RLS) on all tables
- Configure authentication settings
- Get project URL, anon key, and service key

**Testing Checklist:**

- [ ]  Supabase project created and accessible
- [ ]  User can signup via Supabase dashboard/client
- [ ]  User can login and receive valid JWT token
- [ ]  FastAPI successfully verifies Supabase JWT
- [ ]  Protected endpoint rejects requests without token
- [ ]  Protected endpoint rejects invalid/expired tokens
- [ ]  Protected endpoint accepts valid token and returns user data
- [ ]  Environment variables load correctly from .env file

**🔒 SECURITY CHECKPOINT:**

- **Issue:** Supabase service key exposed in version control or client-side code
- **Fix Required:** Store service key only in .env file, add .env to .gitignore, use anon key for client auth only
- **Issue:** Row Level Security (RLS) not enabled on tables
- **Fix Required:** Enable RLS on ALL tables, create policies that verify auth.uid() matches user_id
- **Issue:** No HTTPS enforcement in production
- **Fix Required:** Configure FastAPI to reject HTTP requests, force HTTPS only
- **Issue:** JWT tokens never expire or have excessive expiration time
- **Fix Required:** Set reasonable token expiration (24 hours), implement refresh token flow

**Critical Files Needed:**

```
.env (DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY)
main.py
/app/core/auth.py (JWT verification)
/app/core/config.py (settings management)
requirements.txt
```

---

### **MILESTONE 2: Product Management (Days 3-4)**

**Deliverables:**

- Product CRUD endpoints (Create, Read, Update, Delete)
- Competitor URLs management within products
- User isolation via RLS policies
- Input validation using Pydantic models
- Proper error handling and HTTP status codes

**API Endpoints:**

```
POST /api/products (protected)
GET /api/products (protected - list all user's products)
GET /api/products/{product_id} (protected)
PUT /api/products/{product_id} (protected)
DELETE /api/products/{product_id} (protected)
```

**Database Schema:**

```sql
products table:
- id (UUID, primary key)
- user_id (UUID, references auth.users)
- product_name (VARCHAR 255)
- created_at (TIMESTAMP)
- updated_at (TIMESTAMP)
- is_active (BOOLEAN)

competitors table:
- id (UUID, primary key)
- product_id (UUID, references products)
- url (TEXT)
- retailer_name (VARCHAR 100)
- alert_threshold_percent (DECIMAL, default 10.00)
- created_at (TIMESTAMP)
```

**RLS Policies Required:**

- Users can only SELECT their own products (WHERE user_id = auth.uid())
- Users can only INSERT products with their own user_id
- Users can only UPDATE their own products
- Users can only DELETE their own products
- Same policies for competitors table (via product_id join)

**Pydantic Models:**

- ProductCreate (product_name, list of competitor URLs)
- ProductUpdate (product_name, is_active, alert_threshold)
- ProductResponse (all fields including competitors list)
- CompetitorCreate (url, retailer_name, alert_threshold)

**Testing Checklist:**

- [ ]  User can create product with 1+ competitor URLs
- [ ]  User can retrieve list of only their products
- [ ]  User can retrieve single product by ID
- [ ]  User cannot access another user’s product (returns 404/403)
- [ ]  User can update product name and settings
- [ ]  User can soft delete product (set is_active=false)
- [ ]  Updated_at timestamp updates automatically
- [ ]  Validation rejects invalid URLs
- [ ]  Validation rejects empty product names
- [ ]  Maximum 50 products per user enforced

**🔒 SECURITY CHECKPOINT:**

- **Issue:** User can access other users’ products by guessing product_id
- **Fix Required:** RLS policies must verify user_id matches auth.uid() for ALL operations
- **Issue:** No URL validation allows localhost, internal IPs, or malicious domains
- **Fix Required:** Validate URLs with regex, block localhost (127.0.0.1), private IPs (192.168.x.x, 10.x.x.x), only allow HTTPS
- **Issue:** User can create unlimited products (resource exhaustion)
- **Fix Required:** Implement limit of 50 products per user, check count before INSERT
- **Issue:** XSS vulnerability via product_name in future UI
- **Fix Required:** Sanitize all string inputs, escape HTML characters
- **Issue:** SQL injection via unvalidated inputs
- **Fix Required:** Use Supabase client or SQLAlchemy ORM (parameterized queries), never concatenate user input into SQL

**Database Indexes:**

```
CREATE INDEX idx_products_user_id ON products(user_id);
CREATE INDEX idx_products_is_active ON products(is_active);
CREATE INDEX idx_competitors_product_id ON competitors(product_id);
```

---

### **MILESTONE 3: Price Scraping Engine (Days 5-7)**

**Deliverables:**

- Web scraper service that extracts prices from URLs
- Support for Amazon, eBay, Walmart (minimum 3 retailers)
- User-agent rotation and rate limiting
- Comprehensive error handling
- Price history storage in database
- Manual scrape endpoint for testing

**API Endpoints:**

```
POST /api/scrape/manual/{product_id} (protected - manual trigger for testing)
GET /api/prices/{product_id}/history (protected - get all price history)
GET /api/prices/latest/{competitor_id} (protected - get latest price)
```

**Database Schema:**

```sql
price_history table:
- id (UUID, primary key)
- competitor_id (UUID, references competitors)
- price (DECIMAL 10,2)
- currency (VARCHAR 3, default 'USD')
- scraped_at (TIMESTAMP)
- scrape_status (VARCHAR 20: 'success' or 'failed')
- error_message (TEXT, nullable)
```

**RLS Policy:**

- Users can only SELECT price_history for their own products (via competitor_id → product_id → user_id join)
- No user INSERT policy (only backend can insert via service key)

**Scraper Requirements:**

- Whitelist allowed domains: amazon.com, ebay.com, walmart.com (and country variants)
- Block localhost, 127.0.0.1, 192.168.x.x, 10.x.x.x
- Random user-agent rotation (minimum 5 different agents)
- Random delay between requests (2-5 seconds)
- Request timeout: 30 seconds max
- Max response size: 5MB
- Max redirects: 5
- Handle different price formats: $19.99, 19,99€, 1.999,00
- Multiple CSS selectors per retailer (fallback logic)

**Price Extraction Strategy:**

- Amazon: Check .a-price-whole, #priceblock_ourprice, .a-offscreen
- eBay: Check .x-price-primary, #prcIsum, .display-price
- Walmart: Check [itemprop=“price”], .price-characteristic
- Parse and clean price text (remove symbols, handle decimals)

**Testing Checklist:**

- [ ]  Successfully scrapes Amazon product page (test 5+ different products)
- [ ]  Successfully scrapes eBay product page (test 5+ different products)
- [ ]  Successfully scrapes Walmart product page (test 5+ different products)
- [ ]  Returns 404 error gracefully for non-existent pages
- [ ]  Returns timeout error after 30 seconds
- [ ]  Rejects URLs from non-whitelisted domains
- [ ]  Rejects localhost/internal IP URLs
- [ ]  Handles redirects correctly (up to 5)
- [ ]  Stops download if response exceeds 5MB
- [ ]  Price extraction accuracy verified manually (100% match)
- [ ]  Handles different currency formats correctly
- [ ]  Stores successful scrape in price_history
- [ ]  Stores failed scrape with error message
- [ ]  User-agent rotation working (check logs)

**🔒 CRITICAL SCRAPING INTEGRITY ISSUES:**

- **Issue:** Websites block scraper due to aggressive request patterns
- **Fix Required:** Implement random delays (2-5 sec), rotate user agents, rate limit to 1 request per domain per 5 seconds globally
- **Issue:** Scraping malicious or private URLs submitted by user
- **Fix Required:** Strict domain whitelist, block all non-public domains, validate before scraping
- **Issue:** Infinite redirect loops crash scraper
- **Fix Required:** Set max_redirects=5, timeout=30 seconds
- **Issue:** Large file downloads (videos, archives) consume memory/bandwidth
- **Fix Required:** Stream response, check Content-Length header, abort if >5MB
- **Issue:** Price format inconsistencies cause parsing failures
- **Fix Required:** Robust regex parser, handle multiple formats, log unparseable prices for review
- **Issue:** Website HTML structure changes, scraper breaks silently
- **Fix Required:** Multiple CSS selectors per site, try all before failing, log failures with URL for manual review
- **Issue:** Scraper leaks user information or cookies
- **Fix Required:** Clean session per request, no persistent cookies, clear headers
- **Issue:** Concurrent scraping of same URL wastes resources
- **Fix Required:** Implement idempotency check (don’t scrape same competitor_id twice in same day)

**Database Indexes:**

```
CREATE INDEX idx_price_history_competitor_id ON price_history(competitor_id);
CREATE INDEX idx_price_history_scraped_at ON price_history(scraped_at DESC);
CREATE INDEX idx_price_history_status ON price_history(scrape_status);
```

---

### **MILESTONE 4: Background Task Scheduler (Days 8-9)**

**Deliverables:**

- Celery worker setup with Redis broker
- Celery Beat scheduler for periodic tasks
- Daily automated scraping of all active products
- Task retry logic with exponential backoff
- Idempotency to prevent duplicate scrapes
- Health check endpoint for worker status

**Celery Tasks:**

```
scrape_all_products() - scheduled daily at 2 AM UTC
scrape_single_product(competitor_id) - individual scrape task
check_worker_health() - health check task
```

**Task Schedule:**

```
Daily scrape: 2 AM UTC every day
Batch size: 50 competitors at a time (prevent memory overflow)
```

**Task Configuration:**

- Max retries: 3
- Retry countdown: 60 seconds (exponential backoff: 60s, 120s, 240s)
- Task timeout: 5 minutes
- Use Supabase service key (bypass RLS for background tasks)

**Idempotency Logic:**

- Check if competitor_id already scraped today (scraped_at >= today 00:00 UTC)
- Skip if already scraped, return cached result
- Prevents duplicate scrapes if task retries

**Testing Checklist:**

- [ ]  Celery worker starts successfully
- [ ]  Redis connection established
- [ ]  Can manually trigger scrape_single_product task
- [ ]  Task executes and stores result in database
- [ ]  Failed tasks retry 3 times with exponential backoff
- [ ]  After 3 failures, task marked as failed permanently
- [ ]  Celery Beat scheduler configured correctly
- [ ]  Daily task triggers at 2 AM UTC (verify with logs)
- [ ]  Idempotency prevents duplicate scrapes on same day
- [ ]  Worker processes tasks without blocking FastAPI
- [ ]  Batch processing limits memory usage (test with 1000+ products)
- [ ]  Health check endpoint returns worker status

**🔒 SECURITY & INTEGRITY CHECKPOINT:**

- **Issue:** Celery worker uses anon key instead of service key
- **Fix Required:** Configure Celery to use SUPABASE_SERVICE_KEY for database operations
- **Issue:** No monitoring if Celery worker stops or crashes
- **Fix Required:** Implement health check endpoint that verifies worker is alive and processing tasks
- **Issue:** Tasks run multiple times if worker crashes mid-execution
- **Fix Required:** Implement idempotency check at task start (check if work already done today)
- **Issue:** Memory leak when processing thousands of products
- **Fix Required:** Process in batches of 50, clear variables after each batch, use generator patterns
- **Issue:** Redis credentials exposed or weak
- **Fix Required:** Use strong Redis password, store in environment variables
- **Issue:** No alerts if daily scrape fails completely
- **Fix Required:** Log task failures, implement monitoring/alerting (e.g., email admin if 0 scrapes succeeded)
- **Issue:** Infinite task queue if tasks keep failing and retrying
- **Fix Required:** Set max_retries=3, after that mark as permanently failed

**Environment Variables:**

```
REDIS_URL=redis://...
CELERY_BROKER_URL=redis://...
CELERY_RESULT_BACKEND=redis://...
```

**Monitoring Requirements:**

- Log total tasks queued
- Log successful scrapes count
- Log failed scrapes count
- Alert if success rate < 80%

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
      auth.py
      products.py
      scraper.py
      insights.py
      alerts.py
      export.py
  /core
    __init__.py
    config.py
    security.py
  /db
    __init__.py
    models.py
    database.py
  /services
    __init__.py
    scraper_service.py
    ai_service.py
    email_service.py
  /tasks
    __init__.py
    celery_app.py
    scraper_tasks.py
    alert_tasks.py
  /tests
    __init__.py
    test_auth.py
    test_products.py
    test_scraper.py
    test_insights.py
    test_alerts.py
    conftest.py
  __init__.py
/templates
  base.html
  /auth
    login.html
    signup.html
  /dashboard
    index.html
  /products
    list.html
    detail.html
    _product_card.html
  /insights
    index.html
  /alerts
    settings.html
  /components
    navbar.html
    flash_messages.html
/static
  /css
    output.css
  /js
    htmx.min.js
main.py
requirements.txt
tailwind.config.js
.env.example
.gitignore
Dockerfile
docker-compose.yml
README.md
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