"""Chat defects: timestamp shape, CEO-history import, and profile-bound conversations."""

import asyncio
from datetime import datetime

import pytest

from .helpers import Api
from .test_runs import FakeRuntime, wait_status


def _utc(value: str) -> bool:
    parsed = datetime.fromisoformat(value)
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_conversation_and_message_timestamps_carry_a_utc_offset(api):
    conv = await api("conversations.create", title="t")
    msg = await api("conversations.add_message", conversation_id=conv["id"], role="user", content="hi")
    listed = (await api("conversations.messages", conversation_id=conv["id"]))["messages"]
    stamps = [conv["created_at"], conv["updated_at"], msg["created_at"], listed[0]["created_at"],
              (await api("conversations.list"))["conversations"][0]["updated_at"]]
    assert all(_utc(s) for s in stamps), stamps


@pytest.mark.asyncio
async def test_import_ceo_copies_old_exchanges_once_and_skips_current_ones(make_app):
    api = Api(make_app(specialists=False))
    db = api.db
    for seq, (role, text, when) in enumerate([("user", "old q", "2020-01-01 10:00:00.000000"),
                                                ("assistant", "old a", "2020-01-01 10:00:05.000000")], 1):
        db.insert("ceo_messages", {"id": f"old{seq}", "seq": seq, "role": role, "content": text,
                                   "fast": 0, "created_at": when})
    assert (await api("conversations.import_ceo"))["imported"] == 2
    # a scoped exchange lives in its own chat only: it is never written to the company log nor imported
    chat = (await api("conversations.create", title="own"))["id"]
    await api("ceo.ask", question="scoped hello", conversation_id=chat)
    assert db.scalar("SELECT COUNT(*) FROM ceo_messages") == 2
    assert (await api("conversations.import_ceo"))["imported"] == 0
    # an unscoped exchange made after the first import is imported, exactly once
    await api("ceo.ask", question="unscoped hello")
    assert (await api("conversations.import_ceo"))["imported"] == 2
    assert (await api("conversations.import_ceo"))["imported"] == 0
    texts = [m["content"] for m in (await api("conversations.messages", conversation_id="legacy-ceo-history"))["messages"]]
    assert texts[:2] == ["old q", "old a"] and texts[2] == "unscoped hello" and len(texts) == 4
    assert "scoped hello" not in texts[2:3] and all("scoped hello" != t for t in texts)


@pytest.mark.asyncio
async def test_profile_bound_conversation_applies_current_instructions_on_every_turn(make_app):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda engine: rt, mode="real"))
    profile = await api("profiles.create", name="Pat", role_type="custom", description="d",
                        instructions="FIRST-INSTRUCTIONS")
    conv = await api("conversations.create", title="c", profile_id=profile["id"])
    assert conv["profile_id"] == profile["id"]
    for i in range(14):  # 28 stored messages: the first one is far outside the 20-message window
        run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt=f"q{i}")
        await wait_status(api, run["id"], "succeeded")
    assert "FIRST-INSTRUCTIONS" in rt.calls[-1].prompt
    await api("profiles.update", profile_id=profile["id"], instructions="SECOND-INSTRUCTIONS")
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt="last")
    await wait_status(api, run["id"], "succeeded")
    assert "SECOND-INSTRUCTIONS" in rt.calls[-1].prompt and "FIRST-INSTRUCTIONS" not in rt.calls[-1].prompt


@pytest.mark.asyncio
async def test_scoped_ask_is_durable_before_generation_and_idempotent(make_app):
    from .test_ceo import Recorder
    rt = Recorder()
    api = Api(make_app(runtime=rt))
    chat = (await api("conversations.create", title="c"))["id"]
    seen = []
    original = rt.execute_task

    async def spy(task):
        seen.append([m["content"] for m in api.db.all(
            "SELECT content FROM conversation_messages WHERE conversation_id=? ORDER BY seq", (chat,))])
        return await original(task)

    rt.execute_task = spy
    out = await api("ceo.ask", question="hello", conversation_id=chat, request_id="req-a")
    assert seen == [["hello"]]  # the question was already stored while the model ran
    msgs = (await api("conversations.messages", conversation_id=chat))["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello"), ("assistant", out["reply"])]
    # the same request id again does not duplicate the user message, and the question is not in its own history
    await api("ceo.ask", question="hello", conversation_id=chat, request_id="req-a")
    roles = [m["role"] for m in (await api("conversations.messages", conversation_id=chat))["messages"]]
    assert roles == ["user", "assistant"]
    await api("ceo.ask", question="second", conversation_id=chat, request_id="req-b")
    assert rt.prompts[-1].prompt.count("second") == 1 and "- User: hello" in rt.prompts[-1].prompt


@pytest.mark.asyncio
async def test_failed_scoped_ask_leaves_no_orphan_question(make_app):
    from .test_ceo import Recorder
    rt = Recorder()
    api = Api(make_app(runtime=rt))
    chat = (await api("conversations.create", title="c"))["id"]

    async def boom(task):
        raise RuntimeError("model down")

    rt.execute_task = boom
    assert (await api.fail("ceo.ask", question="hello", conversation_id=chat, request_id="r1")).status == 502
    assert (await api("conversations.messages", conversation_id=chat))["messages"] == []


@pytest.mark.asyncio
async def test_bind_profile_gives_an_unbound_conversation_its_instructions(make_app):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda engine: rt, mode="real"))
    profile = await api("profiles.create", name="Pat", role_type="custom", description="d", instructions="PAT-RULES")
    other = await api("profiles.create", name="Sam", role_type="custom", description="d", instructions="SAM-RULES")
    conv = await api("conversations.create", title="old chat")  # made before chats were bound
    assert conv["profile_id"] is None
    assert (await api.fail("conversations.bind_profile", conversation_id=conv["id"], profile_id="nope")).status == 404
    assert (await api.fail("conversations.bind_profile", conversation_id="nope", profile_id=profile["id"])).status == 404
    bound = await api("conversations.bind_profile", conversation_id=conv["id"], profile_id=profile["id"])
    assert bound["profile_id"] == profile["id"]
    await api("conversations.bind_profile", conversation_id=conv["id"], profile_id=profile["id"])  # idempotent
    assert (await api.fail("conversations.bind_profile", conversation_id=conv["id"], profile_id=other["id"])).status == 409
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="codex", model_id="m", prompt="hi")
    await wait_status(api, run["id"], "succeeded")
    assert "PAT-RULES" in rt.calls[-1].prompt
