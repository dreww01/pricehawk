"""
HTML page routes for the frontend.
These routes return rendered templates, not JSON.
"""

import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.flash import (
    flash,
    pop_flashes,
    clear_flash_cookie,
    FlashMessage,
)
from app.core.security import (
    verify_token_string,
    CurrentUser,
    get_current_user,
    extract_token,
    get_unified_user_and_token,
    get_safe_redirect_url,
    set_access_token_cookie,
    delete_access_token_cookie,
    get_delete_cookie_header,
)
from app.db.database import get_supabase_client
from app.db.models import (
    DashboardActivityResponse,
    DashboardCacheMetricsResponse,
    DashboardInsightsResponse,
    DashboardProductsResponse,
    DashboardStatsResponse,
    ErrorEnvelope,
)
from app.middleware.rate_limit import limiter, AUTH_RATE_LIMIT
from app.services.dashboard_cache import get_dashboard_cache


router = APIRouter(tags=["pages"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

# Template configuration
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    access_token: Optional[str] = Cookie(None),
) -> Optional[CurrentUser]:
    """Get current user from Bearer token or cookie, returns None if not authenticated."""
    token = extract_token(request, credentials)
    if not token:
        return None
    try:
        user = await verify_token_string(token)
        return user
    except Exception:
        return None


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    access_token: Optional[str] = Cookie(None),
) -> CurrentUser:
    """
    Require authentication via session cookie or authorization token.
    Redirects unauthenticated or expired users to /login with a next query parameter.
    """
    # Check if get_current_user has an active dependency override (e.g. in test fixtures)
    if request and hasattr(request, "app") and get_current_user in request.app.dependency_overrides:
        override = request.app.dependency_overrides[get_current_user]
        return override() if callable(override) else override

    raw_destination = request.url.path
    if request.url.query:
        raw_destination = f"{request.url.path}?{request.url.query}"
    destination = get_safe_redirect_url(raw_destination, default="/dashboard")

    token = extract_token(request, credentials)
    if not token:
        flash(request, "Please log in to access this page.", "info")
        redirect_url = f"/login?next={quote(destination)}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": redirect_url},
        )

    try:
        user = await verify_token_string(token)
        if hasattr(request, "state"):
            request.state.auth_token = token
        return user
    except Exception as e:
        err_msg = str(e).lower()
        is_expired = "expired" in err_msg
        notice = "session_expired" if is_expired else "session_expired"
        redirect_url = f"/login?next={quote(destination)}&notice={notice}"
        flash(request, "Your session has expired. Please log in again.", "warning")
        headers = {
            "Location": redirect_url,
            "Set-Cookie": get_delete_cookie_header(),
        }
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers=headers,
        )


def template_response(
    request: Request,
    template_name: str,
    context: dict = None,
    user: Optional[CurrentUser] = None
) -> HTMLResponse:
    """Helper to render templates with common context."""
    flash_messages: list[FlashMessage] = []

    # 1. Retrieve any cryptographically signed flash messages from cookies
    cookie_flashes = pop_flashes(request)
    for cf in cookie_flashes:
        flash_messages.append(FlashMessage(text=cf.get("text", ""), category=cf.get("type", "info")))

    # 2. Support URL query param notices with deduplication against signed flashes
    notice = request.query_params.get("notice")
    message = request.query_params.get("message")
    raw_next = (context.get("next") if context and "next" in context else request.query_params.get("next"))
    safe_next = get_safe_redirect_url(raw_next, default=None)

    if notice == "session_expired" or request.query_params.get("expired"):
        exp_text = "Your session has expired. Please log in again."
        if not any(m.text == exp_text for m in flash_messages):
            flash_messages.append(FlashMessage(text=exp_text, category="warning"))
    elif notice == "login_required" or (safe_next and not notice and template_name == "auth/login.html"):
        req_text = "Please log in to access this page."
        if not any(m.text == req_text for m in flash_messages):
            flash_messages.append(FlashMessage(text=req_text, category="info"))
    elif message:
        if not any(m.text == message for m in flash_messages):
            flash_messages.append(FlashMessage(text=message, category="info"))

    # 3. Incorporate any explicit flash messages passed in context
    if context and "flash_messages" in context:
        for m in context["flash_messages"]:
            if isinstance(m, dict):
                fm = FlashMessage(text=m.get("text", m.get("message", "")), category=m.get("type", m.get("category", "info")))
            else:
                fm = m
            if not any(existing.text == fm.text for existing in flash_messages):
                flash_messages.append(fm)

    ctx = {
        "request": request,
        "user": user,
        "flash_messages": flash_messages,
        "next": safe_next,
    }
    if context:
        ctx.update(context)
    ctx["flash_messages"] = flash_messages

    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=ctx,
    )

    # Clear the flash cookie on page render so it displays exactly once
    clear_flash_cookie(response)

    auth_token = getattr(getattr(request, "state", None), "auth_token", None)
    if auth_token and not request.cookies.get("access_token"):
        set_access_token_cookie(response, auth_token)
    return response


# ============================================================================
# Public Pages
# ============================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Login page."""
    next_url = request.query_params.get("next")
    safe_next = get_safe_redirect_url(next_url, default=None)
    if user:
        return RedirectResponse(url=safe_next or "/dashboard", status_code=303)
    return template_response(request, "auth/login.html", context={"next": safe_next})


@router.post("/login")
@limiter.limit(AUTH_RATE_LIMIT)
async def login_post(
    request: Request,
):
    """
    Handle web form login submissions.
    On success, sets access_token cookie and redirects to next destination.
    """
    next_url = request.query_params.get("next")
    content_type = request.headers.get("content-type", "")
    email = ""
    password = ""
    form_next = None

    if "application/json" in content_type:
        try:
            body = await request.json()
            email = body.get("email", "")
            password = body.get("password", "")
            form_next = body.get("next")
        except Exception:
            pass
    else:
        try:
            form_data = await request.form()
            email = form_data.get("email", "")
            password = form_data.get("password", "")
            form_next = form_data.get("next")
        except Exception:
            pass

    safe_next = get_safe_redirect_url(form_next or next_url, default="/dashboard")

    if not email or not password:
        flash_messages = [{"type": "error", "text": "Email and password are required"}]
        return template_response(
            request,
            "auth/login.html",
            context={"next": safe_next, "flash_messages": flash_messages},
        )

    client = get_supabase_client()
    try:
        auth_resp = client.auth.sign_in_with_password({
            "email": str(email),
            "password": str(password),
        })

        if not auth_resp.session:
            flash_messages = [{"type": "error", "text": "Invalid email or password"}]
            return template_response(
                request,
                "auth/login.html",
                context={"next": safe_next, "flash_messages": flash_messages},
            )

        target = safe_next or "/dashboard"
        response = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
        set_access_token_cookie(response, auth_resp.session.access_token)
        flash(response, "Welcome back! Successfully logged in.", "success")
        return response

    except Exception as e:
        logger.exception(f"Web login error for {email}: {e}")
        flash_messages = [{"type": "error", "text": "Invalid email or password"}]
        return template_response(
            request,
            "auth/login.html",
            context={"next": safe_next, "flash_messages": flash_messages},
        )


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Signup page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return template_response(request, "auth/signup.html")


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Forgot password page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return template_response(request, "auth/forgot_password.html")


@router.get("/verify-reset-code", response_class=HTMLResponse)
async def verify_reset_code_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Verify OTP code page - Step 2 of password reset."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return template_response(request, "auth/verify_reset_code.html")


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Reset password page - Step 3 of password reset (requires token)."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return template_response(request, "auth/reset_password.html")


# ============================================================================
# Protected Pages
# ============================================================================

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """Main dashboard page."""
    return template_response(request, "dashboard/index.html", user=user)


@router.get("/tracked", response_class=HTMLResponse)
async def tracked_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """Tracked products list page."""
    return template_response(request, "products/list.html", user=user)


@router.get("/tracked/{product_id}", response_class=HTMLResponse)
async def tracked_detail_page(
    request: Request,
    product_id: str,
    user: CurrentUser = Depends(require_auth)
):
    """Tracked product detail page."""
    return template_response(
        request,
        "products/detail.html",
        context={"product_id": product_id},
        user=user
    )


@router.get("/discover", response_class=HTMLResponse)
async def discover_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """Store discovery page."""
    return template_response(request, "discovery/index.html", user=user)


@router.get("/insights", response_class=HTMLResponse)
async def insights_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """AI insights page."""
    return template_response(request, "insights/index.html", user=user)


@router.get("/alerts/settings", response_class=HTMLResponse)
async def alerts_settings_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """Alert settings page."""
    return template_response(request, "alerts/settings.html", user=user)


@router.get("/account/settings", response_class=HTMLResponse)
async def account_settings_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """Account settings page."""
    return template_response(request, "account/settings.html", user=user)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: CurrentUser = Depends(require_auth)
):
    """General settings route redirecting to account settings."""
    return RedirectResponse(url="/account/settings", status_code=303)


@router.get("/logout")
async def logout():
    """Logout and clear session cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    delete_access_token_cookie(response)
    flash(response, "You have been logged out successfully.", "info")
    return response


@router.get("/api/dismiss-flash", response_class=HTMLResponse)
@router.post("/api/dismiss-flash", response_class=HTMLResponse)
async def dismiss_flash(request: Request):
    """HTMX endpoint to dismiss a flash message and clear any remaining flash cookie."""
    response = HTMLResponse(content="", status_code=status.HTTP_200_OK)
    clear_flash_cookie(response)
    return response


# ============================================================================
# Dashboard API Endpoints
# ============================================================================

@router.get(
    "/api/dashboard/stats",
    response_model=DashboardStatsResponse,
    summary="Get dashboard statistics",
    description="Get aggregated dashboard statistics including product, competitor, alert, and insight counts.",
    responses={
        200: {"model": DashboardStatsResponse, "description": "Aggregated dashboard statistics"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_dashboard_stats(
    auth: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
):
    """
    Get aggregated dashboard statistics.

    Returns counts for products, competitors, pending alerts, and recent activity.
    Supports either Authorization header or session cookie.
    Cached for high-frequency views with short, configurable expiration.
    """
    current_user, token = auth
    cache = get_dashboard_cache()
    cached_stats = cache.get_stats_data(current_user.id)
    if cached_stats is not None:
        return JSONResponse(
            cached_stats,
            headers={
                "X-Cache": "HIT",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    client = get_supabase_client(token)

    # Get products count
    products_result = (
        client.table("products")
        .select("id", count="exact")
        .eq("user_id", current_user.id)
        .execute()
    )
    products_count = products_result.count or 0

    # Get competitors count
    competitors_result = (
        client.table("competitors")
        .select("id, product_id, products!inner(user_id)")
        .eq("products.user_id", current_user.id)
        .execute()
    )
    competitors_count = len(competitors_result.data) if competitors_result.data else 0

    # Get pending alerts count (this week)
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    alerts_result = (
        client.table("pending_alerts")
        .select("id", count="exact")
        .eq("user_id", current_user.id)
        .gte("detected_at", week_ago)
        .execute()
    )
    alerts_count = alerts_result.count or 0

    # Get insights count
    insights_result = (
        client.table("insights")
        .select("id, product_id, products!inner(user_id)", count="exact")
        .eq("products.user_id", current_user.id)
        .execute()
    )
    insights_count = insights_result.count or 0

    stats = {
        "products": products_count,
        "competitors": competitors_count,
        "alerts": alerts_count,
        "insights": insights_count,
    }
    cache.set_stats_data(current_user.id, stats)

    return JSONResponse(
        stats,
        headers={
            "X-Cache": "MISS",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get(
    "/api/dashboard/activity",
    response_model=DashboardActivityResponse,
    summary="Get recent dashboard activity",
    description="Get recent price change activity feed for the dashboard view.",
    responses={
        200: {"model": DashboardActivityResponse, "description": "Recent activity feed"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_dashboard_activity(
    auth: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
):
    """
    Get recent price change activity for dashboard.

    Returns the last 10 significant price changes.
    Supports either Authorization header or session cookie.
    Cached for high-frequency views with short, configurable expiration.
    """
    current_user, token = auth
    cache = get_dashboard_cache()
    cached_activity = cache.get_activity_data(current_user.id)
    if cached_activity is not None:
        return JSONResponse(
            {"activity": cached_activity},
            headers={
                "X-Cache": "HIT",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    client = get_supabase_client(token)

    # Get recent pending alerts as activity
    activity_result = (
        client.table("pending_alerts")
        .select(
            "id, alert_type, old_price, new_price, price_change_percent, detected_at, "
            "products(id, product_name), competitors(retailer_name, url)"
        )
        .eq("user_id", current_user.id)
        .order("detected_at", desc=True)
        .limit(10)
        .execute()
    )

    activity = []
    for row in activity_result.data or []:
        activity.append({
            "id": row["id"],
            "type": row["alert_type"],
            "product_id": row["products"]["id"] if row.get("products") else None,
            "product_name": row["products"]["product_name"] if row.get("products") else "Unknown",
            "retailer": row["competitors"]["retailer_name"] if row.get("competitors") else "Unknown",
            "old_price": float(row["old_price"]) if row["old_price"] else None,
            "new_price": float(row["new_price"]) if row["new_price"] else None,
            "change_percent": float(row["price_change_percent"]) if row["price_change_percent"] else None,
            "detected_at": row["detected_at"]
        })

    cache.set_activity_data(current_user.id, activity)

    return JSONResponse(
        {"activity": activity},
        headers={
            "X-Cache": "MISS",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get(
    "/api/dashboard/products",
    response_model=DashboardProductsResponse,
    summary="Get recent dashboard products",
    description="Get the most recent products with competitor counts for dashboard display.",
    responses={
        200: {"model": DashboardProductsResponse, "description": "Recent products list"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_dashboard_products(
    auth: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
):
    """
    Get recent products for dashboard display.

    Returns the 5 most recently created products with competitor counts.
    Supports either Authorization header or session cookie.
    Cached for high-frequency views with short, configurable expiration.
    """
    current_user, token = auth
    cache = get_dashboard_cache()
    cached_products = cache.get_products_data(current_user.id)
    if cached_products is not None:
        return JSONResponse(
            {"products": cached_products},
            headers={
                "X-Cache": "HIT",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    client = get_supabase_client(token)

    # Get recent products with competitor count
    products_result = (
        client.table("products")
        .select("id, product_name, is_active, created_at")
        .eq("user_id", current_user.id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    products = []
    for p in products_result.data or []:
        # Get competitor count for each product
        comp_result = (
            client.table("competitors")
            .select("id", count="exact")
            .eq("product_id", p["id"])
            .execute()
        )

        products.append({
            "id": p["id"],
            "product_name": p["product_name"],
            "is_active": p["is_active"],
            "competitor_count": comp_result.count or 0
        })

    cache.set_products_data(current_user.id, products)

    return JSONResponse(
        {"products": products},
        headers={
            "X-Cache": "MISS",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.get(
    "/api/dashboard/cache/metrics",
    response_model=DashboardCacheMetricsResponse,
    summary="Get dashboard cache metrics",
    description="Get dashboard cache performance metrics including hit rates, hits, misses, and invalidations.",
    responses={
        200: {"model": DashboardCacheMetricsResponse, "description": "Cache diagnostics metrics"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_dashboard_cache_metrics(
    auth: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
):
    """
    Get dashboard cache metrics including hit rates, hits, misses, and invalidations.
    """
    cache = get_dashboard_cache()
    return JSONResponse(cache.get_metrics())


@router.get(
    "/api/insights",
    response_model=DashboardInsightsResponse,
    summary="Get all user insights",
    description="Get all AI insights for the current user across all products.",
    responses={
        200: {"model": DashboardInsightsResponse, "description": "User AI insights list"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_all_insights(
    auth: tuple[CurrentUser, str] = Depends(get_unified_user_and_token),
):
    """
    Get all AI insights for the current user across all products.

    Returns insights sorted by generated_at descending.
    Supports either Authorization header or session cookie.
    """
    current_user, token = auth
    client = get_supabase_client(token)

    # Get all insights for user's products with product info
    insights_result = (
        client.table("insights")
        .select("id, product_id, insight_text, insight_type, confidence_score, generated_at, products!inner(user_id, product_name)")
        .eq("products.user_id", current_user.id)
        .order("generated_at", desc=True)
        .limit(50)
        .execute()
    )

    insights = []
    for row in insights_result.data or []:
        insights.append({
            "id": row["id"],
            "product_id": row["product_id"],
            "product_name": row["products"]["product_name"] if row.get("products") else "Unknown",
            "insight_text": row["insight_text"],
            "insight_type": row["insight_type"],
            "generated_at": row["generated_at"]
        })

    return JSONResponse({"insights": insights, "total": len(insights)})
