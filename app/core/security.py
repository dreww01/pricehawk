from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient
from pydantic import BaseModel

from typing import NamedTuple

from app.core.config import get_settings, Settings


security = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None


class AuthContext(NamedTuple):
    """Internal authentication context containing authenticated user and raw token."""
    user: CurrentUser
    token: str


@lru_cache
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Get cached JWKS client for Supabase."""
    return PyJWKClient(jwks_url)


def extract_token(
    request: Request | None = None,
    credentials: HTTPAuthorizationCredentials | None = None,
) -> str | None:
    """
    Extract authentication token from HTTP credentials, Authorization header, or cookie.

    Supports:
    - credentials from FastAPI HTTPBearer
    - Authorization: Bearer <token> header
    - access_token cookie
    """
    if credentials and credentials.credentials:
        val = credentials.credentials.strip()
        if val and val.lower() not in ("undefined", "null", "none"):
            return val

    if request is not None:
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                val = parts[1].strip()
                if val and val.lower() not in ("undefined", "null", "none"):
                    return val

        cookie_token = request.cookies.get("access_token")
        if cookie_token:
            val = cookie_token.strip()
            if val and val.lower() not in ("undefined", "null", "none"):
                return val

    return None


def _decode_jwt(token: str, settings: Settings) -> dict:
    """Decode and verify JWT token supporting both HS256 secret and ES256 JWKS."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise jwt.InvalidTokenError(f"Invalid token header: {e}") from e

    alg = header.get("alg", "ES256")
    unverified = jwt.decode(token, options={"verify_signature": False})
    decode_kwargs: dict = {"algorithms": [alg]}
    if "aud" in unverified:
        decode_kwargs["audience"] = "authenticated"
    else:
        decode_kwargs["options"] = {"verify_aud": False}

    if alg == "HS256":
        return jwt.decode(token, settings.sb_jwt_secret, **decode_kwargs)

    jwks_url = f"{settings.sb_url}/auth/v1/.well-known/jwks.json"
    jwks_client = get_jwks_client(jwks_url)
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    return jwt.decode(token, signing_key.key, **decode_kwargs)


def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Verify Supabase JWT from Bearer token and extract user info."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    if not token or token.lower() in ("undefined", "null", "none"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _decode_jwt(token, settings)
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
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


async def verify_token_string(token: str) -> CurrentUser:
    """Verify a JWT token string directly (for cookie-based auth)."""
    settings = get_settings()

    try:
        payload = _decode_jwt(token, settings)
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except (jwt.InvalidTokenError, Exception) as e:
        raise ValueError(f"Invalid token: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise ValueError("Invalid token payload")

    return CurrentUser(
        id=user_id,
        email=payload.get("email"),
        role=payload.get("role"),
    )


async def get_unified_user_and_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthContext:
    """
    Unified authentication dependency for endpoints accepting either Bearer token or session cookie.
    Returns AuthContext(user, token).
    Raises HTTP 401 if unauthenticated or expired.
    """
    if request and hasattr(request, "app"):
        if get_current_user in request.app.dependency_overrides:
            override = request.app.dependency_overrides[get_current_user]
            user = override() if callable(override) else override
            token = getattr(user, "token", None) or extract_token(request, credentials) or "mock-token"
            return AuthContext(user=user, token=token)
        if verify_token in request.app.dependency_overrides:
            override = request.app.dependency_overrides[verify_token]
            user = override() if callable(override) else override
            token = getattr(user, "token", None) or extract_token(request, credentials) or "mock-token"
            return AuthContext(user=user, token=token)

    token = extract_token(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = await verify_token_string(token)
        return AuthContext(user=user, token=token)
    except ValueError as e:
        err_msg = str(e)
        if "expired" in err_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_unified_user(
    auth_data: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
) -> CurrentUser:
    """Dependency returning CurrentUser from unified authentication."""
    return auth_data[0]
