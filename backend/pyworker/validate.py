"""Explicit parameter validation (replaces the pydantic request models).

Every helper raises ``api_error(422, ...)`` with a short, client-safe message.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .errors import api_error


def bad(message: str):
    return api_error(422, message)


def check_keys(params: dict, required: Iterable[str] = (), optional: Iterable[str] = ()) -> None:
    required = set(required)
    optional = set(optional)
    missing = required - set(params)
    if missing:
        raise bad(f"Missing parameter(s): {', '.join(sorted(missing))}.")
    extra = set(params) - required - optional
    if extra:
        raise bad(f"Unknown parameter(s): {', '.join(sorted(extra))}.")


def req_str(params: dict, name: str, min_len: int = 1, max_len: Optional[int] = None) -> str:
    v = params.get(name)
    if not isinstance(v, str) or len(v) < min_len or (max_len is not None and len(v) > max_len):
        rng = f"{min_len}..{max_len}" if max_len is not None else f"at least {min_len}"
        raise bad(f"'{name}' must be a string of {rng} characters.")
    return v


def opt_str(params: dict, name: str, min_len: int = 0, max_len: Optional[int] = None,
            default: Optional[str] = None) -> Optional[str]:
    if name not in params or params[name] is None:
        return default
    return req_str(params, name, min_len, max_len)


def opt_int(params: dict, name: str, default: Optional[int], lo: int, hi: int) -> Optional[int]:
    if name not in params or params[name] is None:
        return default
    v = params[name]
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        raise bad(f"'{name}' must be an integer in [{lo}, {hi}].")
    return v


def opt_bool(params: dict, name: str, default: Optional[bool] = None) -> Optional[bool]:
    if name not in params or params[name] is None:
        return default
    v = params[name]
    if not isinstance(v, bool):
        raise bad(f"'{name}' must be a boolean.")
    return v


def opt_enum(params: dict, name: str, allowed: Sequence[str], default: Optional[str] = None) -> Optional[str]:
    if name not in params or params[name] is None:
        return default
    v = params[name]
    if v not in allowed:
        raise bad(f"'{name}' must be one of {list(allowed)}.")
    return v


def req_enum(params: dict, name: str, allowed: Sequence[str]) -> str:
    if name not in params:
        raise bad(f"Missing parameter(s): {name}.")
    v = params[name]
    if v not in allowed:
        raise bad(f"'{name}' must be one of {list(allowed)}.")
    return v


def opt_list(params: dict, name: str, max_len: int, item_check=None) -> Optional[list]:
    if name not in params or params[name] is None:
        return None
    v = params[name]
    if not isinstance(v, list) or len(v) > max_len:
        raise bad(f"'{name}' must be a list of at most {max_len} items.")
    if item_check is not None:
        for item in v:
            item_check(item)
    return v


def opt_dict(params: dict, name: str) -> Optional[dict]:
    if name not in params or params[name] is None:
        return None
    v = params[name]
    if not isinstance(v, dict):
        raise bad(f"'{name}' must be an object.")
    return v


def pagination(params: dict, default_limit: int, max_limit: int) -> tuple[int, int]:
    skip = opt_int(params, "skip", 0, 0, 2**31)
    limit = opt_int(params, "limit", default_limit, 1, max_limit)
    return skip, limit


def jsonable(value: Any) -> Any:
    return value
