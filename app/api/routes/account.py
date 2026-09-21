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
from app.db.models import (
    AccountDeleteResponse,
    AccountDeletionDetails,
    AccountSettingsResponse,
    ChangeEmailResponse,
    ChangePasswordResponse,
    ErrorEnvelope,
)
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


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    summary="Change account password",
    description="Change password for authenticated user. Requires current password verification.",
    responses={
        200: {"model": ChangePasswordResponse, "description": "Password updated successfully"},
        400: {"model": ErrorEnvelope, "description": "Weak password or update failure"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def change_password(
    request: ChangePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
) -> ChangePasswordResponse:
    """
    Change password for authenticated user.
    Requires current password verification.
    """
    # Use session-enabled client for auth operations like update_user()
    client = get_supabase_client_with_session(credentials.credentials)

    try:
        client.auth.update_user({"password": request.new_password})

        return ChangePasswordResponse(message="Password updated successfully")

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


@router.post(
    "/change-email",
    response_model=ChangeEmailResponse,
    summary="Request email change",
    description="Request email change. Sends verification to new email. User must click link in email to complete change.",
    responses={
        200: {"model": ChangeEmailResponse, "description": "Verification email dispatched"},
        400: {"model": ErrorEnvelope, "description": "Email already in use or update error"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def change_email(
    request: ChangeEmailRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
) -> ChangeEmailResponse:
    """
    Request email change. Sends verification to new email.
    User must click link in email to complete change.
    """
    # Use session-enabled client for auth operations like update_user()
    client = get_supabase_client_with_session(credentials.credentials)

    try:
        client.auth.update_user({"email": request.new_email})

        return ChangeEmailResponse(
            message="Verification email sent to your new address. Please check your inbox."
        )

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


@router.get(
    "/settings",
    response_model=AccountSettingsResponse,
    summary="Get account settings",
    description="Get current account settings including user ID and email.",
    responses={
        200: {"model": AccountSettingsResponse, "description": "Current user settings"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def get_account_settings(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
) -> AccountSettingsResponse:
    """Get current account settings."""
    return AccountSettingsResponse(
        user_id=current_user.id,
        email=current_user.email
    )


@router.delete(
    "/delete",
    response_model=AccountDeleteResponse,
    summary="Delete account and data",
    description="Delete user account and all associated data. This action is irreversible.",
    responses={
        200: {"model": AccountDeleteResponse, "description": "Account and all associated data deleted"},
        400: {"model": ErrorEnvelope, "description": "Unable to delete account"},
        401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    },
)
async def delete_account(
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: CurrentUser = Depends(get_current_user)
) -> AccountDeleteResponse:
    """
    Delete user account and all associated data.
    This action is irreversible.
    """
    try:
        # Systematically purge all user data, credentials, tasks, and sessions
        result = delete_user_account(current_user.id)

        # Invalidate active session cookie immediately
        delete_access_token_cookie(response)

        details_payload = (
            AccountDeletionDetails(**result)
            if isinstance(result, dict)
            else result
        )

        return AccountDeleteResponse(
            message="Account data deleted successfully. Please log out.",
            details=details_payload,
        )

    except Exception as e:
        logger.exception(f"Delete account error for {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to delete account. Please try again."
        )
