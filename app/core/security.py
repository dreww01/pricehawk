from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient
from pydantic import BaseModel

from app.core.config import get_settings, Settings


security = HTTPBearer()


class CurrentUser(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None


@lru_cache
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Get cached JWKS client for Supabase."""
    return PyJWKClient(jwks_url)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Verify Supabase JWT and extract user info."""
    token = credentials.credentials

    try:
        # Get JWKS client for Supabase
        jwks_url = f"{settings.sb_url}/auth/v1/.well-known/jwks.json"
        jwks_client = get_jwks_client(jwks_url)

        # Get signing key from JWKS based on token's kid
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Decode and verify the token
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    return CurrentUser(
        id=user_id,
        email=payload.get("email"),
        role=payload.get("role"),
    )


def get_current_user(user: CurrentUser = Depends(verify_token)) -> CurrentUser:
    """Dependency to get current authenticated user."""
    return user
