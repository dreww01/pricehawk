from supabase import create_client, Client

from app.core.config import get_settings


def get_supabase_client(access_token: str | None = None) -> Client:
    """
    Get Supabase client.

    If access_token is provided, creates an authenticated client that respects RLS.
    Otherwise, uses the service key (bypasses RLS - for background tasks only).
    """
    settings = get_settings()

    if access_token:
        client = create_client(settings.sb_url, settings.sb_anon_key)
        # Set Authorization header with user's JWT for RLS
        client.postgrest.auth(access_token)
        return client

    return create_client(settings.sb_url, settings.sb_service_key)
