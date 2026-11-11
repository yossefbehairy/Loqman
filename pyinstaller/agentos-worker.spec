# -*- mode: python ; coding: utf-8 -*-
"""
AgentOS backend (stdlib-only Python worker) - PyInstaller spec, ONEFILE console exe.

Entry   : pyinstaller/worker_entry.py -> backend.pyworker (JSON Lines on stdin/stdout)
Output  : <distpath>/agentos-backend.exe  (same name Electron already expects)
No web framework is bundled: only the standard library + backend.pyworker.

    backend\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ^
        --distpath backend\dist --workpath backend\build pyinstaller\agentos-worker.spec
"""
import os

from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# Runtime adapters / orchestration are imported lazily (factories), so pin them.
hiddenimports = collect_submodules("backend.pyworker")

a = Analysis(
    [os.path.join(REPO_ROOT, "pyinstaller", "worker_entry.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "pytest_asyncio", "fastapi", "starlette", "uvicorn",
              "pydantic", "pydantic_core", "sqlalchemy", "httpx", "anyio", "sse_starlette", "backend.app"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="agentos-backend",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Electron talks to it over stdin/stdout
    disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
