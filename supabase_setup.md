# Supabase Setup Guide

Complete step-by-step guide for setting up Supabase for the Competitor Price Monitor API.

---

## Step 1: Create Supabase Account & Project

1. Go to [supabase.com](https://supabase.com)
2. Click **Start your project** → Sign up with GitHub/Email
3. Click **New Project**
4. Fill in:
   - **Organization**: Select or create one
   - **Project name**: `competitor-price-monitor`
   - **Database password**: Generate a strong password (save this!)
   - **Region**: Choose closest to your users
5. Click **Create new project**
6. Wait 2-3 minutes for project to provision

---

## Step 2: Get Your API Keys

1. In your project dashboard, go to **Settings** (gear icon) → **API**
2. Copy these values to your `.env` file:

```
# Project URL (under "Project URL")
SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co

# Anon/Public key (under "Project API keys")
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6...

# Service Role key (under "Project API keys" - click "Reveal")
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6...
```

> **WARNING**: Never expose `SUPABASE_SERVICE_KEY` in client-side code or version control!

---

## Step 3: Get JWT Secret

1. Go to **Settings** → **API**
2. Scroll down to **JWT Settings**
3. Copy the **JWT Secret** to your `.env`:

```
SUPABASE_JWT_SECRET=your-super-secret-jwt-key-here
```

---

## Step 4: Enable Email/Password Authentication

1. Go to **Authentication** (left sidebar)
2. Click **Providers**
3. Find **Email** and ensure it's **enabled**
4. Configure settings:
   - **Enable email confirmations**: Toggle based on preference
     - OFF for development (easier testing)
     - ON for production (more secure)
   - **Secure email change**: ON
   - **Secure password change**: ON
5. Click **Save**

---

## Step 5: Configure Auth Settings

1. Go to **Authentication** → **Settings** (or URL Configuration)
2. Set **Site URL**: `http://localhost:8000` (for development)
3. Add to **Redirect URLs**:
   ```
   http://localhost:8000/**
   http://localhost:3000/**
   ```
4. Configure **JWT expiry**:
   - Default is 3600 seconds (1 hour)
   - Recommended: 86400 (24 hours) for better UX

---

## Step 6: Verify Your .env File

Your complete `.env` should look like:

```env
# Debug mode
DEBUG=true

# Supabase Configuration
SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_JWT_SECRET=your-super-secret-jwt-key-here
```

---

## Step 7: Create a Test User

### Option A: Via Supabase Dashboard
1. Go to **Authentication** → **Users**
2. Click **Add user** → **Create new user**
3. Enter email and password
4. Click **Create user**

### Option B: Via API (using curl)
```bash
curl -X POST "https://YOUR_PROJECT_URL.supabase.co/auth/v1/signup" \
  -H "apikey: YOUR_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "your-secure-password"
  }'
```

---

## Step 8: Get an Access Token (for testing)

### Option A: Via API
```bash
curl -X POST "https://YOUR_PROJECT_URL.supabase.co/auth/v1/token?grant_type=password" \
  -H "apikey: YOUR_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "your-secure-password"
  }'
```

Response will include:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "..."
}
```

### Option B: Via Supabase Dashboard (Quick Test)
1. Go to **SQL Editor**
2. Run:
```sql
SELECT * FROM auth.users;
```
This confirms your user exists.

---

## Step 9: Test Your FastAPI Integration

1. Start the server:
```bash
uv run uvicorn main:app --reload
```

2. Test health endpoint:
```bash
curl http://localhost:8000/api/health
```
Expected: `{"status": "healthy"}`

3. Test protected endpoint WITHOUT token:
```bash
curl http://localhost:8000/api/auth/me
```
Expected: `{"detail": "Not authenticated"}` (403 error)

4. Test protected endpoint WITH token:
```bash
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN_HERE"
```
Expected:
```json
{
  "id": "user-uuid-here",
  "email": "test@example.com",
  "role": "authenticated"
}
```

---

## Step 10: Test via Swagger UI

1. Open browser: `http://localhost:8000/api/docs`
2. Click **Authorize** button (top right)
3. Enter: `Bearer YOUR_ACCESS_TOKEN_HERE`
4. Click **Authorize**
5. Try the `/api/auth/me` endpoint

---

## Troubleshooting

### "Invalid or expired token" error
- Token may have expired (default 1 hour)
- Get a new token using Step 8
- Check `SUPABASE_JWT_SECRET` matches exactly

### "Not authenticated" error
- Ensure header format is: `Authorization: Bearer <token>`
- Check token doesn't have extra spaces

### "Invalid token payload" error
- Token is malformed
- Regenerate token using Step 8

### JWT decode errors in logs
- Verify `SUPABASE_JWT_SECRET` is correct
- Go to Settings → API → JWT Settings and re-copy the secret

### User signup not working
- Check if email confirmations are enabled
- If enabled, check spam folder for confirmation email
- For testing, disable email confirmations

---

## Security Checklist

Before moving to Milestone 2, verify:

- [ ] `.env` file exists with all keys
- [ ] `.env` is in `.gitignore`
- [ ] Test user created successfully
- [ ] Can obtain access token via login
- [ ] `/api/health` returns healthy
- [ ] `/api/auth/me` rejects requests without token
- [ ] `/api/auth/me` returns user data with valid token
- [ ] `SUPABASE_SERVICE_KEY` is NOT in any client-side code

---

## Quick Reference: API Endpoints

| Supabase Auth Endpoint | Method | Purpose |
|------------------------|--------|---------|
| `/auth/v1/signup` | POST | Create new user |
| `/auth/v1/token?grant_type=password` | POST | Login (get token) |
| `/auth/v1/logout` | POST | Logout |
| `/auth/v1/user` | GET | Get current user |
| `/auth/v1/recover` | POST | Password reset email |

---

## Next Steps

Once all checklist items pass, you're ready for **Milestone 2: Product Management**.

This will involve:
1. Creating database tables (products, competitors)
2. Setting up Row Level Security (RLS) policies
3. Building CRUD API endpoints
