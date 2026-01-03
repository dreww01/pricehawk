# PriceHawk - Complex Logic Documentation

## JWT Verification with JWKS (ES256)

**Location:** `app/core/security.py` - `verify_token()` function

**Purpose:** Verify Supabase JWT tokens for authenticated API requests

**How it works:**
1. Supabase uses ES256 (ECDSA) algorithm for JWT signing
2. ES256 requires public key verification (not symmetric secret like HS256)
3. We fetch the public key from Supabase's JWKS endpoint: `{sb_url}/auth/v1/.well-known/jwks.json`
4. PyJWKClient extracts the correct key based on the token's `kid` (key ID) header
5. Token is decoded and verified with the public key
6. JWKS client is cached with `@lru_cache` to avoid repeated HTTP requests

**Why this approach:**
- Industry standard for asymmetric JWT verification
- Supabase migrated from HS256 to ES256 for better security
- JWKS allows key rotation without code changes
- Caching prevents performance overhead from fetching keys on every request

---

## UUID Primary Keys for Products/Competitors

**Location:** Supabase SQL schema - `products` and `competitors` tables

**Purpose:** Uniquely identify each product/competitor record

**How it works:**
1. When a row is inserted, Supabase auto-generates a UUID via `DEFAULT gen_random_uuid()`
2. UUID format: `69c031e7-6951-44b2-9b66-5bafc53497c6` (36 characters)
3. Client receives the UUID in the create response
4. Client uses UUID for all subsequent operations (GET, PUT, DELETE)

**Why UUID instead of auto-increment (1, 2, 3):**
- **Security:** UUIDs are unguessable - attackers can't enumerate `/products/1`, `/products/2`
- **Distributed systems:** No central counter needed, can generate IDs on any server
- **Merge-friendly:** No ID collisions when combining data from multiple sources

**Tradeoff:**
- Longer URLs and slightly more storage than integers
- Not human-readable (can't say "product #5")

---

## Supabase Client with RLS Authentication

**Location:** `app/db/database.py` - `get_supabase_client()` function

**Purpose:** Create Supabase client that respects Row Level Security policies

**How it works:**
1. Create client with `sb_anon_key` (includes apikey header automatically)
2. Call `client.postgrest.auth(access_token)` to set Authorization header
3. Supabase uses the JWT to identify the user via `auth.uid()` in RLS policies
4. User can only access rows where `user_id = auth.uid()`

**Why this approach:**
- RLS is enforced at database level (secure by default)
- Even if API has bugs, users can't access others' data
- Single source of truth for authorization rules

**Two modes:**
- With `access_token`: Respects RLS, user sees only their data
- Without (service key): Bypasses RLS, for background tasks only
