"""The new path must not depend, directly or indirectly, on FastAPI & co.

Three independent checks:
 1. static scan of backend/pyworker sources,
 2. import every module in a clean interpreter and inspect sys.modules,
 3. run the REAL worker end to end with those imports hard-blocked.
"""

import ast
import json
import subprocess
import sys
import time

from .pyworker_harness import BANNED, ROOT, worker_factory  # noqa: F401

PKG = ROOT / "backend" / "pyworker"


def _is_banned(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BANNED)


def test_static_scan_finds_no_banned_imports():
    offenders = []
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            offenders += [(path.name, n) for n in names if _is_banned(n)]
    assert offenders == []


def test_importing_all_modules_loads_only_stdlib():
    code = (
        "import sys, json, importlib, pkgutil\n"
        "sys.path.insert(0, %r)\n"
        "import backend.pyworker as p\n"
        "for m in pkgutil.iter_modules(p.__path__):\n"
        "    if m.name != '__main__': importlib.import_module('backend.pyworker.' + m.name)\n"
        "import backend.pyworker.__main__\n"
        "tops = sorted({n.split('.')[0] for n in sys.modules})\n"
        "print(json.dumps(tops))\n"
    ) % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    tops = set(json.loads(out.stdout.strip().splitlines()[-1]))
    allowed = set(sys.stdlib_module_names) | {"backend", "_virtualenv", "sitecustomize", "usercustomize"}
    # site-packages .pth hooks can inject helper modules; they are not ours.
    third_party = {t for t in tops - allowed if not t.startswith(("_", "nt", "posix"))}
    assert not any(_is_banned(t) for t in tops)
    assert "backend.app" not in " ".join(sorted(tops))
    assert third_party == set(), third_party


def test_real_worker_runs_end_to_end_with_banned_modules_blocked(worker_factory):
    """Exercise most domains (runs, missions, CEO, workflows, backup) with the imports hard-blocked."""
    w = worker_factory(block_imports=True)
    run_id = w.start_run("no frameworks")
    assert w.wait_status(run_id, "succeeded")["assistant_content"]
    m = w.ok("missions.create", {"objective": "Plan and review something real"})
    deadline = time.monotonic() + 15
    while w.ok("missions.get", {"mission_id": m["id"]})["status"] not in ("succeeded", "failed", "partially_succeeded"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert w.ok("missions.get", {"mission_id": m["id"]})["status"] == "succeeded"
    assert w.ok("ceo.ask", {"question": "show me the missions"})["iterations"] == 2
    assert w.ok("usage.summary")["totals"]["events"] > 0
    backup = w.ok("backup.create")
    w.ok("backup.cleanup", {"path": backup["path"]})
    w.ok("shutdown")
    assert w.wait_exit(10) == 0
    assert "BLOCKED-IMPORT" not in w.stderr_text()
