from functools import lru_cache
import posixpath
from urllib.parse import unquote, urlsplit

import httpx
import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWKClient
from pydantic import BaseModel
from starlette.responses import Response

from typing import NamedTuple

from app.core.config import get_settings, Settings


security = HTTPBearer(auto_error=False)

DISALLOWED_REDIRECT_PREFIXES = (
    "/api",
    "/static",
    "/login",
    "/logout",
    "/signup",
    "/forgot-password",
    "/reset-password",
)


def get_safe_redirect_url(url: str | None, default: str | None = "/dashboard") -> str | None:
    """
    Validate and return a safe local return destination.

    Rejects:
    - Non-string or empty values
    - Control characters and newlines
    - Backslashes (unencoded, encoded, or nested)
    - Scheme-relative and absolute URLs
    - External domains / authority separators
    - Sensitive destinations (API, static assets, auth endpoints)
    """
    if not url or not isinstance(url, str):
        return default

    candidate = url.strip()
    if not candidate:
        return default

    # Reject control characters
    if any(ord(c) < 32 or ord(c) == 127 for c in candidate):
        return default

    # Check for unencoded or encoded backslashes and control characters through unquoting
    check_str = candidate
    for _ in range(3):
        if "\\" in check_str or "%5c" in check_str.lower():
            return default
        if any(ord(c) < 32 or ord(c) == 127 for c in check_str):
            return default
        next_str = unquote(check_str)
        if next_str == check_str:
            break
        check_str = next_str

    if "\\" in check_str or "%5c" in check_str.lower():
        return default

    # Must start with a single slash, not double slash or slash-backslash
    if not candidate.startswith("/") or candidate.startswith("//") or candidate.startswith("/\\"):
        return default
    if not check_str.startswith("/") or check_str.startswith("//") or check_str.startswith("/\\"):
        return default

    try:
        parsed = urlsplit(candidate)
        parsed_unquoted = urlsplit(check_str)
    except Exception:
        return default

    # Reject schemes (http, https, javascript, data, etc.) and netlocs
    if parsed.scheme or parsed.netloc:
        return default
    if parsed_unquoted.scheme or parsed_unquoted.netloc:
        return default

    # Validate path
    path = parsed.path
    if not path.startswith("/") or path.startswith("//"):
        return default

    normalized_path = posixpath.normpath(path)
    path_lower = path.lower()
    norm_path_lower = normalized_path.lower()

    for prefix in DISALLOWED_REDIRECT_PREFIXES:
        if path_lower == prefix or path_lower.startswith(prefix + "/") or path_lower.startswith(prefix + "?"):
            return default
        if norm_path_lower == prefix or norm_path_lower.startswith(prefix + "/"):
            return default

    return candidate


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


SUPPORTED_JWT_ALGORITHMS = {"ES256", "HS256"}


def _decode_jwt(token: str, settings: Settings) -> dict:
    """Decode and verify JWT token using trusted application configuration and explicit paths."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise jwt.InvalidTokenError(f"Invalid token header: {e}") from e

    token_alg = header.get("alg")
    if not token_alg:
        raise jwt.InvalidTokenError("Missing token algorithm")

    # Allowed algorithms must come from trusted application configuration and known supported set
    configured_allowed = set(getattr(settings, "jwt_allowed_algorithms", ["ES256", "HS256"]))
    trusted_allowed = configured_allowed & SUPPORTED_JWT_ALGORITHMS

    if token_alg not in trusted_allowed:
        raise jwt.InvalidTokenError(f"Unsupported algorithm: {token_alg}")

    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except Exception as e:
        raise jwt.InvalidTokenError(f"Invalid token payload: {e}") from e

    decode_options = {}
    if "aud" not in unverified:
        decode_options["verify_aud"] = False

    audience = "authenticated" if "aud" in unverified else None

    # Explicit, separately configured verification paths
    if token_alg == "HS256":
        if not settings.sb_jwt_secret:
            raise jwt.InvalidTokenError("HS256 secret not configured")
        return jwt.decode(
            token,
            settings.sb_jwt_secret,
            algorithms=["HS256"],
            audience=audience,
            options=decode_options,
        )

    if token_alg == "ES256":
        jwks_url = f"{settings.sb_url}/auth/v1/.well-known/jwks.json"
        jwks_client = get_jwks_client(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=audience,
            options=decode_options,
        )

    raise jwt.InvalidTokenError(f"Unsupported algorithm: {token_alg}")


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


SESSION_COOKIE_NAME = "access_token"


def get_session_cookie_options(settings: Settings | None = None) -> dict:
    """
    Return standard cookie attributes for the access_token session cookie.
    Restricts cookie to HTTPS in production; permits HTTP in development and tests.
    Attributes are consistent across all set and delete operations.
    """
    cfg = settings or get_settings()
    return {
        "key": SESSION_COOKIE_NAME,
        "path": "/",
        "samesite": "strict",
        "secure": cfg.is_production,
        "httponly": False,
    }


def set_access_token_cookie(
    response: Response,
    token: str,
    settings: Settings | None = None,
    max_age: int | None = None,
) -> None:
    """Set the session access_token cookie using the unified environment-aware policy."""
    opts = get_session_cookie_options(settings)
    response.set_cookie(
        key=opts["key"],
        value=token,
        path=opts["path"],
        samesite=opts["samesite"],
        secure=opts["secure"],
        httponly=opts["httponly"],
        max_age=max_age,
    )


def delete_access_token_cookie(
    response: Response,
    settings: Settings | None = None,
) -> None:
    """Delete the session access_token cookie using the unified environment-aware policy."""
    opts = get_session_cookie_options(settings)
    response.delete_cookie(
        key=opts["key"],
        path=opts["path"],
        samesite=opts["samesite"],
        secure=opts["secure"],
        httponly=opts["httponly"],
    )


def get_delete_cookie_header(settings: Settings | None = None) -> str:
    """Return the Set-Cookie header string for deleting the session access_token cookie."""
    dummy = Response()
    delete_access_token_cookie(dummy, settings)
    return dummy.headers.get("set-cookie", "")


