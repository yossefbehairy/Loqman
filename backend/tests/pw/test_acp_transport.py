"""AcpConnection (generic ACP JSON-RPC transport) driven with in-memory bytes. No process, network or model."""

import asyncio
import json

import pytest

from backend.pyworker.runtime.acp_transport import (
    AcpConnection, AcpError, AcpTimeout, AcpTransportError, find_model_config, parse_capabilities,
)


class Wire:
    """Records what the client wrote; ``feed`` plays the agent."""

    def __init__(self, **kw):
        self.out = []
        self.block = None
        self.conn = AcpConnection(**kw)

    async def write(self, data):
        if self.block is not None:
            await self.block.wait()
        self.out.append(json.loads(data))

    def start(self):
        self.conn.start(self.write)
        return self

    def agent(self, obj):
        self.conn.feed((json.dumps(obj) + "\n").encode())


async def settle(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_request_response_correlation_out_of_order_and_split_frames():
    w = Wire().start()
    a = asyncio.create_task(w.conn.request("a", {}))
    b = asyncio.create_task(w.conn.request("b", {}))
    await settle()
    ids = {m["method"]: m["id"] for m in w.out}
    frame = (json.dumps({"jsonrpc": "2.0", "id": ids["b"], "result": {"v": "B"}}) + "\r\n").encode()
    w.conn.feed(frame[:7]), w.conn.feed(frame[7:])  # one frame split across chunks, CRLF terminated
    w.agent({"jsonrpc": "2.0", "id": ids["a"], "result": None})
    assert await b == {"v": "B"} and await a == {}
    await w.conn.close()


@pytest.mark.asyncio
async def test_error_response_keeps_code_and_nested_detail_bounded():
    w = Wire().start()
    t = asyncio.create_task(w.conn.request("m", {}))
    await settle()
    w.agent({"jsonrpc": "2.0", "id": w.out[0]["id"],
             "error": {"code": -32603, "message": "boom" * 500, "data": {"error": {"message": "Model Gem-X NOT FOUND"},
                                                                         "errorName": "APIError"}}})
    with pytest.raises(AcpError) as info:
        await t
    err = info.value
    assert err.code == -32603 and len(err.message) <= 500 and "model gem-x not found" in err.hint() and "apierror" in err.hint()
    assert len(err.hint()) <= 1500
    await w.conn.close()


@pytest.mark.asyncio
async def test_pending_table_is_bounded_and_timeout_frees_the_slot():
    w = Wire(max_pending=2).start()
    t1 = asyncio.create_task(w.conn.request("a", {}))
    t2 = asyncio.create_task(w.conn.request("b", {}))
    await settle()
    with pytest.raises(AcpTransportError) as info:
        await w.conn.request("c", {})
    assert info.value.kind == "pending"
    t1.cancel(), t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    w2 = Wire(max_pending=1).start()
    with pytest.raises(AcpTimeout):
        await w2.conn.request("slow", {}, timeout=0.01)
    t3 = asyncio.create_task(w2.conn.request("next", {}))  # the timed-out slot was released
    await settle()
    assert w2.out[-1]["method"] == "next"
    t3.cancel()
    await asyncio.gather(t3, return_exceptions=True)
    await w.conn.close(), await w2.conn.close()


@pytest.mark.asyncio
async def test_unknown_response_id_is_a_protocol_violation_and_fails_pending():
    w = Wire().start()
    t = asyncio.create_task(w.conn.request("a", {}))
    await settle()
    w.agent({"jsonrpc": "2.0", "id": 999, "result": {}})
    with pytest.raises(AcpTransportError) as info:
        await t
    assert info.value.kind == "protocol" and w.conn.fatal is info.value
    with pytest.raises(AcpTransportError):
        await w.conn.request("later", {})  # a failed connection never writes again
    await w.conn.close()


@pytest.mark.asyncio
async def test_non_json_lines_are_diagnostics_not_protocol():
    seen = []
    w = Wire(on_diagnostic=seen.append).start()
    t = asyncio.create_task(w.conn.request("a", {}))
    await settle()
    for i in range(30):
        w.conn.feed(f"[agy] starting component {i}\n".encode())
    w.conn.feed(b"42\n[1,2]\n\xff\xfe not utf8\n")
    w.conn.feed(b"half a line, ")
    w.conn.feed(b"completed later\n")  # a partial diagnostic line is joined across chunks
    w.agent({"jsonrpc": "2.0", "id": w.out[0]["id"], "result": {"ok": True}})
    assert await asyncio.wait_for(t, 5) == {"ok": True}
    assert w.conn.fatal is None and w.conn.diagnostic_count == 34 and len(w.conn.diagnostics) == 20
    assert len(seen) == 34 and all(len(x) <= 300 for x in seen)
    await w.conn.close()


@pytest.mark.asyncio
async def test_excess_noise_oversize_json_and_non_rpc_objects_fail_but_oversize_noise_is_skipped():
    w = Wire(max_diagnostic_lines=3, max_line_bytes=100).start()
    for i in range(4):
        w.conn.feed(b"noise\n")
    assert isinstance(w.conn.fatal, AcpTransportError) and w.conn.fatal.kind == "protocol"
    w = Wire(max_line_bytes=100).start()
    w.conn.feed(b"y" * 250)          # oversized non-JSON: dropped as noise, the rest of its line skipped
    w.conn.feed(b"z" * 50 + b"\n")
    assert w.conn.fatal is None and w.conn.diagnostic_count == 1
    w.agent({"jsonrpc": "2.0", "method": "x/y"})
    assert w.conn.fatal is None
    w.conn.feed(b'{"jsonrpc":"2.0","method":"' + b"m" * 200)  # oversized JSON frame is a violation
    assert isinstance(w.conn.fatal, AcpTransportError)
    w3 = Wire().start()
    w3.agent({"hello": "world"})
    assert isinstance(w3.conn.fatal, AcpTransportError)
    w4 = Wire(max_total_bytes=50).start()
    w4.conn.feed(b"x" * 60)
    assert isinstance(w4.conn.fatal, AcpTransportError)
    w5 = Wire(max_messages=2).start()
    for _ in range(3):
        w5.agent({"jsonrpc": "2.0", "method": "n"})
    assert isinstance(w5.conn.fatal, AcpTransportError)


@pytest.mark.asyncio
async def test_message_queue_is_bounded_without_dropping_silently():
    w = Wire(max_queue=2)
    for _ in range(3):
        w.agent({"jsonrpc": "2.0", "method": "n"})  # dispatcher not started yet: the third overflows
    assert isinstance(w.conn.fatal, AcpTransportError) and "overflow" in w.conn.fatal.message


@pytest.mark.asyncio
async def test_updates_are_routed_by_session_and_replays_and_strangers_dropped_in_order():
    got = []
    w = Wire().start()

    async def one(update, params):
        got.append(("s1", update["n"]))

    async def two(update, params):
        got.append(("s2", update["n"]))

    w.conn.bind_session("s1", one)
    w.conn.bind_session("s2", two)

    def upd(sid, n, **meta):
        frame = {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {"n": n}}}
        frame["params"].update(meta)
        w.agent(frame)

    upd("s1", 1)
    upd("s2", 2)
    upd("nobody", 3)
    upd("s1", 4, _meta={"isReplay": True})
    w.conn.feed((json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": "s2", "update": {"n": 5, "_meta": {"isReplay": True}}}}) + "\n").encode())
    upd("s1", 6)
    t = asyncio.create_task(w.conn.request("r", {}))
    await settle()
    w.agent({"jsonrpc": "2.0", "id": w.out[0]["id"], "result": {}})
    await t
    assert got == [("s1", 1), ("s2", 2), ("s1", 6)] and w.conn.replay_dropped == 2 and w.conn.unrouted_dropped == 1
    w.conn.unbind_session("s1")
    upd("s1", 7)
    await settle()
    assert w.conn.unrouted_dropped == 2
    await w.conn.close()


@pytest.mark.asyncio
async def test_server_requests_get_default_method_not_found_or_the_handler_result():
    w = Wire().start()
    w.agent({"jsonrpc": "2.0", "id": "x-1", "method": "fs/read_text_file", "params": {}})
    await settle(10)
    assert w.out[0] == {"jsonrpc": "2.0", "id": "x-1", "error": {"code": -32601, "message": "Method not supported by client"}}
    await w.conn.close()

    async def handler(method, params, rid):
        return "result", {"echo": method, "rid": rid}

    w = Wire(on_request=handler).start()
    w.agent({"jsonrpc": "2.0", "id": 7, "method": "session/request_permission", "params": {}})
    await settle(10)
    assert w.out[0] == {"jsonrpc": "2.0", "id": 7, "result": {"echo": "session/request_permission", "rid": 7}}
    w.agent({"jsonrpc": "2.0", "id": [1], "method": "bad/id"})  # a non-scalar id is a violation
    await settle(10)
    assert isinstance(w.conn.fatal, AcpTransportError)


@pytest.mark.asyncio
async def test_eof_fails_pending_and_a_handler_exception_fails_the_connection_with_that_exception():
    w = Wire().start()
    t = asyncio.create_task(w.conn.request("a", {}))
    await settle()
    w.conn.feed(None)
    with pytest.raises(AcpTransportError) as info:
        await t
    assert info.value.kind == "eof"

    class Boom(Exception):
        pass

    async def bad(update, params):
        raise Boom()

    w = Wire().start()
    w.conn.bind_session("s", bad)
    t = asyncio.create_task(w.conn.request("a", {}))
    await settle()
    w.agent({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s", "update": {}}})
    with pytest.raises(Boom):
        await t


@pytest.mark.asyncio
async def test_before_write_can_veto_a_frame_and_a_stuck_pipe_fails_the_connection():
    w = Wire().start()

    def veto():
        raise RuntimeError("stopped")

    with pytest.raises(RuntimeError):
        await w.conn.request("session/prompt", {}, before_write=veto)
    assert w.out == [] and not w.conn._pending  # nothing was written, nothing leaked
    await w.conn.close()
    w = Wire(write_timeout=0.05)
    w.block = asyncio.Event()  # the pipe never drains
    w.start()
    with pytest.raises(AcpTransportError) as info:
        await w.conn.notify("session/cancel", {})
    assert info.value.kind == "write"
    with pytest.raises(AcpTransportError):
        await w.conn.request("later", {})


@pytest.mark.asyncio
async def test_writes_are_serialised_and_whole_lines():
    w = Wire().start()
    await asyncio.gather(*(w.conn.notify("n", {"i": i, "pad": "é" * 50}) for i in range(20)))
    assert [m["params"]["i"] for m in w.out] == list(range(20))
    await w.conn.close()


def test_capability_probe_never_assumes_and_model_config_lookup():
    assert not parse_capabilities({}).resume and not parse_capabilities({"agentCapabilities": "x"}).load_session
    caps = parse_capabilities({"agentCapabilities": {"loadSession": True, "sessionCapabilities": {"resume": {}}}})
    assert caps.load_session and caps.resume and not caps.close
    caps = parse_capabilities({"agentCapabilities": {"loadSession": "yes", "sessionCapabilities": {"resume": False, "close": {}}}})
    assert not caps.load_session and not caps.resume and caps.close
    options = [{"id": "model", "category": "model", "options": [{"group": "g", "options": [{"value": "m1"}]}, {"value": "m2"}]},
               {"id": "mode", "category": "mode", "options": [{"value": "m1"}]}]
    assert find_model_config(options, "m1") == "model" and find_model_config(options, "m2") == "model"
    assert find_model_config(options, "m3") is None and find_model_config("nope", "m1") is None


@pytest.mark.asyncio
async def test_a_timed_out_request_tombstones_its_id_and_one_late_response_is_ignored():
    w = Wire().start()
    with pytest.raises(AcpTimeout):
        await w.conn.request("slow", {}, timeout=0.01)
    late_id = w.out[0]["id"]
    w.agent({"jsonrpc": "2.0", "id": late_id, "result": {}})  # the late answer: ignored, connection still healthy
    await settle(10)
    assert w.conn.fatal is None
    t = asyncio.create_task(w.conn.request("next", {}))
    await settle()
    w.agent({"jsonrpc": "2.0", "id": w.out[-1]["id"], "result": {"v": 1}})
    assert await t == {"v": 1}
    w.agent({"jsonrpc": "2.0", "id": late_id, "result": {}})  # a SECOND frame for the same id is a violation again
    await settle(10)
    assert isinstance(w.conn.fatal, AcpTransportError)
    await w.conn.close()


@pytest.mark.asyncio
async def test_after_callback_runs_only_after_the_reply_frame_was_written():
    order = []
    w = Wire()
    orig = w.write

    async def write(data):
        await orig(data)
        order.append("write")

    async def handler(method, params, rid):
        async def after():
            order.append("after")
        return "result", {"outcome": "cancelled"}, after

    w.conn = AcpConnection(on_request=handler)
    w.conn.start(write)
    w.agent({"jsonrpc": "2.0", "id": "p-1", "method": "session/request_permission", "params": {}})
    await settle(10)
    assert order == ["write", "after"] and w.out[0]["result"] == {"outcome": "cancelled"}
    await w.conn.close()
