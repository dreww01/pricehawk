-- ============================================================================
-- PriceHawk - Complete Database Schema
-- ============================================================================
-- This file contains ALL SQL needed to set up the database from scratch
-- Run this in Supabase SQL Editor after creating a new project
-- ============================================================================
-- IDEMPOTENT: Safe to run multiple times (uses DROP IF EXISTS / CREATE OR REPLACE)
-- ============================================================================
--
-- Usage:
-- 1. Create new Supabase project
-- 2. Go to SQL Editor
-- 3. Copy/paste this entire file
-- 4. Click "Run"
-- 5. Verify with: SELECT * FROM products; (should return empty result)
--
-- ============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1: Tables
-- ---------------------------------------------------------------------------

-- Products table
CREATE TABLE IF NOT EXISTS products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  product_name VARCHAR(255) NOT NULL,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Competitors table
CREATE TABLE IF NOT EXISTS competitors (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  retailer_name VARCHAR(100),
  alert_threshold_percent DECIMAL(5,2) DEFAULT 10.00,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Price history table
CREATE TABLE IF NOT EXISTS price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_id UUID NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    price DECIMAL(10, 2),
    currency VARCHAR(3) DEFAULT 'USD',
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    scrape_status VARCHAR(20) NOT NULL CHECK (scrape_status IN ('success', 'failed')),
    error_message TEXT
);

-- Insights table (Milestone 5: AI-generated price analysis)
CREATE TABLE IF NOT EXISTS insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    insight_text TEXT NOT NULL,
    insight_type VARCHAR(50) NOT NULL CHECK (insight_type IN ('pattern', 'alert', 'recommendation')),
    confidence_score DECIMAL(3,2) NOT NULL CHECK (confidence_score >= 0.00 AND confidence_score <= 1.00),
    generated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pending alerts table (stores price changes before digest send)
CREATE TABLE IF NOT EXISTS pending_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    competitor_id UUID NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    alert_type VARCHAR(50) NOT NULL CHECK (alert_type IN ('price_drop', 'price_increase')),
    old_price DECIMAL(10,2) NOT NULL,
    new_price DECIMAL(10,2) NOT NULL,
    price_change_percent DECIMAL(5,2) NOT NULL,
    threshold_percent DECIMAL(5,2) NOT NULL,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    included_in_digest BOOLEAN DEFAULT false
);

-- Alert history table (Milestone 6: tracks sent digest emails)
CREATE TABLE IF NOT EXISTS alert_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    digest_sent_at TIMESTAMPTZ DEFAULT NOW(),
    alerts_count INTEGER NOT NULL DEFAULT 0,
    email_status VARCHAR(20) NOT NULL CHECK (email_status IN ('sent', 'failed', 'pending')),
    error_message TEXT,
    alert_ids UUID[] NOT NULL
);

-- User alert settings table (Milestone 6: user preferences for notifications)
CREATE TABLE IF NOT EXISTS user_alert_settings (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email_enabled BOOLEAN DEFAULT true,
    digest_frequency_hours INTEGER DEFAULT 24 CHECK (digest_frequency_hours IN (6, 12, 24)),
    alert_price_drop BOOLEAN DEFAULT true,
    alert_price_increase BOOLEAN DEFAULT true,
    last_digest_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- SECTION 2: Indexes for Performance
-- ---------------------------------------------------------------------------

-- Products indexes
CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_products_is_active ON products(is_active);

-- Competitors indexes
CREATE INDEX IF NOT EXISTS idx_competitors_product_id ON competitors(product_id);

-- Price history indexes
CREATE INDEX IF NOT EXISTS idx_price_history_competitor_id ON price_history(competitor_id);
CREATE INDEX IF NOT EXISTS idx_price_history_scraped_at ON price_history(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_history_status ON price_history(scrape_status);

-- Insights indexes
CREATE INDEX IF NOT EXISTS idx_insights_product_id ON insights(product_id);
CREATE INDEX IF NOT EXISTS idx_insights_generated_at ON insights(generated_at DESC);

-- Pending alerts indexes
CREATE INDEX IF NOT EXISTS idx_pending_alerts_user_id ON pending_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_alerts_included ON pending_alerts(included_in_digest);
CREATE INDEX IF NOT EXISTS idx_pending_alerts_detected_at ON pending_alerts(detected_at DESC);

-- Alert history indexes
CREATE INDEX IF NOT EXISTS idx_alert_history_user_id ON alert_history(user_id);
CREATE INDEX IF NOT EXISTS idx_alert_history_sent_at ON alert_history(digest_sent_at DESC);


-- ---------------------------------------------------------------------------
-- SECTION 3: Triggers
-- ---------------------------------------------------------------------------

-- Auto-update updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing triggers if exist (idempotent)
DROP TRIGGER IF EXISTS products_updated_at ON products;
DROP TRIGGER IF EXISTS user_alert_settings_updated_at ON user_alert_settings;

-- Create triggers
CREATE TRIGGER products_updated_at
  BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER user_alert_settings_updated_at
  BEFORE UPDATE ON user_alert_settings
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ---------------------------------------------------------------------------
-- SECTION 4: Row Level Security (RLS) Policies
-- ---------------------------------------------------------------------------

-- Enable RLS on all tables
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE insights ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_alert_settings ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Products Table Policies
-- ---------------------------------------------------------------------------

-- Drop existing policies (idempotent)
DROP POLICY IF EXISTS "Users can view own products" ON products;
DROP POLICY IF EXISTS "Users can insert own products" ON products;
DROP POLICY IF EXISTS "Users can update own products" ON products;
DROP POLICY IF EXISTS "Users can delete own products" ON products;

-- Create policies
CREATE POLICY "Users can view own products"
    ON products
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert own products"
    ON products
    FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own products"
    ON products
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can delete own products"
    ON products
    FOR DELETE
    USING (user_id = auth.uid());


-- ---------------------------------------------------------------------------
-- Competitors Table Policies
-- ---------------------------------------------------------------------------

-- Drop existing policies (idempotent)
DROP POLICY IF EXISTS "Users can view own competitors" ON competitors;
DROP POLICY IF EXISTS "Users can insert own competitors" ON competitors;
DROP POLICY IF EXISTS "Users can update own competitors" ON competitors;
DROP POLICY IF EXISTS "Users can delete own competitors" ON competitors;

-- Create policies (via product ownership)
CREATE POLICY "Users can view own competitors"
    ON competitors
    FOR SELECT
    USING (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Users can insert own competitors"
    ON competitors
    FOR INSERT
    WITH CHECK (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Users can update own competitors"
    ON competitors
    FOR UPDATE
    USING (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    )
    WITH CHECK (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Users can delete own competitors"
    ON competitors
    FOR DELETE
    USING (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );


-- ---------------------------------------------------------------------------
-- Price History Table Policies
-- ---------------------------------------------------------------------------

-- Drop existing policies (idempotent)
DROP POLICY IF EXISTS "Users can view own price history" ON price_history;

-- Create policies (read-only for users, via competitor → product ownership)
CREATE POLICY "Users can view own price history"
    ON price_history
    FOR SELECT
    USING (
        competitor_id IN (
            SELECT c.id FROM competitors c
            JOIN products p ON c.product_id = p.id
            WHERE p.user_id = auth.uid()
        )
    );

-- Note: No INSERT/UPDATE/DELETE policies for price_history
-- Only service key (backend) can modify price history


-- ---------------------------------------------------------------------------
-- Insights Table Policies
-- ---------------------------------------------------------------------------

-- Drop existing policies (idempotent)
DROP POLICY IF EXISTS "Users can view own insights" ON insights;
DROP POLICY IF EXISTS "Service can insert insights" ON insights;
DROP POLICY IF EXISTS "Users can delete own insights" ON insights;

-- Create policies
CREATE POLICY "Users can view own insights"
    ON insights
    FOR SELECT
    USING (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );

-- Service role can insert insights (for AI service)
CREATE POLICY "Service can insert insights"
    ON insights
    FOR INSERT
    WITH CHECK (true);

-- Users can delete their own insights (optional)
CREATE POLICY "Users can delete own insights"
    ON insights
    FOR DELETE
    USING (
        product_id IN (
            SELECT id FROM products
            WHERE user_id = auth.uid()
        )
    );


-- ---------------------------------------------------------------------------
-- Pending Alerts Table Policies
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view own pending alerts" ON pending_alerts;
DROP POLICY IF EXISTS "Service can insert pending alerts" ON pending_alerts;
DROP POLICY IF EXISTS "Service can update pending alerts" ON pending_alerts;
DROP POLICY IF EXISTS "Service can delete pending alerts" ON pending_alerts;

CREATE POLICY "Users can view own pending alerts"
    ON pending_alerts
    FOR SELECT
    USING (user_id = auth.uid());

-- Service role can manage pending alerts
CREATE POLICY "Service can insert pending alerts"
    ON pending_alerts
    FOR INSERT
    WITH CHECK (true);

CREATE POLICY "Service can update pending alerts"
    ON pending_alerts
    FOR UPDATE
    USING (true);

CREATE POLICY "Service can delete pending alerts"
    ON pending_alerts
    FOR DELETE
    USING (true);


-- ---------------------------------------------------------------------------
-- Alert History Table Policies
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view own alert history" ON alert_history;
DROP POLICY IF EXISTS "Service can insert alert history" ON alert_history;

CREATE POLICY "Users can view own alert history"
    ON alert_history
    FOR SELECT
    USING (user_id = auth.uid());

-- Service role can insert alert history
CREATE POLICY "Service can insert alert history"
    ON alert_history
    FOR INSERT
    WITH CHECK (true);


-- ---------------------------------------------------------------------------
-- User Alert Settings Table Policies
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS "Users can view own settings" ON user_alert_settings;
DROP POLICY IF EXISTS "Users can insert own settings" ON user_alert_settings;
DROP POLICY IF EXISTS "Users can update own settings" ON user_alert_settings;

CREATE POLICY "Users can view own settings"
    ON user_alert_settings
    FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert own settings"
    ON user_alert_settings
    FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own settings"
    ON user_alert_settings
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());


-- ---------------------------------------------------------------------------
-- SECTION 5: Verification Queries
-- ---------------------------------------------------------------------------
-- Run these to verify setup was successful:

-- 1. Check all tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('products', 'competitors', 'price_history', 'insights', 'pending_alerts', 'alert_history', 'user_alert_settings')
ORDER BY table_name;

-- 2. Check RLS is enabled
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE tablename IN ('products', 'competitors', 'price_history', 'insights', 'pending_alerts', 'alert_history', 'user_alert_settings')
ORDER BY tablename;

-- 3. List all policies
SELECT schemaname, tablename, policyname, cmd
FROM pg_policies
WHERE tablename IN ('products', 'competitors', 'price_history', 'insights', 'pending_alerts', 'alert_history', 'user_alert_settings')
ORDER BY tablename, cmd;

-- 4. Check indexes
SELECT tablename, indexname
FROM pg_indexes
WHERE tablename IN ('products', 'competitors', 'price_history', 'insights', 'pending_alerts', 'alert_history', 'user_alert_settings')
ORDER BY tablename, indexname;

-- 5. Verify triggers
SELECT trigger_name, event_manipulation, event_object_table
FROM information_schema.triggers
WHERE event_object_table = 'products';


-- ============================================================================
-- Setup Complete!
-- ============================================================================
--
-- Next steps:
-- 1. Create test user in Supabase Dashboard (Authentication → Users)
-- 2. Get access token for testing
-- 3. Test API endpoints with authentication
-- 4. Verify RLS by trying to access data as different users
--
-- See supabase_setup.md for detailed instructions
-- ============================================================================
