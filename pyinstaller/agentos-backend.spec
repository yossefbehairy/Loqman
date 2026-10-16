# -*- mode: python ; coding: utf-8 -*-
"""
AgentOS backend - PyInstaller spec (ONEFILE, console).

Entry point : backend/app/cli.py  (desktop sidecar entry; parses --port/--host/--data-dir)
Imports     : absolute "backend.app.*" imports, so the repo root must be on pathex.
Output      : backend/dist/agentos-backend.exe

Build (from the repo root, using the backend venv):

    backend\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean ^
        --distpath backend\\dist --workpath backend\\build ^
        pyinstaller\\agentos-backend.spec

or simply:  powershell -ExecutionPolicy Bypass -File pyinstaller\\build-backend.ps1
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is the directory containing this spec file -> repo root is its parent.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # D:\AgentOS

hiddenimports = [
    # uvicorn resolves loop/http/websocket/lifespan implementations dynamically
    # via importlib, so the auto() modules and concrete impls must be pinned.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # SQLAlchemy loads the DBAPI dialect module dynamically.
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    # anyio selects its backend (asyncio) dynamically.
    "anyio._backends._asyncio",
    # App object imported inside cli.main() after env vars are applied.
    "backend.app.main",
    # Reached dynamically via __import__() in backend/app/orchestration/ceo.py.
    "backend.app.orchestration.planner",
]

# SSE streaming endpoint (sse_starlette) - grab every submodule.
hiddenimports += collect_submodules("sse_starlette")

a = Analysis(
    [os.path.join(REPO_ROOT, "backend", "app", "cli.py")],
    pathex=[REPO_ROOT],  # makes "backend.app.*" absolute imports resolvable
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "pytest_asyncio"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ONEFILE: everything is bundled into a single console exe (no _internal dir).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agentos-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries are a false-positive magnet for AV.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Electron captures stdout/stderr -> keep the console subsystem.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
