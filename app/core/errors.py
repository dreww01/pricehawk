"""Standardized error codes, envelope contracts, and error mapping for PriceHawk API."""

from enum import Enum
from typing import Any
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """Strictly typed error codes for consistent API error reporting."""

    # Validation errors (422)
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Authentication & Session errors (401)
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    SESSION_EXPIRED = "SESSION_EXPIRED"

    # Authorization & Permission errors (403)
    FORBIDDEN = "FORBIDDEN"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # Resource & Client errors (400, 404, 409)
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"

    # Operational & Server errors (429, 500, 503)
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class ErrorEnvelope(BaseModel):
    """Standard error response contract across all API endpoints."""

    detail: Any = Field(
        ...,
        description="Error details or validation errors list for backward compatibility",
        examples=["Product not found"],
    )
    error_code: ErrorCode = Field(
        ...,
        description="Machine-readable strictly typed error code",
        examples=[ErrorCode.NOT_FOUND],
    )
    message: str = Field(
        ...,
        description="Clean, human-readable error description",
        examples=["Product not found"],
    )
    error_id: str | None = Field(
        None,
        description="Unique trace error ID for server errors",
        examples=["e8f1a23c"],
    )
    retry_after: str | None = Field(
        None,
        description="Retry delay indicator when rate-limited",
        examples=["60"],
    )


def map_error_to_code(status_code: int, detail: Any = None) -> ErrorCode:
    """Map HTTP status code and optional detail context to a strictly typed ErrorCode."""
    detail_str = str(detail).lower() if detail else ""

    if status_code == 400:
        if "already" in detail_str or "conflict" in detail_str or "duplicate" in detail_str:
            return ErrorCode.CONFLICT
        return ErrorCode.BAD_REQUEST

    if status_code == 401:
        if "expired" in detail_str:
            return ErrorCode.SESSION_EXPIRED
        return ErrorCode.NOT_AUTHENTICATED

    if status_code == 403:
        return ErrorCode.FORBIDDEN

    if status_code == 404:
        return ErrorCode.NOT_FOUND

    if status_code == 409:
        return ErrorCode.CONFLICT

    if status_code == 422:
        return ErrorCode.VALIDATION_ERROR

    if status_code == 429:
        return ErrorCode.RATE_LIMIT_EXCEEDED

    if status_code == 503:
        return ErrorCode.SERVICE_UNAVAILABLE

    if status_code >= 500:
        return ErrorCode.INTERNAL_ERROR

    return ErrorCode.BAD_REQUEST


def create_error_response(
    status_code: int,
    detail: Any,
    error_code: ErrorCode | str | None = None,
    error_id: str | None = None,
    retry_after: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Construct a standardized JSONResponse with ErrorEnvelope content."""
    if error_code is None:
        resolved_code = map_error_to_code(status_code, detail)
    elif isinstance(error_code, ErrorCode):
        resolved_code = error_code
    elif isinstance(error_code, str):
        try:
            resolved_code = ErrorCode(error_code)
        except ValueError:
            resolved_code = map_error_to_code(status_code, detail)
    else:
        resolved_code = map_error_to_code(status_code, detail)

    # Format clean human-readable message
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, list):
        formatted_msgs: list[str] = []
        for err in detail:
            if isinstance(err, dict):
                loc_parts = [str(x) for x in err.get("loc", []) if str(x) != "body"]
                loc = ".".join(loc_parts)
                msg = err.get("msg", "")
                if loc and msg:
                    formatted_msgs.append(f"{loc}: {msg}")
                elif msg:
                    formatted_msgs.append(msg)
            elif isinstance(err, str):
                formatted_msgs.append(err)
        message = f"Validation failed: {'; '.join(formatted_msgs)}" if formatted_msgs else "Validation error"
    elif isinstance(detail, dict) and "message" in detail:
        message = str(detail["message"])
    else:
        message = str(detail)

    payload: dict[str, Any] = {
        "detail": jsonable_encoder(detail),
        "error_code": resolved_code.value,
        "message": message,
    }
    if error_id is not None:
        payload["error_id"] = error_id
    if retry_after is not None:
        payload["retry_after"] = str(retry_after)

    return JSONResponse(status_code=status_code, content=payload, headers=headers)


STANDARD_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Bad Request"},
    401: {"model": ErrorEnvelope, "description": "Unauthorized / Session Expired"},
    403: {"model": ErrorEnvelope, "description": "Forbidden"},
    404: {"model": ErrorEnvelope, "description": "Not Found"},
    422: {"model": ErrorEnvelope, "description": "Validation Error"},
    429: {"model": ErrorEnvelope, "description": "Rate Limit Exceeded"},
    500: {"model": ErrorEnvelope, "description": "Internal Server Error"},
}
