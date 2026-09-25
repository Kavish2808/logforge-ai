"""Consistent error envelope for every non-2xx API response.

Every error the API returns — validation failures, 404s, and unexpected
server errors alike — is shaped the same way so clients can handle them
uniformly. Internal details (stack traces, file paths, DB errors) are
logged server-side and never included in the response body.
"""
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    fields: dict[str, str] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
