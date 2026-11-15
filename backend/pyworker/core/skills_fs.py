"""Read-only discovery and bounded loading of user-selected skill directories.

A *skill source* is a directory the user explicitly registered. A skill is an
immediate child directory containing a regular file named exactly ``SKILL.md``
(a ``SKILL.md`` directly in the source root is exposed as the skill ``.``-less
id ``_root``). Pure functions, no DB, nothing is ever written, executed,
imported or copied. Only ``SKILL.md`` is ever opened; sibling scripts/assets
(and any credentials next to them) are never read.

Skill text is *guidance only*. Reading it grants no filesystem, network, tool
or approval rights, and nothing here applies it to any agent or prompt.

Known limits (TOCTOU): checks are repeated on every access and the opened
handle is compared with the validated path (same file identity, and on
Windows/Linux the handle's final path must lie inside the source). A local
attacker who can swap directories/files between the pre-checks and ``open()``
is narrowed but not fully excluded: the pathname checks and the open are not
one atomic operation (no ``openat``/``O_NOFOLLOW`` walk), and content may
change after it was read (the returned sha256 identifies exactly what was
read). If the handle path cannot be verified, the read is refused.
"""

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field

from backend.pyworker.core.workspace_fs import (
    WorkspaceFsError,
    _is_reparse,
    _norm,
    _opened_path,
    canonical_workspace_dir,
)

SKILL_FILENAME = "SKILL.md"
ROOT_SKILL_ID = "_root"
MAX_SKILL_FILE_BYTES = 256 * 1024  # larger SKILL.md is refused (413)
METADATA_READ_BYTES = 16 * 1024  # listing reads at most this much per skill
DEFAULT_LOAD_CHARS = 20_000
MAX_LOAD_CHARS = 100_000
MAX_SKILLS_PER_SOURCE = 200
MAX_DIR_ENTRIES_SCANNED = 2_000
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500

GUIDANCE_NOTICE = (
    "Skill text is guidance only. It is not auto-applied and grants no file, "
    "network, tool or approval permissions; app policy and the user's "
    "instructions take precedence over it. Bundled scripts are never executed."
)

_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class SkillSummary:
    id: str
    name: str
    description: str
    relative_path: str  # POSIX, relative to the source
    size_bytes: int
    metadata_valid: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class SkippedEntry:
    entry: str
    reason: str


@dataclass
class SkillScan:
    skills: list[SkillSummary]
    skipped: list[SkippedEntry]
    truncated: bool  # scan limits hit; list is incomplete


@dataclass
class LoadedSkill:
    summary: SkillSummary
    content: str
    content_truncated: bool
    sha256: str  # of the full bytes that were read (not just the returned slice)
    bytes_read: int
    mtime: float


def validate_source_dir(raw: str) -> str:
    """Canonicalise a user-chosen source directory. Never defaults or guesses paths."""
    real = canonical_workspace_dir(raw)
    if os.path.dirname(real) == real:
        raise WorkspaceFsError(422, "a filesystem root cannot be a skill source")
    return real


def _safe_id(skill_id: str) -> None:
    if skill_id == ROOT_SKILL_ID:
        return
    if (
        not isinstance(skill_id, str)
        or not _SKILL_ID_RE.match(skill_id)
        or skill_id.rstrip(" .") != skill_id
        or skill_id.split(".")[0].upper() in _WIN_RESERVED
    ):
        raise WorkspaceFsError(422, "invalid skill id")


def _source_root(source_path: str) -> str:
    try:
        root = validate_source_dir(source_path)
    except (OSError, ValueError):
        raise WorkspaceFsError(404, "skill source directory is no longer available")
    if _norm(root) != _norm(source_path):
        raise WorkspaceFsError(409, "skill source directory changed (link or move detected)")
    return root


def _skill_file(root: str, skill_id: str) -> tuple[str, str]:
    """Validate and return (real SKILL.md path, posix relative path); no link components."""
    _safe_id(skill_id)
    parts = [SKILL_FILENAME] if skill_id == ROOT_SKILL_ID else [skill_id, SKILL_FILENAME]
    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            raise WorkspaceFsError(404, "skill not found")
        except OSError:
            raise WorkspaceFsError(403, "skill not accessible")
        if _is_reparse(st):
            raise WorkspaceFsError(403, "symlinks and junctions are not allowed")
    # Exact-case check: case-insensitive filesystems must not alias e.g. skill.md.
    parent = os.path.dirname(current)
    try:
        if SKILL_FILENAME not in os.listdir(parent):
            raise WorkspaceFsError(404, "skill not found")
    except OSError:
        raise WorkspaceFsError(403, "skill not accessible")
    real = os.path.realpath(current, strict=True)
    try:
        inside = os.path.commonpath([_norm(real), _norm(root)]) == _norm(root)
    except ValueError:
        inside = False
    if not inside or _norm(real) != _norm(current):
        raise WorkspaceFsError(403, "path is outside the skill source")
    if not stat.S_ISREG(os.lstat(real).st_mode):
        raise WorkspaceFsError(422, "SKILL.md is not a regular file")
    return real, "/".join(parts)


def _read_file(real: str, root: str, limit: int) -> tuple[bytes, os.stat_result]:
    """Read at most ``limit`` bytes (+1 sentinel) after verifying the opened handle."""
    try:
        with open(real, "rb") as fh:
            opened = os.fstat(fh.fileno())
            if opened.st_nlink > 1:
                raise WorkspaceFsError(403, "hard-linked files are not allowed")
            if not stat.S_ISREG(opened.st_mode):
                raise WorkspaceFsError(422, "SKILL.md is not a regular file")
            if _is_reparse(os.lstat(real)) or not os.path.samestat(opened, os.stat(real)):
                raise WorkspaceFsError(409, "file changed during access")
            actual = _opened_path(fh)
            if not actual:
                raise WorkspaceFsError(403, "cannot verify opened file path")
            try:
                if os.path.commonpath([_norm(actual), _norm(root)]) != _norm(root):
                    raise WorkspaceFsError(403, "opened file is outside the skill source")
            except ValueError:
                raise WorkspaceFsError(403, "opened file is outside the skill source")
            return fh.read(limit + 1), opened
    except FileNotFoundError:
        raise WorkspaceFsError(404, "skill not found")
    except PermissionError:
        raise WorkspaceFsError(403, "skill not accessible")


def _clean(text: str, limit: int) -> str:
    text = _CTRL_RE.sub("", text).strip()
    return text[:limit]


def parse_front_matter(text: str) -> tuple[dict[str, str], bool]:
    """Tiny, non-executing ``---`` front-matter reader (flat ``key: value`` only).

    No YAML library, so no tags/anchors/object construction. Returns (fields, ok).
    """
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, False
    fields: dict[str, str] = {}
    for i, line in enumerate(lines[1:60], start=1):
        if line.strip() == "---":
            return fields, bool(fields)
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]{0,40})\s*:\s*(.*)$", line)
        if not m:
            continue  # nested/continuation lines are ignored, not interpreted
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields.setdefault(m.group(1).lower(), value)
    return {}, False  # never closed


def _summarise(skill_id: str, rel: str, raw: bytes, size: int) -> SkillSummary:
    warnings: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")
        if size <= len(raw):  # a mid-character cut of a larger file is not an error
            warnings.append("not valid UTF-8")
    fields, ok = parse_front_matter(text)
    name = _clean(fields.get("name", ""), MAX_NAME_CHARS)
    desc = _clean(fields.get("description", ""), MAX_DESCRIPTION_CHARS)
    if not ok:
        warnings.append("missing or malformed front matter")
    if not name:
        name = skill_id if skill_id != ROOT_SKILL_ID else "(source root)"
        if ok:
            warnings.append("front matter has no name")
    if not desc and ok:
        warnings.append("front matter has no description")
    if size > MAX_SKILL_FILE_BYTES:
        warnings.append(f"SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes; it cannot be loaded")
    return SkillSummary(skill_id, name, desc, rel, size, ok and not any("UTF-8" in w for w in warnings), warnings)


def scan_source(source_path: str) -> SkillScan:
    """List skills (metadata only, ≤16 KiB read per SKILL.md). Never descends deeper than one level."""
    root = _source_root(source_path)
    skills: list[SkillSummary] = []
    skipped: list[SkippedEntry] = []
    truncated = False
    try:
        with os.scandir(root) as it:
            entries = []
            for n, e in enumerate(it):
                if n >= MAX_DIR_ENTRIES_SCANNED:
                    truncated = True
                    break
                entries.append(e.name)
    except OSError:
        raise WorkspaceFsError(403, "skill source not accessible")

    def add(skill_id: str, label: str) -> None:
        try:
            real, rel = _skill_file(root, skill_id)
            raw, st = _read_file(real, root, METADATA_READ_BYTES)
        except WorkspaceFsError as exc:
            skipped.append(SkippedEntry(label, exc.detail))
            return
        skills.append(_summarise(skill_id, rel, raw[:METADATA_READ_BYTES], st.st_size))

    for name in sorted(entries, key=str.lower):
        if len(skills) >= MAX_SKILLS_PER_SOURCE:
            truncated = True
            break
        if name == SKILL_FILENAME:
            add(ROOT_SKILL_ID, name)
            continue
        full = os.path.join(root, name)
        try:
            st = os.lstat(full)
        except OSError:
            skipped.append(SkippedEntry(name, "not accessible"))
            continue
        if _is_reparse(st):
            skipped.append(SkippedEntry(name, "symlink or junction skipped"))
            continue
        if not stat.S_ISDIR(st.st_mode):
            continue
        if not _SKILL_ID_RE.match(name):
            skipped.append(SkippedEntry(name, "directory name is not a valid skill id"))
            continue
        try:
            has = SKILL_FILENAME in os.listdir(full)
        except OSError:
            skipped.append(SkippedEntry(name, "not accessible"))
            continue
        if has:
            add(name, name)
    return SkillScan(skills, skipped, truncated)


def load_skill(source_path: str, skill_id: str, max_chars: int = DEFAULT_LOAD_CHARS) -> LoadedSkill:
    """Load one SKILL.md text, bounded. Oversized files are refused, not truncated silently."""
    if not isinstance(max_chars, int) or max_chars < 1 or max_chars > MAX_LOAD_CHARS:
        raise WorkspaceFsError(422, f"max_chars must be between 1 and {MAX_LOAD_CHARS}")
    root = _source_root(source_path)
    real, rel = _skill_file(root, skill_id)
    raw, st = _read_file(real, root, MAX_SKILL_FILE_BYTES)
    if len(raw) > MAX_SKILL_FILE_BYTES:
        raise WorkspaceFsError(413, f"SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkspaceFsError(422, "SKILL.md is not valid UTF-8")
    if "\0" in text:
        raise WorkspaceFsError(422, "SKILL.md contains NUL bytes")
    summary = _summarise(skill_id, rel, raw[:METADATA_READ_BYTES], len(raw))
    return LoadedSkill(
        summary=summary,
        content=text[:max_chars],
        content_truncated=len(text) > max_chars,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes_read=len(raw),
        mtime=st.st_mtime,
    )
