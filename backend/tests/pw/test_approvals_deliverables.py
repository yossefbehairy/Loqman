"""Approvals (state machine only) and reviewable deliverable drafts."""

import asyncio
import hashlib

import pytest

from backend.pyworker.core.approvals import RunNotActive

from .helpers import Api
from .test_runs import FakeRuntime, new_conv, wait_status


@pytest.fixture
def rig(make_app):
    rt = FakeRuntime()
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    return api, rt


async def active_run(api, rt):
    cid = await new_conv(api)
    run = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="p")
    await rt.started.wait()
    return run["id"]


@pytest.mark.asyncio
async def test_capabilities_match_enforced_workspace_approvals(api):
    caps = await api("approvals.capabilities")
    assert set(caps) == {"opencode", "claude-code", "codex", "antigravity"}
    assert caps["claude-code"]["tool_approvals"] is True
    assert caps["claude-code"]["scope"] == "workspace_write"
    assert all(not c["tool_approvals"] for name, c in caps.items() if name != "claude-code")


@pytest.mark.asyncio
async def test_create_decide_once_and_scope(rig):
    api, rt = rig
    run_id = await active_run(api, rt)
    mgr = api.app.ctx.approvals
    ap = mgr.create(run_id, "write file", scope="workspace")
    assert ap["status"] == "pending" and ap["scope"] == "workspace"
    other = mgr.create(run_id, "second")
    assert {a["id"] for a in (await api("approvals.list", run_id=run_id))["approvals"]} == {ap["id"], other["id"]}
    decided = await api("approvals.decide", run_id=run_id, approval_id=ap["id"], decision="approve", note="ok")
    assert decided["status"] == "approved" and decided["decision_note"] == "ok" and decided["decided_at"]
    dup = await api.fail("approvals.decide", run_id=run_id, approval_id=ap["id"], decision="deny")
    assert dup.status == 409 and "approved" in dup.message
    assert (await api.fail("approvals.decide", run_id="wrong", approval_id=other["id"],
                           decision="approve")).status == 404  # scoped to its own run
    assert (await api.fail("approvals.decide", run_id=run_id, approval_id="nope", decision="approve")).status == 404
    assert (await api.fail("approvals.decide", run_id=run_id, approval_id=other["id"],
                           decision="maybe")).status == 422
    assert [a["id"] for a in (await api("approvals.list"))["approvals"]] == [other["id"]]


@pytest.mark.asyncio
async def test_create_refused_for_terminal_or_missing_run(rig):
    api, rt = rig
    mgr = api.app.ctx.approvals
    with pytest.raises(RunNotActive):
        mgr.create("missing", "x")
    run_id = await active_run(api, rt)
    await api("runs.cancel", run_id=run_id)
    with pytest.raises(RunNotActive):
        mgr.create(run_id, "late")


@pytest.mark.asyncio
async def test_pending_approvals_expire_with_their_run(rig):
    api, rt = rig
    run_id = await active_run(api, rt)
    mgr = api.app.ctx.approvals
    ap = mgr.create(run_id, "x")
    await api("runs.cancel", run_id=run_id)
    got = mgr.get(ap["id"])
    assert got["status"] == "expired" and got["expired_reason"] == "run_cancelled"
    assert (await api.fail("approvals.decide", run_id=run_id, approval_id=ap["id"],
                           decision="approve")).status == 409
    assert (await api("approvals.list"))["approvals"] == []


@pytest.mark.asyncio
async def test_approval_times_out(rig):
    api, rt = rig
    run_id = await active_run(api, rt)
    mgr = api.app.ctx.approvals
    ap = mgr.create(run_id, "x", ttl_seconds=1)
    api.db.update("approvals", {"expires_at": "2000-01-01 00:00:00.000000"}, "id=?", (ap["id"],))
    assert (await api("approvals.list"))["approvals"] == []
    got = mgr.get(ap["id"])
    assert got["status"] == "expired" and got["expired_reason"] == "timeout"
    ap2 = mgr.create(run_id, "y")
    api.db.update("approvals", {"expires_at": "2000-01-01 00:00:00.000000"}, "id=?", (ap2["id"],))
    err = await api.fail("approvals.decide", run_id=run_id, approval_id=ap2["id"], decision="approve")
    assert err.status == 409 and "expired" in err.message  # a late decision never lands


# -- deliverables -----------------------------------------------------------------------

async def succeeded_run(api, rt, text="# Report\nbody"):
    rt.gated, rt.text = False, text
    cid = await new_conv(api)
    run = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="p")
    await wait_status(api, run["id"], "succeeded")
    return run["id"]


@pytest.mark.asyncio
async def test_draft_is_a_snapshot_of_the_real_reply(rig):
    api, rt = rig
    run_id = await succeeded_run(api, rt)
    d = await api("deliverables.create", run_id=run_id, filename="../../evil name.md")
    assert d["status"] == "pending_review" and d["filename"] == "evil_name.md" and d["mime"] == "text/markdown"
    assert d["written_to_workspace"] is False and d["downloadable"] is False and d["content"] is None
    assert d["content_sha256"] == hashlib.sha256(b"# Report\nbody").hexdigest() and d["size_bytes"] == 13
    prov = d["provenance"]
    assert prov["model_called_during_draft_creation"] is False and prov["run_id"] == run_id
    assert prov["engine"] == "codex" and prov["source"] == "conversation_run_assistant_message"
    full = await api("deliverables.get", run_id=run_id, draft_id=d["id"])
    assert full["content"] == "# Report\nbody"
    again = await api("deliverables.create", run_id=run_id, filename="../../evil name.md")
    assert again["id"] == d["id"]  # idempotent per (run, hash, filename)
    assert (await api.fail("deliverables.create", run_id=run_id, content="client text")).status == 422  # no client content
    assert [x["id"] for x in (await api("deliverables.list", run_id=run_id))["drafts"]] == [d["id"]]


@pytest.mark.asyncio
async def test_default_and_unsafe_filenames(rig):
    api, rt = rig
    run_id = await succeeded_run(api, rt)
    default = await api("deliverables.create", run_id=run_id)
    assert default["filename"] == f"deliverable-{run_id[:8]}.md"
    for bad in ("x.exe", ".md", "noext", "a/b/c.txt"):
        d = await api("deliverables.create", run_id=run_id, filename=bad)
        assert d["filename"].endswith((".md", ".txt"))
    assert (await api("deliverables.create", run_id=run_id, filename="a/b/c.txt"))["filename"] == "c.txt"


@pytest.mark.asyncio
async def test_decision_is_bound_to_hash_and_happens_once(rig):
    api, rt = rig
    run_id = await succeeded_run(api, rt)
    d = await api("deliverables.create", run_id=run_id)
    assert (await api.fail("deliverables.content", run_id=run_id, draft_id=d["id"])).status == 409  # not approved
    wrong = "0" * 64
    err = await api.fail("deliverables.decide", run_id=run_id, draft_id=d["id"], decision="approve",
                         content_sha256=wrong)
    assert err.status == 409 and "does not match" in err.message
    assert (await api.fail("deliverables.decide", run_id=run_id, draft_id=d["id"], decision="approve",
                           content_sha256="xyz")).status == 422
    ok = await api("deliverables.decide", run_id=run_id, draft_id=d["id"], decision="approve",
                   content_sha256=d["content_sha256"].upper(), note="fine")
    assert ok["status"] == "approved" and ok["downloadable"] is True and ok["decision_note"] == "fine"
    err = await api.fail("deliverables.decide", run_id=run_id, draft_id=d["id"], decision="reject",
                         content_sha256=d["content_sha256"])
    assert err.status == 409 and "already decided" in err.message.lower()
    dl = await api("deliverables.content", run_id=run_id, draft_id=d["id"])
    assert dl["content"] == "# Report\nbody" and dl["media_type"].startswith("text/markdown")
    assert dl["headers"]["Content-Security-Policy"] == "sandbox" and dl["content_sha256"] == d["content_sha256"]
    assert (await api.fail("deliverables.get", run_id="other", draft_id=d["id"])).status == 404  # run-scoped


@pytest.mark.asyncio
async def test_download_refuses_tampered_content(rig):
    api, rt = rig
    run_id = await succeeded_run(api, rt)
    d = await api("deliverables.create", run_id=run_id)
    await api("deliverables.decide", run_id=run_id, draft_id=d["id"], decision="approve",
              content_sha256=d["content_sha256"])
    api.db.update("deliverable_drafts", {"content": "TAMPERED"}, "id=?", (d["id"],))
    err = await api.fail("deliverables.content", run_id=run_id, draft_id=d["id"])
    assert err.status == 500 and "integrity" in err.message


@pytest.mark.asyncio
async def test_run_must_have_succeeded_with_a_reply(rig):
    api, rt = rig
    run_id = await active_run(api, rt)
    err = await api.fail("deliverables.create", run_id=run_id)
    assert err.status == 409 and "running" in err.message
    assert (await api.fail("deliverables.create", run_id="missing")).status == 404
    assert (await api.fail("deliverables.list", run_id="missing")).status == 404
    rt.gate.set()
    await wait_status(api, run_id, "succeeded")


@pytest.mark.asyncio
async def test_empty_oversized_and_too_many(rig):
    api, rt = rig
    run_id = await succeeded_run(api, rt, text="   ")
    assert (await api.fail("deliverables.create", run_id=run_id)).status == 422
    big = await succeeded_run(api, rt, text="x" * (256 * 1024 + 1))
    assert (await api.fail("deliverables.create", run_id=big)).status == 413
    many = await succeeded_run(api, rt, text="ok")
    for i in range(10):
        await api("deliverables.create", run_id=many, filename=f"f{i}.txt")
    assert (await api.fail("deliverables.create", run_id=many, filename="one-too-many.txt")).status == 429


@pytest.mark.asyncio
async def test_runs_and_drafts_are_listable_without_a_client_side_index(rig):
    api, rt = rig
    rt.gated, rt.text = False, "reply one"
    cid = await new_conv(api)
    first = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="one")
    await wait_status(api, first["id"], "succeeded")
    rt.text = "reply two"
    second = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="two")
    await wait_status(api, second["id"], "succeeded")
    other = await new_conv(api)
    elsewhere = await api("runs.start_turn", conversation_id=other, engine="codex", model_id="m", prompt="x")
    await wait_status(api, elsewhere["id"], "succeeded")

    runs = (await api("runs.list", conversation_id=cid))["runs"]
    assert [r["id"] for r in runs] == [first["id"], second["id"]]  # oldest first, this conversation only
    assert runs[1]["assistant_content"] == "reply two" and runs[0]["assistant_message_seq"] is not None
    assert [r["id"] for r in (await api("runs.list", conversation_id=cid, limit=1))["runs"]] == [second["id"]]
    assert (await api.fail("runs.list", conversation_id="missing")).status == 404

    d1 = await api("deliverables.create", run_id=first["id"])
    d2 = await api("deliverables.create", run_id=elsewhere["id"])
    listed = (await api("deliverables.list_all"))["drafts"]
    assert {d["id"] for d in listed} == {d1["id"], d2["id"]}
    by_id = {d["id"]: d for d in listed}
    assert by_id[d1["id"]]["conversation_id"] == cid and by_id[d1["id"]]["run_id"] == first["id"]
    assert by_id[d2["id"]]["conversation_id"] == other and by_id[d1["id"]]["content"] is None
    assert len((await api("deliverables.list_all", limit=1))["drafts"]) == 1


@pytest.mark.asyncio
async def test_list_all_drafts_pages_with_an_exclusive_cursor(rig):
    api, rt = rig
    run = await succeeded_run(api, rt, text="ok")
    made = [(await api("deliverables.create", run_id=run, filename=f"f{i}.txt"))["id"] for i in range(5)]
    # identical timestamps: the id tie-break must keep the order stable and the cursor exclusive
    api.db.execute("UPDATE deliverable_drafts SET created_at='2030-01-01 00:00:00.000000'")
    seen, cursor, pages = [], None, 0
    while True:
        out = await api("deliverables.list_all", limit=2, **({"cursor": cursor} if cursor else {}))
        pages += 1
        seen += [d["id"] for d in out["drafts"]]
        cursor = out["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and sorted(seen) == sorted(made) and len(set(seen)) == 5
    assert seen == sorted(made, reverse=True)
    assert (await api.fail("deliverables.list_all", cursor="garbage")).status == 422
    assert (await api("deliverables.list_all", limit=500))["next_cursor"] is None
