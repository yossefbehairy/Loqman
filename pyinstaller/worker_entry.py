"""Frozen entry point for the stdlib-only Python worker (see docs/PYWORKER.md).

PyInstaller runs the entry as a plain script, so it imports the package module
absolutely instead of using `python -m backend.pyworker`.
"""
import os
import sys

from backend.pyworker.__main__ import main

if __name__ == "__main__":
    code = main()
    try:
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)  # the stdin reader thread is blocked in readline(); see __main__.py
