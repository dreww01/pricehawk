# Alert System Testing Guide (Milestone 6)

## Prerequisites

1. **Database Setup**
   ```bash
   # Run the updated database schema in Supabase SQL Editor
   # Copy contents of database_schema.sql and execute
   ```

2. **Environment Variables**
   ```bash
   # Add to .env file
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USERNAME=resend
   SMTP_PASSWORD=your-resend-api-key  # Get from https://resend.com/api-keys
   FROM_EMAIL=noreply@yourdomain.com
   FROM_NAME=PriceHawk Alerts
   ```

3. **Start Services**
   ```bash
   # Terminal 1: Start FastAPI
   uv run fastapi dev main.py

   # Terminal 2: Start Redis
   redis-server

   # Terminal 3: Start Celery Worker
   uv run celery -A app.tasks.celery_app worker --loglevel=info

   # Terminal 4: Start Celery Beat (for scheduled tasks)
   uv run celery -A app.tasks.celery_app beat --loglevel=info
   ```

---

## Test 1: Alert Settings API

### Get Default Settings
```bash
GET http://localhost:8000/api/alerts/settings
Authorization: Bearer YOUR_JWT_TOKEN
```

**Expected Response:**
```json
{
  "user_id": "uuid",
  "email_enabled": true,
  "digest_frequency_hours": 24,
  "alert_price_drop": true,
  "alert_price_increase": true,
  "last_digest_sent_at": null,
  "created_at": "2025-01-06T...",
  "updated_at": "2025-01-06T..."
}
```

### Update Settings to 6-hour Digest
```bash
PUT http://localhost:8000/api/alerts/settings
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "digest_frequency_hours": 6
}
```

**Expected Response:**
```json
{
  "user_id": "uuid",
  "email_enabled": true,
  "digest_frequency_hours": 6,  // Updated
  "alert_price_drop": true,
  "alert_price_increase": true,
  "last_digest_sent_at": null,
  "created_at": "2025-01-06T...",
  "updated_at": "2025-01-06T..."  // Updated timestamp
}
```

### Disable All Alerts
```bash
PUT http://localhost:8000/api/alerts/settings
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "email_enabled": false
}
```

**Expected:** `email_enabled: false` in response

---

## Test 2: Test Email Endpoint

### Send Test Email to Your Email
```bash
POST http://localhost:8000/api/alerts/test
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "email": "your-email@example.com"
}
```

**Expected Response:**
```json
{
  "success": true,
  "message": "Test email sent to your-email@example.com",
  "email": "your-email@example.com"
}
```

**Check Your Inbox:**
- Subject: "PriceHawk Test Email"
- Body: Simple test message with timestamp
- Both HTML and plain text versions

**Troubleshooting:**
- If fails with "SMTP authentication failed": Check `SMTP_PASSWORD` in `.env`
- If email not received: Check spam folder
- If Resend API key invalid: Get new key from https://resend.com/api-keys

---

## Test 3: Alert Detection (Automatic)

### Setup: Create Product with Competitor
```bash
# 1. Discover products
POST http://localhost:8000/api/stores/discover
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "url": "https://colourpopcosmetics.com",
  "keyword": "lipstick",
  "limit": 5
}

# 2. Track a product
POST http://localhost:8000/api/stores/track
Authorization: Bearer YOUR_JWT_TOKEN
Content-Type: application/json

{
  "group_name": "Test Product for Alerts",
  "product_urls": ["https://colourpopcosmetics.com/products/so-juicy"],
  "alert_threshold_percent": 5.0
}
```

### Trigger Alert by Manual Scrape
```bash
# First scrape (establishes baseline price)
POST http://localhost:8000/api/scrape/manual/{product_id}
Authorization: Bearer YOUR_JWT_TOKEN

# Wait 5 minutes, then scrape again
# If price changed by >5%, alert should be created
```

### Check Pending Alerts
```bash
GET http://localhost:8000/api/alerts/pending
Authorization: Bearer YOUR_JWT_TOKEN
```

**Expected Response (if price changed):**
```json
{
  "alerts": [
    {
      "id": "uuid",
      "product_name": "Test Product for Alerts",
      "competitor_name": "colourpopcosmetics.com",
      "alert_type": "price_drop",  // or "price_increase"
      "old_price": 10.00,
      "new_price": 9.50,
      "price_change_percent": -5.00,
      "currency": "USD",
      "detected_at": "2025-01-06T..."
    }
  ],
  "total": 1
}
```

**If No Alert Created, Check:**
1. Price actually changed by >5%?
2. Check Celery worker logs: `logger.info("Alert created for...")`
3. Check database: `SELECT * FROM pending_alerts;`
4. Email enabled in settings?

---

## Test 4: Manual Digest Trigger (Bypass Schedule)

### Create Pending Alert in Database (For Testing)
```sql
-- Run in Supabase SQL Editor
INSERT INTO pending_alerts (
  user_id,
  product_id,
  competitor_id,
  alert_type,
  old_price,
  new_price,
  price_change_percent,
  threshold_percent
) VALUES (
  'your-user-uuid',  -- Get from auth.users
  'your-product-uuid',  -- Get from products table
  'your-competitor-uuid',  -- Get from competitors table
  'price_drop',
  100.00,
  90.00,
  -10.00,
  5.00
);
```

### Trigger Digest Send Manually (Celery Task)
```python
# In Python shell or create a test script
from app.tasks.scraper_tasks import send_alert_digests

# Call the task directly
result = send_alert_digests()
print(result)
# Expected: {'total_users': 1, 'sent': 1, 'failed': 0}
```

### Check Alert History
```bash
GET http://localhost:8000/api/alerts/history
Authorization: Bearer YOUR_JWT_TOKEN
```

**Expected Response:**
```json
{
  "history": [
    {
      "id": "uuid",
      "digest_sent_at": "2025-01-06T...",
      "alerts_count": 1,
      "email_status": "sent",
      "error_message": null
    }
  ],
  "total": 1
}
```

### Check Email Inbox
**Expected Email:**
- Subject: "PriceHawk Alert: 1 price drop"
- Body contains:
  - Product name: "Test Product for Alerts"
  - Store: "colourpopcosmetics.com"
  - Price change: $100.00 → $90.00
  - Percentage: ↓ 10.0%
  - Color-coded (green for drop)

---

## Test 5: Digest Scheduling (Automatic)

### Verify Celery Beat Schedule
```bash
# Check Celery Beat logs
# Should see:
# - "daily-scrape-all-products" at 2:00 AM UTC
# - "hourly-send-alert-digests" every hour at :00
# - "daily-cleanup-old-alerts" at 3:00 AM UTC
```

### Test Hourly Digest (Wait for Next Hour)
1. Ensure you have pending alerts (from Test 3 or 4)
2. Ensure `email_enabled = true` in settings
3. Ensure `last_digest_sent_at` is either NULL or >6 hours ago (if frequency is 6)
4. Wait until next hour :00
5. Check Celery Beat logs for "Found X users due for alert digest"
6. Check email inbox

---

## Test 6: Configuration Tweaking

### Test Easy Config Blocks

#### Change Minimum Alert Trigger Amount
Edit `app/services/alert_service.py`:
```python
class AlertConfig:
    MIN_SIGNIFICANT_CHANGE_AMOUNT = Decimal("1.00")  # Was 5.00
```
Restart API and test with $1 change.

#### Change Max Alerts Per Digest
Edit `app/services/email_service.py`:
```python
class EmailConfig:
    MAX_ALERTS_PER_DIGEST = 10  # Was 50
```
Create 15 pending alerts, send digest, verify only 10 included.

#### Change Retry Logic
Edit `app/services/email_service.py`:
```python
class EmailConfig:
    MAX_RETRIES = 5  # Was 2
    RETRY_DELAY_SECONDS = 10  # Was 5
```
Test with invalid SMTP credentials, check retry behavior in logs.

---

## Test 7: Edge Cases

### Test 1: No Previous Price (First Scrape)
- Create new competitor
- Scrape immediately
- **Expected:** No alert created (logged: "No previous price to compare")

### Test 2: Price Changed by <$5 and <Threshold
- Threshold: 10%
- Price change: $100 → $102 (2%, $2 change)
- **Expected:** No alert (both below minimum)

### Test 3: Price Changed by <Threshold but >$5
- Threshold: 10%
- Price change: $100 → $106 (6%, $6 change)
- **Expected:** Alert created (significant absolute change)

### Test 4: User Has 100 Pending Alerts
```sql
-- Create 100 pending alerts for user
-- Try to create 101st
```
**Expected:** No new alert created (logged: "User has too many pending alerts")

### Test 5: User Disabled Email
```bash
PUT /api/alerts/settings
{ "email_enabled": false }

# Trigger price change
```
**Expected:** Alert detected but not created (logged: "User has disabled email alerts")

### Test 6: Digest Already Sent This Hour
- Manually trigger `send_alert_digests()`
- Immediately trigger again
- **Expected:** No duplicate emails (idempotency check)

---

## Test 8: Security Tests

### Test XSS in Product Name
```sql
-- Insert product with malicious name
UPDATE products SET product_name = '<script>alert("XSS")</script>' WHERE id = '...';

-- Create alert for this product
-- Send digest
```
**Expected in Email:** `&lt;script&gt;alert("XSS")&lt;/script&gt;` (escaped)

### Test Email Header Injection
```bash
POST /api/alerts/test
Content-Type: application/json

{
  "email": "user@example.com\nBcc: attacker@evil.com"
}
```
**Expected:** 400 error: "Email address contains invalid characters"

### Test Rate Limiting
```python
# Create 101 pending alerts for one user
# Try to send digest
```
**Expected:** Only 50 alerts in email (MAX_ALERTS_PER_DIGEST limit)

---

## Test 9: Database Verification

### Check Tables Exist
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('pending_alerts', 'alert_history', 'user_alert_settings');
-- Should return 3 rows
```

### Check RLS Policies
```sql
SELECT tablename, policyname FROM pg_policies
WHERE tablename IN ('pending_alerts', 'alert_history', 'user_alert_settings');
-- Should show policies for each table
```

### Check Indexes
```sql
SELECT indexname FROM pg_indexes
WHERE tablename = 'pending_alerts';
-- Should show: idx_pending_alerts_user_id, idx_pending_alerts_included, idx_pending_alerts_detected_at
```

### Manual Database Queries
```sql
-- View all pending alerts
SELECT * FROM pending_alerts WHERE included_in_digest = false;

-- View alert history
SELECT * FROM alert_history ORDER BY digest_sent_at DESC;

-- View user settings
SELECT * FROM user_alert_settings;

-- Check alert counts per user
SELECT user_id, COUNT(*) FROM pending_alerts
WHERE included_in_digest = false
GROUP BY user_id;
```

---

## Test 10: Cleanup Task

### Create Old Alerts
```sql
-- Create alert 8 days ago (older than 7-day cleanup threshold)
INSERT INTO pending_alerts (
  user_id, product_id, competitor_id,
  alert_type, old_price, new_price, price_change_percent, threshold_percent,
  detected_at, included_in_digest
) VALUES (
  'your-user-uuid', 'product-uuid', 'competitor-uuid',
  'price_drop', 100.00, 90.00, -10.00, 5.00,
  NOW() - INTERVAL '8 days',  -- 8 days old
  true  -- Already included in digest
);
```

### Run Cleanup Task Manually
```python
from app.tasks.scraper_tasks import cleanup_old_alerts

result = cleanup_old_alerts()
print(result)
# Expected: {'deleted_count': 1}
```

### Verify Cleanup
```sql
-- Check old alerts are gone
SELECT COUNT(*) FROM pending_alerts
WHERE included_in_digest = true
AND detected_at < NOW() - INTERVAL '7 days';
-- Should return 0
```

---

## Expected Celery Beat Output

```
[2025-01-06 10:00:00] Task app.tasks.scraper_tasks.send_alert_digests received
[2025-01-06 10:00:00] Found 1 users due for alert digest
[2025-01-06 10:00:01] Sent digest to user@example.com with 3 alerts
[2025-01-06 10:00:01] Alert digest batch complete: 1 sent, 0 failed out of 1
[2025-01-06 10:00:01] Task app.tasks.scraper_tasks.send_alert_digests succeeded
```

---

## Common Issues & Solutions

### Issue: "SMTP authentication failed"
**Solution:**
- Check `SMTP_PASSWORD` in `.env` is valid Resend API key
- Get new key from https://resend.com/api-keys
- Ensure no extra spaces in `.env` file

### Issue: No email received
**Solution:**
- Check spam folder
- Verify Resend dashboard for delivery status
- Check `alert_history` table for `email_status = 'failed'`
- Review Celery worker logs for errors

### Issue: Alert not created despite price change
**Solution:**
- Check threshold: Is change > `alert_threshold_percent`?
- Check minimum: Is change > $5 absolute?
- Verify `email_enabled = true` in user settings
- Check user doesn't have 100+ pending alerts
- Review scraper logs: `logger.info("Alert created...")`

### Issue: Digest not sending at scheduled time
**Solution:**
- Ensure Celery Beat is running (`celery -A app.tasks.celery_app beat`)
- Check `last_digest_sent_at` + `digest_frequency_hours`
- Verify user has pending alerts: `SELECT * FROM pending_alerts WHERE included_in_digest = false`
- Check Celery Beat logs for schedule execution

### Issue: Duplicate emails sent
**Solution:**
- Check idempotency logic: `last_digest_sent_at` should update after send
- Ensure only one Celery Beat instance running
- Review `alert_history` for duplicate entries

---

## Performance Benchmarks

- **Test email send:** < 2 seconds
- **Alert detection (after scrape):** < 100ms
- **Digest send (1 user, 10 alerts):** < 3 seconds
- **Hourly digest task (100 users):** < 30 seconds
- **Cleanup task:** < 5 seconds

---

## Success Criteria

✅ Test email received successfully
✅ Alert created when price changes >threshold
✅ Digest email contains all pending alerts
✅ User can change digest frequency (6/12/24 hours)
✅ Rate limiting prevents spam (100 pending, 50 per digest)
✅ Automatic cleanup removes old alerts
✅ Security: XSS/injection prevented, no sensitive data in emails
✅ Idempotency: No duplicate emails from same alerts
✅ RLS enforced: Users only see their own data
✅ Configuration blocks work (easy to tweak thresholds)

---

## Delete This File When Done

```bash
rm alert_testing.md
```
