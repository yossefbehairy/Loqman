"""JSON Lines wire protocol ("agentos-worker/1").

This is OUR protocol. It is not MCP and not JSON-RPC 2.0 (no "jsonrpc" field).
One JSON object per line, UTF-8, "\n" terminated.

Client -> worker:   {"id": <str|int>, "method": "<name>", "params": {...}}
Worker -> client:   {"type":"response","id":<id>,"result":{...}}
                    {"type":"response","id":<id|null>,"error":{"code":..,"message":..}}
                    {"type":"event","event":"run.progress","run_id":..,"sequence":N,"data":{..}}
                    {"type":"ready", ...}   (first line, once)
                    {"type":"fatal","error":{..}}  (startup failure, then exit)
"""

from __future__ import annotations

import json
import re

from .errors import (
    INVALID_REQUEST,
    PARSE_ERROR,
    WorkerError,
)

DEFAULT_MAX_MESSAGE_BYTES = 1_048_576
MIN_MAX_MESSAGE_BYTES = 1_024
MAX_MAX_MESSAGE_BYTES = 16 * 1_048_576

_METHOD_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_ALLOWED_KEYS = {"id", "method", "params"}
MAX_ID_LENGTH = 128


class Request:
    __slots__ = ("id", "method", "params")

    def __init__(self, id, method: str, params: dict):
        self.id = id
        self.method = method
        self.params = params


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name} not allowed")


def _valid_id(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and 0 < len(value) <= MAX_ID_LENGTH


def parse_request(raw: bytes) -> Request:
    """Parse one already-size-checked line. Raises WorkerError (with the
    request id attached when it could be recovered)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkerError(PARSE_ERROR, "Message is not valid UTF-8.") from None
    try:
        obj = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise WorkerError(PARSE_ERROR, "Message is not valid JSON.") from None

    if not isinstance(obj, dict):
        raise WorkerError(INVALID_REQUEST, "Request must be a JSON object.")

    rid = obj.get("id")
    rid_ok = "id" in obj and _valid_id(rid)
    safe_id = rid if rid_ok else None
    if not rid_ok:
        raise WorkerError(
            INVALID_REQUEST, "Request 'id' must be a non-empty string (max 128) or an integer."
        )

    extra = set(obj) - _ALLOWED_KEYS
    if extra:
        raise WorkerError(INVALID_REQUEST, "Unknown request field(s).", safe_id)

    method = obj.get("method")
    if not isinstance(method, str) or not _METHOD_RE.match(method):
        raise WorkerError(INVALID_REQUEST, "Request 'method' is missing or malformed.", safe_id)

    params = obj.get("params", {})
    if not isinstance(params, dict):
        raise WorkerError(INVALID_REQUEST, "Request 'params' must be an object.", safe_id)

    return Request(rid, method, params)


def encode(message: dict) -> bytes:
    """Serialize to one protocol line. ensure_ascii keeps it pure ASCII, so a
    line can never contain a raw newline or an encoding surprise."""
    return json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def response_ok(request_id, result: dict) -> dict:
    return {"type": "response", "id": request_id, "result": result}


def response_error(request_id, error: WorkerError) -> dict:
    return {"type": "response", "id": request_id, "error": error.to_dict()}
