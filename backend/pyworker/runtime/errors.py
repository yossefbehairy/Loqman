"""
Normalized, engine-neutral runtime errors shared by every engine adapter.
Maps transport / provider failures to useful, non-sensitive UI messages.
"""

from __future__ import annotations


class RuntimeErrorBase(Exception):
    code: str = "runtime_error"
    retryable: bool = False

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RuntimeOffline(RuntimeErrorBase):
    code = "offline"
    retryable = True


class RuntimeAuthError(RuntimeErrorBase):
    code = "auth"
    retryable = False


class RuntimeRateLimit(RuntimeErrorBase):
    code = "rate_limited"
    retryable = True


class RuntimeModelUnavailable(RuntimeErrorBase):
    code = "model_unavailable"
    retryable = False


class RuntimeTimeout(RuntimeErrorBase):
    code = "timeout"
    retryable = True


class RuntimeCancelled(RuntimeErrorBase):
    code = "cancelled"
    retryable = False


def safe_error_message(error: BaseException) -> str:
    """Expose a stable error category without persisting provider output."""
    if isinstance(error, ValueError) and str(error) == "Mission task returned invalid JSON":
        return "Mission task returned invalid JSON"
    code = getattr(error, "code", "runtime_error")
    if code not in {"offline", "auth", "rate_limited", "model_unavailable", "timeout", "cancelled", "unsupported_capability", "output_limit"}:
        code = "runtime_error"
    return f"Request failed ({code})."
