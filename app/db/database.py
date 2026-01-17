from supabase import create_client, Client
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

# Shared security instance
security = HTTPBearer()


def get_supabase_client(access_token: str | None = None) -> Client:
    """
    Get Supabase client.

    If access_token is provided, creates an authenticated client that respects RLS.
    Otherwise, uses the service key (bypasses RLS - for background tasks only).
    """
    settings = get_settings()

    if access_token:
        client = create_client(settings.sb_url, settings.sb_anon_key)
        # Use postgrest.auth() to properly set the JWT for RLS
        client.postgrest.auth(access_token)
        return client

    return create_client(settings.sb_url, settings.sb_service_key)


def get_supabase_client_with_session(access_token: str) -> Client:
    """
    Get Supabase client with active auth session.

    Use this for auth operations like update_user() that require a session.
    Regular get_supabase_client() only sets postgrest auth for RLS.
    """
    settings = get_settings()
    client = create_client(settings.sb_url, settings.sb_anon_key)
    # Set session establishes auth context for update_user() etc.
    client.auth.set_session(access_token, access_token)
    return client


def get_user_supabase_client(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Client:
    """
    Dependency to get authenticated Supabase client with user's JWT token.

    This ensures consistent token handling across all endpoints.
    Use this instead of manually calling get_supabase_client(credentials.credentials).
    """
    return get_supabase_client(credentials.credentials)
