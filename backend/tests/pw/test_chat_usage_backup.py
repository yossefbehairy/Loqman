"""Consultation chat, OpenCode session mapping + SDK proxy rules, models/health,
usage ledger and backup/restore."""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from backend.pyworker.core import backup as backup_mod
from backend.pyworker.runtime import ledger
from backend.pyworker.runtime.base import AgentResult, ExecuteAgentTask
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api

MODEL = "muse-spark-1.3-contributor-free"


# -- consultation ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consultation_uses_persona_thread_and_records_usage(make_app):
    seen = []

    class Rec(MockRuntime):
        async def execute_task(self, task):
            seen.append(task)
            return await super().execute_task(task)

    api = Api(make_app(runtime=Rec()))
    out = await api("chat.consult", manager_id="planner", message="Plan the sprint",
                    history=[{"role": "user", "content": "earlier"}])
    assert out["manager_id"] == "planner" and out["suggested_mission"] is None and out["reply"]
    assert "Sara" in out["manager_name"]
    task = seen[0]
    assert task.session_title == "consult-planner" and task.agent_id == "agentos-chat"
    assert "Plan the sprint" in task.prompt and "- user: earlier" in task.prompt and "no mission talk" in task.prompt
    ev = (await api("usage.events"))["events"][0]
    assert ev["kind"] == "chat_consult" and ev["success"] is True
    # default manager is the CEO; the path form resolves aliases
    ceo = await api("chat.consult", message="hi")
    assert ceo["manager_id"] == "general_manager"
    by_alias = await api("managers.consult", manager_id="dr_sara", message="hi")
    assert by_alias["manager_id"] == "dr_sara"


@pytest.mark.asyncio
async def test_consultation_errors_and_ad_hoc_agent(api):
    err = await api.fail("managers.consult", manager_id="ghost", message="hi")
    assert err.status == 404
    adhoc = await api("managers.consult", manager_id="ghost", message="hi", agent_name="Zed", agent_role="Tester",
                      agent_instructions="Be terse", model_id=MODEL, variant="high")
    assert adhoc["manager_name"] == "Zed" and adhoc["title"] == "Tester"
    assert (await api.fail("chat.consult", message="")).status == 422
    assert (await api.fail("chat.consult", message="x", history=[{"role": "user"}])).status == 422


@pytest.mark.asyncio
async def test_consultation_unwraps_json_replies(make_app):
    class Json(MockRuntime):
        async def execute_task(self, task):
            return AgentResult(text='```json\n{"reply": "wrapped"}\n```', model_id=task.model_id)

    api = Api(make_app(runtime=Json()))
    assert (await api("chat.consult", manager_id="planner", message="x"))["reply"] == "wrapped"


# -- OpenCode session mapping ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_mapping_roundtrip_and_rules(api):
    assert (await api("chat.session.get", conversation_id="c1"))["session"] is None
    saved = await api("chat.session.save", conversation_id="c1", session_id="s1", model_id=MODEL,
                      provider_id="opencode")
    assert saved["agent_id"] == "agentos-chat"
    assert (await api("chat.session.get", conversation_id="c1"))["session"]["session_id"] == "s1"
    again = await api("chat.session.save", conversation_id="c1", session_id="s1", model_id=MODEL,
                      provider_id="opencode", agent_id="agentos-worker")
    assert again["agent_id"] == "agentos-worker"
    # the conversation cannot be re-pointed at another session while the agent is unchanged
    err = await api.fail("chat.session.save", conversation_id="c1", session_id="other", model_id=MODEL,
                         provider_id="opencode", agent_id="agentos-worker")
    assert err.status == 409
    # the unrestricted legacy agent is never accepted
    assert (await api.fail("chat.session.save", conversation_id="c2", session_id="s", model_id=MODEL,
                           provider_id="opencode", agent_id="build")).status == 400
    assert (await api.fail("chat.session.save", conversation_id="c2", session_id="s", model_id="paid-x",
                           provider_id="opencode")).status == 400
    assert (await api.fail("chat.session.save", conversation_id="c2", session_id="s", model_id=MODEL,
                           provider_id="not-a-free-provider")).status == 400


@pytest.mark.asyncio
async def test_legacy_unsafe_agent_session_is_repointed_not_reused(api):
    api.db.insert("opencode_conversations", {
        "conversation_id": "old", "session_id": "unsafe-session", "model_id": MODEL, "provider_id": "opencode",
        "agent_id": "build", "created_at": "2026-01-01 00:00:00.000000", "updated_at": "2026-01-01 00:00:00.000000"})
    out = await api("chat.session.save", conversation_id="old", session_id="safe-session", model_id=MODEL,
                    provider_id="opencode", agent_id="agentos-chat")
    assert out["session_id"] == "safe-session" and out["agent_id"] == "agentos-chat"


class SessionRuntime(MockRuntime):
    def __init__(self):
        super().__init__()
        self.calls, self.fail = [], False

    async def request_api(self, method, path, data=None, timeout=180):
        if self.fail:
            raise RuntimeError("OpenCode disconnected: sk-SECRET")
        self.calls.append((method, path, data))
        return {"data": {"id": "session-1", "echo": path}}


@pytest.mark.asyncio
async def test_proxy_allows_only_session_routes_and_checks_models(make_app):
    rt = SessionRuntime()
    api = Api(make_app(runtime=rt))
    created = await api("chat.opencode_proxy", method="POST", path="session", body={
        "title": "t", "agent": "agentos-chat", "model": {"id": MODEL, "providerID": "opencode", "variant": "high"}})
    assert created["data"]["data"]["id"] == "session-1" and rt.calls[-1][1] == "/api/session"
    msg = await api("chat.opencode_proxy", method="POST", path="session/abc-1/message", query="x=1", body={
        "model": {"providerID": "opencode", "modelID": MODEL}, "variant": "high", "parts": []})
    assert rt.calls[-1][1] == "/api/session/abc-1/message?x=1" and msg["data"]
    assert (await api("chat.opencode_proxy", method="GET", path="session/abc-1/message"))["data"]
    for path in ("global/config", "session/a/b/c", "session/../etc", "auth", "file", "session//message"):
        assert (await api.fail("chat.opencode_proxy", method="GET", path=path)).status == 404, path
    # a model that is not verified free is refused before any call is made
    n = len(rt.calls)
    assert (await api.fail("chat.opencode_proxy", method="POST", path="session",
                           body={"model": {"id": "paid", "providerID": "opencode"}})).status == 400
    assert (await api.fail("chat.opencode_proxy", method="POST", path="session",
                           body={"model": {"id": MODEL, "providerID": "opencode", "variant": "nope"}})).status == 400
    assert (await api.fail("chat.opencode_proxy", method="POST", path="session/a/message",
                           body={"model": {"providerID": "opencode", "modelID": "paid"}})).status == 400
    assert len(rt.calls) == n
    assert (await api.fail("chat.opencode_proxy", method="DELETE", path="session")).status == 422


@pytest.mark.asyncio
async def test_proxy_failure_is_a_clean_502_without_leaks(make_app):
    rt = SessionRuntime()
    rt.fail = True
    api = Api(make_app(runtime=rt))
    err = await api.fail("chat.opencode_proxy", method="POST", path="session",
                         body={"model": {"id": MODEL, "providerID": "opencode"}})
    assert err.status == 502 and "OpenCode request failed" in err.message and "SECRET" not in json.dumps(err.to_dict())
    plain = Api(make_app(runtime=MockRuntime()))  # runtime without request_api
    assert (await plain.fail("chat.opencode_proxy", method="GET", path="session")).status == 502


# -- models / health / engines -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_models_and_engine_endpoints(api):
    assert (await api("health"))["status"] == "ok"
    assert (await api("app.info"))["status"] == "online"
    rh = await api("runtime.health")
    assert rh["status"] and rh["models_count"] >= rh["free_models_count"] > 0
    free = (await api("models.free"))["models"]
    assert all(m["is_free"] and m["input_cost"] == 0 and m["output_cost"] == 0 for m in free)
    assert {m["id"] for m in free} == {m["id"] for m in (await api("models.refresh"))["models"]}
    setup = await api("engines.setup", engine_id="codex")
    assert setup["command"] == ["codex", "login"] and setup["credentials_handled_by"] == "official_cli"
    assert (await api("engines.setup", engine_id="antigravity"))["login_method"] == "external_cli"
    assert (await api.fail("engines.setup", engine_id="gemini")).status == 404
    assert (await api.fail("engines.models", engine_id="gemini")).status == 404
    assert (await api.fail("engines.check", engine_id="gemini")).status == 404


@pytest.mark.asyncio
async def test_engine_check_and_models_use_the_engine_factory(make_app, monkeypatch):
    from backend.pyworker.runtime import engine_discovery as ed
    from types import SimpleNamespace

    monkeypatch.setattr(ed, "probe_engine", lambda spec: SimpleNamespace(
        engine_id=spec.engine_id, display_name="X", install_state="installed", auth_state="unknown",
        version="1.0", executable="/x", adapter_supported=True, detail="d"))

    class Eng(MockRuntime):
        async def list_available_models(self, free_only=True):
            return await super().list_available_models(free_only=False)

    api = Api(make_app(runtime_factory=lambda e: Eng(), mode="real"))
    check = await api("engines.check", engine_id="codex")
    assert check["discovery"]["install_state"] == "installed" and check["runtime"]["status"]
    assert check["generation_verified"] is False and check["billing_state"] == "unknown"
    assert (await api("engines.models", engine_id="codex"))["models"]


# -- usage ledger ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_records_success_failure_cancellation_and_never_breaks(api):
    rt = MockRuntime()
    task = ExecuteAgentTask(prompt="hello", model_id=MODEL, session_title="t")
    res = await ledger.run_agent_task(rt, task, kind="chat_consult", run_id="r1")
    assert res.text
    row = api.db.one("SELECT * FROM usage_events WHERE run_id='r1'")
    assert row["success"] == 1 and row["kind"] == "chat_consult" and row["model_id"] == MODEL
    assert row["agent_id"] == "agentos-worker" and row["tokens_in"] is not None

    class Boom(MockRuntime):
        async def execute_task(self, task):
            raise RuntimeError("secret sk-123 in provider text")

    with pytest.raises(RuntimeError):
        await ledger.run_agent_task(Boom(), task, kind="workflow_node")
    fail = api.db.one("SELECT * FROM usage_events WHERE kind='workflow_node'")
    assert fail["success"] == 0 and fail["error_kind"] == "RuntimeError"
    assert "sk-123" not in (fail["error_detail"] or "")

    class Slow(MockRuntime):
        async def execute_task(self, task):
            await asyncio.sleep(30)

    t = asyncio.create_task(ledger.run_agent_task(Slow(), task, kind="mission_delegate"))
    await asyncio.sleep(0.02)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert api.db.one("SELECT * FROM usage_events WHERE kind='mission_delegate'")["error_kind"] == "CancelledError"

    # a broken ledger never breaks the task it observes
    api.db.execute("DROP TABLE usage_events")
    assert (await ledger.run_agent_task(rt, task, kind="x")).text


def test_token_mapping_never_invents_usage():
    f = ledger._tokens_in_out
    assert f({"input": 5, "output": 7, "cache": {"read": 11}}) == (5, 7, 11)
    assert f({"input_tokens": "3", "output_tokens": 4, "cache_read_input_tokens": 9}) == (3, 4, 9)
    assert f({"weird": 1}) == (None, None, None) and f(None) == (None, None, None)


@pytest.mark.asyncio
async def test_usage_summary_and_filters(api):
    base = {"agent_id": "a", "provider_id": "opencode", "duration_ms": 10, "created_at": "2026-01-01 00:00:00.000000"}
    rows = [("e1", "chat_consult", "m1", 1, 10, 20, 5), ("e2", "chat_consult", "m1", 0, 30, None, None),
            ("e3", "ceo_ask", "m2", 1, 50, 60, 0)]
    for i, (eid, kind, model, ok, tin, tout, cache) in enumerate(rows):
        api.db.insert("usage_events", {**base, "id": eid, "kind": kind, "model_id": model, "success": ok,
                                       "tokens_in": tin, "tokens_out": tout, "tokens_cache_read": cache,
                                       "duration_ms": 10 * (i + 1)})
    s = await api("usage.summary")
    t = s["totals"]
    assert (t["events"], t["succeeded"], t["failed"], t["total_duration_ms"], t["avg_duration_ms"]) == (3, 2, 1, 60, 20)
    assert (t["tokens_in"], t["tokens_out"], t["tokens_cache_read"], t["token_usage_unknown_events"]) == (90, 80, 5, 1)
    by_model = {g["key"]: g for g in s["by_model"]}
    assert by_model["m1"]["events"] == 2 and by_model["m1"]["failed"] == 1 and by_model["m2"]["tokens_in"] == 50
    assert s["by_kind"][0]["key"] == "chat_consult"
    assert {e["id"] for e in (await api("usage.events", kind="ceo_ask"))["events"]} == {"e3"}
    assert {e["id"] for e in (await api("usage.events", success=False))["events"]} == {"e2"}
    assert {e["id"] for e in (await api("usage.events", model_id="m1", success=True))["events"]} == {"e1"}
    assert len((await api("usage.events", limit=2))["events"]) == 2
    assert (await api.fail("usage.events", limit=0)).status == 422
    empty = Api  # noqa: F841


# -- backup / restore -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backup_includes_wal_data_and_cleans_up(api):
    api.db.insert("conversations", {"id": "c-backup", "title": "hello", "created_at": "2026-01-01 00:00:00.000000",
                                    "updated_at": "2026-01-01 00:00:00.000000"})
    out = await api("backup.create")
    path = Path(out["path"])
    assert out["name"].startswith("agentos-backup-") and out["name"].endswith(".db") and out["size"] > 0
    assert out["media_type"] == "application/vnd.sqlite3"
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT title FROM conversations WHERE id='c-backup'").fetchone() == ("hello",)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # self-contained file
    finally:
        con.close()
    assert path.exists()
    await api("backup.cleanup", path=str(path))
    assert not path.parent.exists()
    await api("backup.cleanup", path=str(path))  # idempotent


@pytest.mark.asyncio
async def test_backup_too_large_busy_and_errors_are_sanitised(api, monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # observe leftovers
    with pytest.raises(backup_mod.BackupTooLarge):
        backup_mod.create_backup(api.db, max_bytes=1)
    assert not list(tmp_path.glob("agentos-backup-*"))  # failed attempt leaves nothing behind

    real = backup_mod.create_backup
    monkeypatch.setattr(backup_mod, "create_backup",
                        lambda db: (_ for _ in ()).throw(backup_mod.BackupTooLarge("big")))
    assert (await api.fail("backup.create")).status == 413
    monkeypatch.setattr(backup_mod, "create_backup", real)

    assert backup_mod._backup_lock.acquire(blocking=False)
    try:
        assert (await api.fail("backup.create")).status == 409
    finally:
        backup_mod._backup_lock.release()

    def exploding_connect(*a, **k):
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", exploding_connect)
    err = await api.fail("backup.create")
    assert err.status == 500 and err.message == "Backup failed" and "secret" not in json.dumps(err.to_dict())
    assert not list(tmp_path.glob("agentos-backup-*"))
    monkeypatch.undo()
    # the lock was released by the failed attempt: a new backup works
    ok = await api("backup.create")
    await api("backup.cleanup", path=ok["path"])


def test_restore_roundtrip_and_pre_restore_backup(tmp_path):
    from backend.pyworker.db.core import Database
    from backend.pyworker.db.migrate import init_db

    live = tmp_path / "live.db"
    db = Database(str(live))
    init_db(db)
    db.insert("conversations", {"id": "keep", "title": "original", "created_at": "2026-01-01 00:00:00.000000",
                                "updated_at": "2026-01-01 00:00:00.000000"})
    path, _name = backup_mod.create_backup(db)
    db.update("conversations", {"title": "changed"}, "id='keep'")
    db.close()
    pre = backup_mod.restore_database(path, live)
    con = sqlite3.connect(live)
    assert con.execute("SELECT title FROM conversations").fetchone() == ("original",)
    con.close()
    pre_con = sqlite3.connect(pre)
    assert pre_con.execute("SELECT title FROM conversations").fetchone() == ("changed",)
    pre_con.close()
    backup_mod.cleanup_backup(path)


def test_restore_refuses_bad_backups_and_same_file(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all")
    target = tmp_path / "t.db"
    sqlite3.connect(target).close()
    with pytest.raises(backup_mod.RestoreError):
        backup_mod.restore_database(bad, target)
    with pytest.raises(backup_mod.RestoreError, match="not found"):
        backup_mod.restore_database(tmp_path / "missing.db", target)
    future = tmp_path / "future.db"
    con = sqlite3.connect(future)
    con.execute("PRAGMA user_version=99")
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    with pytest.raises(backup_mod.RestoreError, match="Unsupported schema"):
        backup_mod.restore_database(future, target)
