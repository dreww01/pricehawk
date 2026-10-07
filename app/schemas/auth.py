"""Authentication, token, user profile, and account management schemas."""

from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials for user authentication."""

    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    """Credentials for user registration."""

    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    """Authentication token response."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str | None = None


class ForgotPasswordRequest(BaseModel):
    """Request to initiate password reset."""

    email: EmailStr


class VerifyResetOTPRequest(BaseModel):
    """Request to verify one-time password for reset."""

    email: EmailStr
    token: str


class ResetPasswordRequest(BaseModel):
    """Request to set a new password."""

    reset_token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    """Request to change password for an authenticated user."""

    current_password: str
    new_password: str


class ChangeEmailRequest(BaseModel):
    """Request to change email address."""

    new_email: EmailStr


class VerifyEmailChangeRequest(BaseModel):
    """Request to confirm email change with verification token."""

    token: str


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
    details: AccountDeletionDetails | dict[str, Any] = Field(..., description="Resource cleanup summary metrics")


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


class DashboardStatsResponse(BaseModel):
    """Aggregated dashboard statistics summary."""

    products: int = Field(..., description="Total tracked product count", examples=[12])
    competitors: int = Field(..., description="Total competitor URL count", examples=[35])
    alerts: int = Field(..., description="Pending alerts count over past 7 days", examples=[4])
    insights: int = Field(..., description="Total AI insights count", examples=[8])


class DashboardActivityItem(BaseModel):
    """Individual price change alert item for dashboard display."""

    id: str = Field(..., description="Alert record identifier", examples=["alt_12345"])
    type: str = Field(..., description="Alert classification type", examples=["price_drop"])
    product_id: str | None = Field(None, description="Parent product identifier", examples=["prod_67890"])
    product_name: str = Field(..., description="Product display name", examples=["Sony WH-1000XM5"])
    retailer: str = Field(..., description="Retailer name or store domain", examples=["amazon.com"])
    old_price: float | None = Field(None, description="Previous recorded price", examples=[399.99])
    new_price: float | None = Field(None, description="Newly detected price", examples=[348.00])
    change_percent: float | None = Field(None, description="Computed percentage price change", examples=[-13.0])
    detected_at: str = Field(..., description="Timestamp when price change was detected", examples=["2025-01-15T12:00:00Z"])


class DashboardActivityResponse(BaseModel):
    """Recent price change activity feed."""

    activity: list[DashboardActivityItem] = Field(default_factory=list, description="Recent activity items")
