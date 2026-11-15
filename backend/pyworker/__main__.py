"""Entry point: ``python -m backend.pyworker --db <path>``.

stdout is reserved for the protocol: we grab the binary handle first and point
``sys.stdout`` at stderr so a stray print() can never corrupt a frame.
"""

from __future__ import annotations

import asyncio
import os
import sys

from .config import parse_args, setup_logging


def main(argv: list[str] | None = None) -> int:
    if (sys.argv[1:] if argv is None else argv) == ["--mcp-proxy"]:
        from .mcp_proxy import main as proxy_main
        return proxy_main()
    settings = parse_args(sys.argv[1:] if argv is None else argv)
    from .appsettings import load_settings

    try:  # a retired RUNTIME_MODE (env or .env) gets a clear message instead of a stack trace
        load_settings(AGENTOS_DATA_DIR=settings.data_dir, RUNTIME_MODE=settings.runtime_mode)
    except ValueError as exc:
        sys.stderr.write(f"agentos worker: {exc}\n")
        return 2
    proto_out = sys.stdout.buffer
    proto_in = sys.stdin.buffer
    sys.stdout = sys.stderr
    setup_logging(settings.log_level)

    from .server import run

    return asyncio.run(run(settings, proto_in, proto_out))


if __name__ == "__main__":
    code = main()
    try:
        sys.stderr.flush()
    except Exception:
        pass
    # The stdin reader is a daemon thread blocked in readline(); a normal
    # interpreter exit can hang on its buffer lock on Windows. Output was
    # already flushed by the transport, so exit hard.
    os._exit(code)
