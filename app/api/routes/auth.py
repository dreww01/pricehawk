"""
Authentication endpoints for login and signup.
Uses Supabase Auth for user management.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from pydantic import BaseModel, EmailStr

from app.core.config import get_settings
from app.core.security import get_current_user, CurrentUser, set_access_token_cookie
from app.db.database import get_supabase_client
from app.db.models import (
    ErrorEnvelope,
    ForgotPasswordResponse,
    ResetPasswordResponse,
    SignupResponse,
    VerifyResetOTPResponse,
)
from app.middleware.rate_limit import limiter, AUTH_RATE_LIMIT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="User login",
    description="Login with email and password to receive an access token.",
    responses={
        200: {"model": AuthResponse, "description": "Successful login"},
        400: {"model": ErrorEnvelope, "description": "Bad request"},
        401: {"model": ErrorEnvelope, "description": "Invalid email or password"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    },
)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(request: Request, login_data: LoginRequest, http_response: Response):
    """Login with email and password."""
    client = get_supabase_client()

    try:
        response = client.auth.sign_in_with_password({
            "email": login_data.email,
            "password": login_data.password
        })

        if not response.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        set_access_token_cookie(http_response, response.session.access_token)

        return AuthResponse(
            access_token=response.session.access_token,
            user_id=response.user.id,
            email=response.user.email
        )

    except Exception as e:
        error_msg = str(e)
        if "Invalid login credentials" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        logger.exception(f"Login error for {login_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to sign in. Please try again."
        )


@router.post(
    "/signup",
    response_model=SignupResponse,
    summary="User registration",
    description="Create a new user account with email and password.",
    responses={
        200: {"model": SignupResponse, "description": "Account created successfully"},
        400: {"model": ErrorEnvelope, "description": "Unable to create account or email already registered"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    },
)
@limiter.limit(AUTH_RATE_LIMIT)
async def signup(request: Request, signup_data: SignupRequest) -> SignupResponse:
    """Create a new account."""
    client = get_supabase_client()

    try:
        response = client.auth.sign_up({
            "email": signup_data.email,
            "password": signup_data.password
        })

        if response.user:
            return SignupResponse(
                message="Account created successfully",
                user_id=response.user.id,
                email=response.user.email,
                email_confirmed=response.user.email_confirmed_at is not None
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create account"
        )

    except Exception as e:
        error_msg = str(e)
        if "already registered" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        logger.exception(f"Signup error for {signup_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to create account. Please try again."
        )


@router.get("/me", response_model=CurrentUser)
def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Get current authenticated user info."""
    return current_user


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetOTPRequest(BaseModel):
    email: EmailStr
    otp: str


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request password reset",
    description="Send password reset OTP code to email. User will receive a 6-digit code to verify identity.",
    responses={
        200: {"model": ForgotPasswordResponse, "description": "Reset instructions sent if account exists"},
        422: {"model": ErrorEnvelope, "description": "Invalid email format"},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    },
)
@limiter.limit(AUTH_RATE_LIMIT)
async def forgot_password(request: Request, forgot_data: ForgotPasswordRequest) -> ForgotPasswordResponse:
    """
    Send password reset OTP code to email.
    User will receive a 6-digit code to verify identity.
    """
    import re
    client = get_supabase_client()

    try:
        client.auth.reset_password_email(forgot_data.email)

        return ForgotPasswordResponse(
            message="If an account exists with this email, a reset code has been sent."
        )

    except Exception as e:
        error_msg = str(e)
        # Check for rate limit error and extract seconds
        rate_limit_match = re.search(r"after (\d+) seconds", error_msg)
        if rate_limit_match:
            seconds = int(rate_limit_match.group(1))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {seconds} seconds before requesting another reset code.",
                headers={"X-Retry-After": str(seconds)}
            )

        logger.exception(f"Forgot password error for {forgot_data.email}: {e}")
        return ForgotPasswordResponse(
            message="If an account exists with this email, a reset code has been sent."
        )


@router.post(
    "/verify-reset-otp",
    response_model=VerifyResetOTPResponse,
    summary="Verify reset OTP code",
    description="Verify OTP code and return a temporary reset token. Step 2 of password reset flow.",
    responses={
        200: {"model": VerifyResetOTPResponse, "description": "OTP verified successfully"},
        400: {"model": ErrorEnvelope, "description": "Invalid or expired OTP code"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    },
)
@limiter.limit(AUTH_RATE_LIMIT)
async def verify_reset_otp(request: Request, reset_data: VerifyResetOTPRequest) -> VerifyResetOTPResponse:
    """
    Verify OTP code and return a temporary reset token.
    Step 2 of password reset flow - validates identity before allowing password change.
    """
    client = get_supabase_client()

    try:
        response = client.auth.verify_otp({
            "email": reset_data.email,
            "token": reset_data.otp,
            "type": "recovery"
        })

        if not response.session:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code"
            )

        # Return the access token as reset_token for the next step
        return VerifyResetOTPResponse(
            message="Code verified successfully",
            reset_token=response.session.access_token
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "expired" in error_msg or "invalid" in error_msg or "otp" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code. Please request a new one."
            )
        logger.exception(f"Verify OTP error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to verify code. Please try again."
        )


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Reset password with token",
    description="Reset password using the token from OTP verification. Step 3 of password reset flow.",
    responses={
        200: {"model": ResetPasswordResponse, "description": "Password reset completed successfully"},
        400: {"model": ErrorEnvelope, "description": "Password requirements not met or token expired"},
        422: {"model": ErrorEnvelope, "description": "Validation error"},
        429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    },
)
@limiter.limit(AUTH_RATE_LIMIT)
async def reset_password(request: Request, reset_data: ResetPasswordRequest) -> ResetPasswordResponse:
    """
    Reset password using the token from OTP verification.
    Step 3 of password reset flow - only accessible after OTP verified.
    """
    from app.db.database import get_supabase_client_with_session

    try:
        # Use the reset token to establish session and update password
        client = get_supabase_client_with_session(reset_data.reset_token)
        client.auth.update_user({"password": reset_data.new_password})

        return ResetPasswordResponse(message="Password has been reset successfully. You can now log in.")

    except Exception as e:
        error_msg = str(e).lower()
        if "weak" in error_msg or "password" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password does not meet requirements. Use at least 6 characters."
            )
        if "session" in error_msg or "token" in error_msg or "expired" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset session expired. Please start the password reset process again."
            )
        logger.exception(f"Reset password error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to reset password. Please try again."
        )
