"""Bounded full-text projection of the driver's single ordered event sink."""

from __future__ import annotations

import asyncio

from .base import AgentResult
from .driver import EVENT_TYPES, DriverFailure, DriverTurnError, Emit, StartTurn, TurnDriver


async def collect_turn(driver: TurnDriver, turn: StartTurn, emit: Emit | None = None,
                       *, is_cancelled=None) -> AgentResult:
    done = asyncio.get_running_loop().create_future()
    items: dict[str, dict] = {}
    usage = {}
    last_seq = 0
    size = 0
    admitted = False

    def fail(category, message):
        raise DriverTurnError(DriverFailure(category, message, False, "running", "output-seen"))

    async def accept(event):
        nonlocal last_seq, size, usage, admitted
        if done.done():
            return  # terminal immutability, including duplicate finish frames
        try:
            if event.version != 1 or event.type not in EVENT_TYPES:
                fail("protocol_error", "Unsupported driver event vocabulary or version.")
            if event.turn_id != turn.turn_id or event.run_id != turn.run_id or event.seq != last_seq + 1:
                fail("protocol_error", "Invalid driver event identity or order.")
            admitted = True
            last_seq = event.seq
            if last_seq > 100000:
                fail("output_limit", "Driver event limit exceeded.")
            data = event.data
            if event.type in {"content.delta", "item.completed"}:
                if not event.item_id:
                    fail("protocol_error", "Text event has no item identity.")
                if len(items) >= 4096 and event.item_id not in items:
                    fail("output_limit", "Driver item limit exceeded.")
                item = items.setdefault(event.item_id, {"text": "", "kind": None, "phase": None})
                old_size = len(item["text"])
                if event.type == "content.delta":
                    text = data.get("delta", "")
                    item["kind"] = data.get("stream_kind")
                    new_size = old_size + len(text)
                else:
                    text = data.get("text", "")
                    item["kind"] = data.get("item_type")
                    new_size = len(text)
                if size - old_size + new_size > turn.max_output_chars:
                    fail("output_limit", "Engine output exceeded the allowed character limit.")
                size += new_size - old_size
                item["text"] = item["text"] + text if event.type == "content.delta" else text
                item["phase"] = data.get("phase", item["phase"])
                item["synthetic_error"] = item.get("synthetic_error", False) or bool(data.get("synthetic_error"))
            elif event.type == "usage" and data.get("scope") == "turn":
                usage = data  # replacement snapshot, never sum measurements
            if emit is not None:
                await emit(event)
            if event.type == "turn.completed":
                if data.get("status") != "succeeded":
                    failure = data.get("failure")
                    if failure:
                        raise DriverTurnError(DriverFailure(**failure))
                    fail("cancelled" if data.get("status") == "cancelled" else "runtime_error", "Turn did not succeed.")
                texts = []
                final_ids = data.get("final_item_ids", [])
                if len(final_ids) != len(set(final_ids)):
                    fail("protocol_error", "Duplicate final assistant item identity.")
                for item_id in final_ids:
                    item = items.get(item_id)
                    if item is None:
                        fail("protocol_error", "Final assistant item is missing.")
                    if item["kind"] in {"assistant", "assistant_text"} and item["phase"] != "commentary" and not item.get("synthetic_error"):
                        texts.append(item["text"])
                final_usage = data.get("usage") or usage
                # Drivers retain the existing token dictionary for lossless compatibility.
                tokens = final_usage.get("tokens")
                if tokens is None:
                    tokens = {k: final_usage[k] for k in ("input", "output", "reasoning", "cache")
                              if final_usage.get(k) is not None}
                    if final_usage.get("cache_read") is not None or final_usage.get("cache_write") is not None:
                        tokens["cache"] = {k: final_usage[f"cache_{k}"] for k in ("read", "write")
                                           if final_usage.get(f"cache_{k}") is not None}
                reasoning_items = [i["text"] for i in items.values() if i["kind"] in {"reasoning", "reasoning_text"}]
                reasoning = "".join(reasoning_items) if reasoning_items else None
                done.set_result(AgentResult(
                    text="".join(texts), reasoning=reasoning,
                    model_id=data.get("model_id", turn.model_id), variant_id=data.get("variant"),
                    cost=final_usage.get("cost"), tokens=tokens, duration_ms=data.get("duration_ms", 0),
                    session_id=event.native_session_id or data.get("session_id"),
                ))
        except Exception as exc:
            if not done.done():
                done.set_exception(exc if isinstance(exc, DriverTurnError) else DriverTurnError(DriverFailure(
                    "protocol_error", "Invalid driver event or event delivery failure.", False, "running", "unknown")))

    async def cleanup():
        outcome = await driver.interrupt(turn.turn_id)
        if outcome != "unknown":
            await driver.close(turn.scope_kind, turn.scope_id)

    try:
        try:
            await driver.start_turn(turn, accept)
        except asyncio.CancelledError:
            admitted = True  # admission may have registered ownership before cancellation
            raise
        admitted = True
        return await done
    finally:
        # A separate shielded task also survives repeated cancellation of the caller.
        async def cleanup_if_admitted():
            if admitted:
                await cleanup()

        cleaning = asyncio.create_task(cleanup_if_admitted())
        cancelled_during_cleanup = False
        while not cleaning.done():
            try:
                await asyncio.shield(cleaning)
            except asyncio.CancelledError:
                cancelled_during_cleanup = True
                continue
        await cleaning
        if done.done() and not done.cancelled():
            done.exception()  # consume a terminal exception if admission/cancellation won
        if cancelled_during_cleanup and done.done() and not done.cancelled() and done.exception() is None:
            # Cleanup cancellation cannot discard an already collected answer.
            # The durable manager's cancellation gate still wins over that answer.
            if is_cancelled is None or not is_cancelled():
                return done.result()
        if cancelled_during_cleanup:
            raise asyncio.CancelledError
