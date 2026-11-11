"""
Read-only discovery of locally installed engine CLIs.

Runs ONLY supported local version/status commands. It never starts a model
turn, never reads credential files, and never returns raw CLI output (only a
sanitized version string and enum states), so tokens cannot leak through it.

Installed != authenticated != working adapter. This module reports the first
two at most; `adapter_supported` reports that code exists, not that a live
model call has succeeded.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Sequence

PROBE_TIMEOUT_SECONDS = 5.0

INSTALLED = "installed"
NOT_INSTALLED = "not_installed"
UNKNOWN = "unknown"

AUTHENTICATED = "authenticated"
UNAUTHENTICATED = "unauthenticated"


@dataclass(frozen=True)
class EngineSpec:
    engine_id: str
    display_name: str
    binaries: Sequence[str]
    version_args: Sequence[str]
    # None => no supported non-interactive status command known => auth unknown.
    auth_args: Optional[Sequence[str]]
    auth_parser: Optional[Callable[[int, str], str]]


@dataclass
class EngineStatus:
    engine_id: str
    display_name: str
    install_state: str            # installed | not_installed | unknown
    auth_state: str               # authenticated | unauthenticated | unknown
    version: Optional[str]
    executable: Optional[str]
    adapter_supported: bool
    detail: str


def _parse_codex_auth(code: int, out: str) -> str:
    low = out.lower()
    if code == 0 and "logged in" in low and "not logged in" not in low:
        return AUTHENTICATED
    if "not logged in" in low:
        return UNAUTHENTICATED
    return UNKNOWN


def _parse_claude_auth(code: int, out: str) -> str:
    try:
        data = json.loads(out)
        if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
            return AUTHENTICATED if data["loggedIn"] else UNAUTHENTICATED
    except (ValueError, TypeError):
        pass
    return UNKNOWN


def _parse_opencode_auth(code: int, out: str) -> str:
    if code != 0:
        return UNKNOWN
    m = re.search(r"(\d+)\s+credentials?", out, re.IGNORECASE)
    if not m:
        return UNKNOWN
    return AUTHENTICATED if int(m.group(1)) > 0 else UNAUTHENTICATED


ENGINE_SPECS: List[EngineSpec] = [
    EngineSpec("opencode", "OpenCode", ("opencode",), ("--version",),
               ("auth", "list"), _parse_opencode_auth),
    EngineSpec("codex", "Codex", ("codex",), ("--version",),
               ("login", "status"), _parse_codex_auth),
    EngineSpec("claude-code", "Claude Code", ("claude",), ("--version",),
               ("auth", "status"), _parse_claude_auth),
    # No verified status command for Antigravity: auth stays unknown.
    EngineSpec("antigravity", "Antigravity", ("agy",), ("--version",), None, None),
]

_VERSION_RE = re.compile(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.\-]+)?")


def _run(exe: str, args: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
    """Run a probe. Returns None on timeout/OS error. Never raises."""
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            [exe, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            shell=False,
            env={k: v for k, v in os.environ.items() if k != "AGENTOS_LOCAL_TOKEN"},
            **kwargs,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _extract_version(text: str) -> Optional[str]:
    m = _VERSION_RE.search(text or "")
    return m.group(0)[:64] if m else None


def probe_engine(spec: EngineSpec) -> EngineStatus:
    exe = None
    for name in spec.binaries:
        exe = shutil.which(name)
        if exe:
            break
    base = dict(engine_id=spec.engine_id, display_name=spec.display_name,
                adapter_supported=spec.engine_id in {"opencode", "claude-code", "codex", "antigravity"})
    if not exe:
        return EngineStatus(**base, install_state=NOT_INSTALLED, auth_state=UNKNOWN,
                            version=None, executable=None,
                            detail="CLI not found on PATH.")

    ver = _run(exe, spec.version_args)
    if ver is None:
        return EngineStatus(**base, install_state=UNKNOWN, auth_state=UNKNOWN,
                            version=None, executable=exe,
                            detail="Version probe timed out or could not start.")
    version = _extract_version((ver.stdout or "") + " " + (ver.stderr or ""))
    if ver.returncode != 0 and version is None:
        return EngineStatus(**base, install_state=UNKNOWN, auth_state=UNKNOWN,
                            version=None, executable=exe,
                            detail="Version probe failed; install state unverified.")

    auth = UNKNOWN
    detail = "Installed. Authentication not checked: no supported status command."
    if spec.auth_args and spec.auth_parser:
        res = _run(exe, spec.auth_args)
        if res is None:
            detail = "Installed. Auth status probe timed out or failed to start."
        else:
            auth = spec.auth_parser(res.returncode, (res.stdout or "") + (res.stderr or ""))
            detail = {
                AUTHENTICATED: "Installed; CLI reports an authenticated session.",
                UNAUTHENTICATED: "Installed; CLI reports no authenticated session.",
                UNKNOWN: "Installed; auth status output not recognized.",
            }[auth]
    detail += " Discovery does not verify actual model execution."
    return EngineStatus(**base, install_state=INSTALLED, auth_state=auth,
                        version=version, executable=exe, detail=detail)


def discover_engines() -> List[EngineStatus]:
    with ThreadPoolExecutor(max_workers=len(ENGINE_SPECS)) as pool:
        return list(pool.map(probe_engine, ENGINE_SPECS))
