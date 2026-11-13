"""Bound subprocess output while draining stdout and stderr concurrently."""

from __future__ import annotations

import asyncio

from .errors import RuntimeErrorBase

MAX_PROCESS_OUTPUT_BYTES = 8 * 1024 * 1024


class RuntimeOutputLimit(RuntimeErrorBase):
    code = "output_limit"


async def communicate_bounded(proc, data=None, *, kill=None, max_bytes=MAX_PROCESS_OUTPUT_BYTES):
    used = 0

    async def read(stream):
        nonlocal used
        chunks = []
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return b"".join(chunks)
            used += len(chunk)
            if used > max_bytes:
                raise RuntimeOutputLimit("Engine output exceeded the allowed byte limit.")
            chunks.append(chunk)

    async def write():
        if proc.stdin is None:
            return
        try:
            if data:
                proc.stdin.write(data)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    tasks = [asyncio.create_task(read(proc.stdout)),
             asyncio.create_task(read(proc.stderr)),
             asyncio.create_task(write())]

    async def cleanup():
        # Drain without retaining bytes so killing cannot leave a paused pipe
        # transport behind on Windows.
        async def discard(stream):
            while await stream.read(64 * 1024):
                pass

        drains = [asyncio.create_task(discard(proc.stdout)),
                  asyncio.create_task(discard(proc.stderr))]
        try:
            await kill(proc)
            try:
                await asyncio.wait_for(asyncio.gather(*drains), 5)
            except asyncio.TimeoutError:
                pass
        finally:
            for task in drains:
                task.cancel()
            await asyncio.gather(*drains, return_exceptions=True)

    try:
        out, err, _ = await asyncio.gather(*tasks)
        await proc.wait()
        return out, err
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if kill is not None:
            await asyncio.shield(cleanup())
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
