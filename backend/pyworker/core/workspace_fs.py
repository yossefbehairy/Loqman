"""Read-only, containment-checked file access for workspaces.

Pure functions, no DB. Every check is repeated on each access (no cached trust).
Nothing here ever writes, creates, renames or deletes files.
"""

import os
import stat
from pathlib import PurePosixPath

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # registration + download cap

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}


class WorkspaceFsError(Exception):
    """Carries an HTTP-ish status so routes can map it directly."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _is_reparse(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def canonical_workspace_dir(raw: str) -> str:
    """Validate a user-chosen directory and return its canonical path."""
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise WorkspaceFsError(422, "path must be a non-empty string")
    raw = raw.strip()
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise WorkspaceFsError(422, "UNC / device paths are not supported")
    if not os.path.isabs(raw) or (os.name == "nt" and not os.path.splitdrive(raw)[0]):
        raise WorkspaceFsError(422, "path must be absolute")
    if ".." in PurePosixPath(raw.replace("\\", "/")).parts:
        raise WorkspaceFsError(422, "path must not contain '..'")
    try:
        real = os.path.realpath(raw, strict=True)
    except (OSError, ValueError):
        raise WorkspaceFsError(404, "directory does not exist")
    if not os.path.isdir(real):
        raise WorkspaceFsError(422, "path is not a directory")
    if _norm(os.path.dirname(real)) == _norm(real):
        raise WorkspaceFsError(422, "filesystem roots cannot be registered as workspaces")
    profile = os.environ.get("USERPROFILE")
    if profile and _norm(real) == _norm(os.path.realpath(profile)):
        raise WorkspaceFsError(422, "user profile cannot be registered as a workspace")
    return real


def _clean_relative(rel: str) -> list[str]:
    if not isinstance(rel, str) or not rel.strip() or "\0" in rel:
        raise WorkspaceFsError(422, "invalid path")
    unified = rel.replace("\\", "/")
    if unified.startswith("/") or os.path.isabs(rel) or (len(unified) > 1 and unified[1] == ":"):
        raise WorkspaceFsError(422, "path must be relative to the workspace")
    parts = [p for p in unified.split("/") if p not in ("", ".")]
    if not parts:
        raise WorkspaceFsError(422, "invalid path")
    for part in parts:
        if part == ".." or ":" in part:  # ':' blocks drive switches and NTFS streams
            raise WorkspaceFsError(422, "path escapes workspace or is not allowed")
        if part.rstrip(" .") != part or part.split(".")[0].upper() in _WIN_RESERVED and os.name == "nt":
            raise WorkspaceFsError(422, "path component not allowed")
    return parts


def resolve_in_workspace(workspace_path: str, rel: str) -> tuple[str, str]:
    """Return (absolute_real_path, posix_relative_path) for an existing regular file.

    Rejects traversal, any symlink/junction/reparse component, and anything whose
    realpath leaves the (re-canonicalised) workspace.
    """
    parts = _clean_relative(rel)
    try:
        root = canonical_workspace_dir(workspace_path)
    except OSError:
        raise WorkspaceFsError(404, "workspace directory is no longer available")
    if _norm(root) != _norm(workspace_path):
        raise WorkspaceFsError(409, "workspace directory changed (link or move detected)")

    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            raise WorkspaceFsError(404, "file not found")
        except OSError:
            raise WorkspaceFsError(403, "file not accessible")
        if _is_reparse(st):
            raise WorkspaceFsError(403, "symlinks and junctions are not allowed")

    real = os.path.realpath(current, strict=True)
    try:
        inside = os.path.commonpath([_norm(real), _norm(root)]) == _norm(root)
    except ValueError:  # different drives
        inside = False
    if not inside or _norm(real) == _norm(root):
        raise WorkspaceFsError(403, "path is outside the workspace")
    if not stat.S_ISREG(os.lstat(real).st_mode):
        raise WorkspaceFsError(422, "not a regular file")
    return real, "/".join(parts)


def file_size(real_path: str) -> int:
    size = os.stat(real_path).st_size
    if size > MAX_ARTIFACT_BYTES:
        raise WorkspaceFsError(413, f"file exceeds {MAX_ARTIFACT_BYTES} bytes")
    return size


def _opened_path(fh) -> str | None:
    """Resolve the opened handle, not a pathname that can be swapped."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        fn = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        fn.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        handle = msvcrt.get_osfhandle(fh.fileno())
        size = fn(handle, None, 0, 0)
        if not size:
            return None
        buf = ctypes.create_unicode_buffer(size + 1)
        if not fn(handle, buf, len(buf), 0):
            return None
        path = buf.value
        return path[4:] if path.startswith("\\\\?\\") else path
    proc_path = f"/proc/self/fd/{fh.fileno()}"
    if os.path.exists(proc_path):
        return os.path.realpath(proc_path)
    return None


def read_limited(real_path: str, workspace_path: str | None = None) -> bytes:
    """Read the file read-only, verifying the opened handle is the validated file."""
    try:
        with open(real_path, "rb") as fh:
            opened = os.fstat(fh.fileno())
            if opened.st_nlink > 1:
                raise WorkspaceFsError(403, "hard-linked files are not allowed")
            if not stat.S_ISREG(opened.st_mode):
                raise WorkspaceFsError(422, "not a regular file")
            if _is_reparse(os.lstat(real_path)) or not os.path.samestat(opened, os.stat(real_path)):
                raise WorkspaceFsError(409, "file changed during access")
            if workspace_path is not None:
                actual = _opened_path(fh)
                if not actual:
                    raise WorkspaceFsError(403, "cannot verify opened file path")
                try:
                    if os.path.commonpath([_norm(actual), _norm(workspace_path)]) != _norm(workspace_path):
                        raise WorkspaceFsError(403, "opened file is outside the workspace")
                except ValueError:
                    raise WorkspaceFsError(403, "opened file is outside the workspace")
            if opened.st_size > MAX_ARTIFACT_BYTES:
                raise WorkspaceFsError(413, f"file exceeds {MAX_ARTIFACT_BYTES} bytes")
            data = fh.read(MAX_ARTIFACT_BYTES + 1)  # growth after fstat is still capped
    except FileNotFoundError:
        raise WorkspaceFsError(404, "file not found")
    except PermissionError:
        raise WorkspaceFsError(403, "file not accessible")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise WorkspaceFsError(413, f"file exceeds {MAX_ARTIFACT_BYTES} bytes")
    return data
