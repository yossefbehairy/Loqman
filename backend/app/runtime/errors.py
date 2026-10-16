"""
Normalized runtime errors for AgentOS OpenCode integration.
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


def normalize_cli_error(stderr: str, returncode: int) -> RuntimeErrorBase:
    text = (stderr or "").lower()
    if "unauthorized" in text or "401" in text or "invalid api key" in text or "auth" in text and "oauth" in text:
        return RuntimeAuthError("OpenCode authentication failed. Check provider login (opencode auth login).")
    if "429" in text or "rate limit" in text or "too many requests" in text or "quota" in text:
        return RuntimeRateLimit("OpenCode rate limit or free-tier quota reached. Wait and retry; no paid fallback used.")
    if "not found" in text or "unknown model" in text or "model not" in text:
        return RuntimeModelUnavailable("Requested model is not available in the current OpenCode environment.")
    if "econnrefused" in text or "connect" in text or "no such host" in text or "timed out" in text:
        return RuntimeOffline("Cannot reach the OpenCode runtime. Ensure `opencode` is installed and reachable.")
    return RuntimeErrorBase(f"OpenCode execution failed (exit {returncode}).")
