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

-- Drop existing trigger if exists (idempotent)
DROP TRIGGER IF EXISTS products_updated_at ON products;

-- Create trigger
CREATE TRIGGER products_updated_at
  BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ---------------------------------------------------------------------------
-- SECTION 4: Row Level Security (RLS) Policies
-- ---------------------------------------------------------------------------

-- Enable RLS on all tables
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE competitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_history ENABLE ROW LEVEL SECURITY;

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
-- SECTION 5: Verification Queries
-- ---------------------------------------------------------------------------
-- Run these to verify setup was successful:

-- 1. Check all tables exist
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('products', 'competitors', 'price_history')
ORDER BY table_name;

-- 2. Check RLS is enabled
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE tablename IN ('products', 'competitors', 'price_history')
ORDER BY tablename;

-- 3. List all policies
SELECT schemaname, tablename, policyname, cmd
FROM pg_policies
WHERE tablename IN ('products', 'competitors', 'price_history')
ORDER BY tablename, cmd;

-- 4. Check indexes
SELECT tablename, indexname
FROM pg_indexes
WHERE tablename IN ('products', 'competitors', 'price_history')
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
