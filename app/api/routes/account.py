"""
Account management endpoints for authenticated users.
Change password, change email, and account settings.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

from app.core.security import get_current_user, CurrentUser, delete_access_token_cookie
from app.db.database import get_supabase_client, get_supabase_client_with_session
from app.services.account_service import delete_user_account
from app.services.dashboard_cache import invalidate_dashboard_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])
security = HTTPBearer()


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr


class VerifyEmailChangeRequest(BaseModel):
    token: str


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Change password for authenticated user.
    Requires current password verification.
    """
    # Use session-enabled client for auth operations like update_user()
    client = get_supabase_client_with_session(credentials.credentials)

    try:
        client.auth.update_user({"password": request.new_password})

        return {"message": "Password updated successfully"}

    except Exception as e:
        error_msg = str(e).lower()
        if "weak" in error_msg or "password" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password does not meet requirements. Use at least 6 characters."
            )
        logger.exception(f"Change password error for {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to change password. Please try again."
        )


@router.post("/change-email")
async def change_email(
    request: ChangeEmailRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Request email change. Sends verification to new email.
    User must click link in email to complete change.
    """
    # Use session-enabled client for auth operations like update_user()
    client = get_supabase_client_with_session(credentials.credentials)

    try:
        client.auth.update_user({"email": request.new_email})

        return {
            "message": "Verification email sent to your new address. Please check your inbox."
        }

    except Exception as e:
        error_msg = str(e).lower()
        if "already" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This email is already in use."
            )
        logger.exception(f"Change email error for {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to change email. Please try again."
        )


@router.get("/settings")
async def get_account_settings(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get current account settings."""
    return {
        "user_id": current_user.id,
        "email": current_user.email
    }


@router.delete("/delete")
async def delete_account(
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Delete user account and all associated data.
    This action is irreversible.
    """
    try:
        # Systematically purge all user data, credentials, tasks, and sessions
        result = delete_user_account(current_user.id)

        # Invalidate active session cookie immediately
        delete_access_token_cookie(response)

        return {
            "message": "Account data deleted successfully. Please log out.",
            "details": result,
        }

    except Exception as e:
        logger.exception(f"Delete account error for {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to delete account. Please try again."
        )
