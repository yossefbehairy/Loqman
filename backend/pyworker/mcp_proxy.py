"""Stdio MCP transport into one authenticated, short-lived worker tool session."""

import json
import os
import socket
import sys

MAX_FRAME = 256 * 1024


def main():
    port = int(os.environ["AGENTOS_MCP_PORT"])
    token = os.environ["AGENTOS_MCP_TOKEN"]
    while True:
        line = sys.stdin.buffer.readline(MAX_FRAME + 1)
        if not line:
            return 0
        if len(line) > MAX_FRAME:
            return 2
        message = json.loads(line)
        with socket.create_connection(("127.0.0.1", port), timeout=600) as connection:
            frame = json.dumps({"token": token, "message": message}, ensure_ascii=False).encode("utf-8") + b"\n"
            connection.sendall(frame)
            with connection.makefile("rb") as response_file:
                response = json.loads(response_file.readline(2 * 1024 * 1024))
        if response.get("response") is not None:
            print(json.dumps(response["response"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
