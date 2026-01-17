"""
HTML page routes for the frontend.
These routes return rendered templates, not JSON.
"""

from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_token_string, CurrentUser, get_current_user
from app.db.database import get_supabase_client


router = APIRouter(tags=["pages"])
security = HTTPBearer()

# Template configuration
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


async def get_current_user_optional(
    access_token: Optional[str] = Cookie(None)
) -> Optional[CurrentUser]:
    """Get current user from cookie, returns None if not authenticated."""
    if not access_token:
        return None
    try:
        return await verify_token_string(access_token)
    except Exception:
        return None


async def require_auth(
    access_token: Optional[str] = Cookie(None)
) -> CurrentUser:
    """Require authentication, redirect to login if not authenticated."""
    if not access_token:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    try:
        return await verify_token_string(access_token)
    except Exception:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def template_response(
    request: Request,
    template_name: str,
    context: dict = None,
    user: Optional[CurrentUser] = None
) -> HTMLResponse:
    """Helper to render templates with common context."""
    ctx = {
        "request": request,
        "user": user,
        "flash_messages": [],  # TODO: Implement flash message system
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(template_name, ctx)


# ============================================================================
# Public Pages
# ============================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    user: Optional[CurrentUser] = Depends(get_current_user_optional)
):
    """Login page."""
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return template_response(request, "auth/login.html")


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


@router.get("/logout")
async def logout():
    """Logout and clear session cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response


# ============================================================================
# Dashboard API Endpoints
# ============================================================================

@router.get("/api/dashboard/stats")
async def get_dashboard_stats(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get aggregated dashboard statistics.

    Returns counts for products, competitors, pending alerts, and recent activity.
    """
    client = get_supabase_client(credentials.credentials)

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

    return JSONResponse({
        "products": products_count,
        "competitors": competitors_count,
        "alerts": alerts_count,
        "insights": insights_count
    })


@router.get("/api/dashboard/activity")
async def get_dashboard_activity(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get recent price change activity for dashboard.

    Returns the last 10 significant price changes.
    """
    client = get_supabase_client(credentials.credentials)

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

    return JSONResponse({"activity": activity})


@router.get("/api/dashboard/products")
async def get_dashboard_products(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get recent products for dashboard display.

    Returns the 5 most recently created products with competitor counts.
    """
    client = get_supabase_client(credentials.credentials)

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

    return JSONResponse({"products": products})


@router.get("/api/insights")
async def get_all_insights(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get all AI insights for the current user across all products.

    Returns insights sorted by generated_at descending.
    """
    client = get_supabase_client(credentials.credentials)

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
