"""Stable, client-safe error type. Messages here are shown to the client, so
they must never contain tracebacks, paths to secrets, or raw exception text.

``status`` carries the HTTP-like status the legacy REST API used for the same
condition (404, 409, ...), so an adapter in front of the worker can keep the
old contract; ``detail`` is the legacy ``detail`` payload (string or object).
"""

from __future__ import annotations

from typing import Any, Optional


class WorkerError(Exception):
    def __init__(self, code: str, message: str, request_id=None, status: Optional[int] = None,
                 detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.status is not None:
            out["status"] = self.status
        if self.detail is not None:
            out["detail"] = self.detail
        return out


# Error codes (stable; documented in docs/PYWORKER.md)
PARSE_ERROR = "parse_error"
INVALID_REQUEST = "invalid_request"
METHOD_NOT_FOUND = "method_not_found"
INVALID_PARAMS = "invalid_params"
MESSAGE_TOO_LARGE = "message_too_large"
DUPLICATE_REQUEST_ID = "duplicate_request_id"
NOT_FOUND = "not_found"
RUNTIME_UNAVAILABLE = "runtime_unavailable"
TOO_MANY_RUNS = "too_many_runs"
OVERLOADED = "overloaded"
SHUTTING_DOWN = "shutting_down"
INTERNAL_ERROR = "internal_error"

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: NOT_FOUND,
    409: "conflict",
    413: "too_large",
    422: INVALID_PARAMS,
    429: "too_many_requests",
    500: INTERNAL_ERROR,
    502: "upstream_error",
    503: "unavailable",
}


def api_error(status: int, detail: Any) -> WorkerError:
    """Build the equivalent of the legacy ``HTTPException(status, detail)``."""
    message = detail if isinstance(detail, str) else (
        detail.get("message") if isinstance(detail, dict) and isinstance(detail.get("message"), str)
        else "Request failed.")
    return WorkerError(_STATUS_CODES.get(status, "error"), message, status=status, detail=detail)
