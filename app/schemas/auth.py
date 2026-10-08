"""Authentication, token, account management, and user profile schemas."""

from typing import Any

from pydantic import AliasChoices, BaseModel, EmailStr, Field


class AccountSettingsResponse(BaseModel):
    """Current authenticated user account profile and settings."""

    user_id: str = Field(..., description="Unique user identifier", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    email: str = Field(..., description="Registered user email address", examples=["user@example.com"])


class ChangePasswordResponse(BaseModel):
    """Outcome of password update operation."""

    message: str = Field(default="Password updated successfully", description="Status message", examples=["Password updated successfully"])


class ChangeEmailResponse(BaseModel):
    """Outcome of email change request."""

    message: str = Field(
        default="Verification email sent to your new address. Please check your inbox.",
        description="Status message",
        examples=["Verification email sent to your new address. Please check your inbox."],
    )


class AccountDeletionDetails(BaseModel):
    """Detailed audit metrics of resources purged during account deletion."""

    model_config = {"extra": "allow"}

    user_id: str = Field(..., description="Target user identifier purged", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    products_found: int = Field(default=0, description="Total products found and removed", examples=[3])
    competitors_found: int = Field(default=0, description="Total competitor entries found and removed", examples=[5])
    tasks_revoked: int = Field(default=0, description="Total background Celery tasks revoked", examples=[2])
    tables_cleaned: list[str] = Field(
        default_factory=list,
        description="Database tables purged during cascade",
        examples=[["price_history", "competitors", "insights", "tracking_jobs", "pending_alerts", "alert_history", "user_alert_settings", "products"]],
    )


class AccountDeleteResponse(BaseModel):
    """Outcome contract for permanent account and data deletion."""

    message: str = Field(
        default="Account data deleted successfully. Please log out.",
        description="Status message",
        examples=["Account data deleted successfully. Please log out."],
    )
    details: AccountDeletionDetails | dict[str, Any] = Field(
        ...,
        description="Resource cleanup summary metrics",
    )


class SignupResponse(BaseModel):
    """Outcome of user account registration."""

    message: str = Field(default="Account created successfully", description="Outcome message", examples=["Account created successfully"])
    user_id: str = Field(..., description="Unique ID assigned to created user", examples=["usr_12345678-abcd-ef01-2345-6789abcdef01"])
    email: str = Field(..., description="Email address of registered user", examples=["user@example.com"])
    email_confirmed: bool = Field(default=False, description="Whether email confirmation is already verified", examples=[False])


class ForgotPasswordResponse(BaseModel):
    """Confirmation for password reset initiation."""

    message: str = Field(
        default="If an account exists with this email, a reset code has been sent.",
        description="Status message",
        examples=["If an account exists with this email, a reset code has been sent."],
    )


class VerifyResetOTPResponse(BaseModel):
    """Outcome of password reset OTP verification."""

    message: str = Field(default="Code verified successfully", description="Status message", examples=["Code verified successfully"])
    reset_token: str = Field(..., description="Temporary single-use token to complete password reset", examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])


class ResetPasswordResponse(BaseModel):
    """Outcome of password reset completion."""

    message: str = Field(
        default="Password has been reset successfully. You can now log in.",
        description="Status message",
        examples=["Password has been reset successfully. You can now log in."],
    )


class LoginRequest(BaseModel):
    """Request to authenticate with email and password."""

    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    """Request to create a new user account."""

    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    """Output contract for successful authentication session."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class ForgotPasswordRequest(BaseModel):
    """Request to initiate password reset flow."""

    email: EmailStr


class VerifyResetOTPRequest(BaseModel):
    """Request to verify password reset one-time password code."""

    email: EmailStr
    otp: str = Field(..., validation_alias=AliasChoices("otp", "token"))

    @property
    def token(self) -> str:
        return self.otp


class ResetPasswordRequest(BaseModel):
    """Request to complete password reset using verified token."""

    reset_token: str
    new_password: str
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    """Request to change password for authenticated user."""

    current_password: str
    new_password: str


class ChangeEmailRequest(BaseModel):
    """Request to change email address for authenticated user."""

    new_email: EmailStr


class VerifyEmailChangeRequest(BaseModel):
    """Request to verify updated email token."""

    token: str


__all__ = [
    "AccountSettingsResponse",
    "ChangePasswordResponse",
    "ChangeEmailResponse",
    "AccountDeletionDetails",
    "AccountDeleteResponse",
    "SignupResponse",
    "ForgotPasswordResponse",
    "VerifyResetOTPResponse",
    "ResetPasswordResponse",
    "LoginRequest",
    "SignupRequest",
    "AuthResponse",
    "ForgotPasswordRequest",
    "VerifyResetOTPRequest",
    "ResetPasswordRequest",
    "ChangePasswordRequest",
    "ChangeEmailRequest",
    "VerifyEmailChangeRequest",
]
