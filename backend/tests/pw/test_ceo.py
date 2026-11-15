"""CEO control plane: ask loop, fast path, memory, proposals and approval gating."""

import json

import pytest

from backend.pyworker.runtime.base import AgentResult
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api


class Recorder(MockRuntime):
    def __init__(self, scripted=None):
        super().__init__()
        self.prompts, self.scripted = [], list(scripted or [])

    async def execute_task(self, task):
        self.prompts.append(task)
        if self.scripted:
            return AgentResult(text=self.scripted.pop(0), model_id=task.model_id)
        return await super().execute_task(task)


MODEL = "deepseek-v4.1-flash-free"


@pytest.mark.asyncio
async def test_read_tool_loop_answers_from_live_data(api):
    m = await api("missions.create", objective="hi there, some real objective")
    out = await api("ceo.ask", question="show me the missions status")
    assert out["fast"] is False and out["iterations"] == 2 and out["actions_proposed"] == []
    assert "Based on live company data" in out["reply"] and m["id"] in out["reply"]


@pytest.mark.asyncio
async def test_fast_path_is_one_plain_call_without_tools(make_app):
    rt = Recorder()
    api = Api(make_app(runtime=rt))
    out = await api("ceo.ask", question="hello")
    assert out["fast"] is True and out["iterations"] == 1 and out["actions_proposed"] == []
    assert len(rt.prompts) == 1 and rt.prompts[0].variant == "low"
    assert "ceo tools" not in rt.prompts[0].prompt.lower()
    assert rt.prompts[0].agent_id == "agentos-chat" and rt.prompts[0].session_title == "ceo-fast-gm"
    # data-intent words always take the deep loop, even with a greeting
    deep = await api("ceo.ask", question="hello, show me the team")
    assert deep["fast"] is False


@pytest.mark.asyncio
async def test_write_tools_only_record_proposals(api):
    out = await api("ceo.ask", question="عدل سرعة الموظف")
    assert [a["kind"] for a in out["actions_proposed"]] == ["update_contract"]
    pending = (await api("ceo.actions"))["actions"]
    assert [a["id"] for a in pending] == [out["actions_proposed"][0]["id"]] and pending[0]["status"] == "pending"
    planner = await api("profiles.get", profile_id="planner")
    assert planner["default_variant_id"] != "low"  # nothing mutated by the loop itself


@pytest.mark.asyncio
async def test_loop_has_a_hard_iteration_cap(api):
    out = await api("ceo.ask", question="stubborn_loop please")
    assert out["iterations"] == 3 and "reasoning limit" in out["reply"]


@pytest.mark.asyncio
async def test_unknown_tool_and_bad_envelopes_are_observations_not_crashes(make_app):
    rt = Recorder(scripted=["not json", '{"tool_call": {"name": "rm_rf", "args": {}}}',
                            '{"final": "recovered"}'])
    api = Api(make_app(runtime=rt))
    out = await api("ceo.ask", question="Tell me about the quarterly outlook for status")
    assert out["reply"] == "recovered" and out["iterations"] == 3
    assert "ERROR: reply was not a JSON envelope" in rt.prompts[1].prompt
    assert "ERROR: unknown tool 'rm_rf'" in rt.prompts[2].prompt


@pytest.mark.asyncio
async def test_duplicate_write_proposals_are_not_repeated(make_app):
    call = '{"tool_call": {"name": "propose_new_profile", "args": {"name": "N", "role": "researcher", "instructions": "i"}}}'
    rt = Recorder(scripted=[call, call, '{"final": "done"}'])
    api = Api(make_app(runtime=rt))
    out = await api("ceo.ask", question="Please do the status thing")
    assert out["reply"] == "done" and out["actions_proposed"] == []
    assert len(out["agents_created"]) == 1  # the CEO creates agents on its own, once
    assert "already recorded" in rt.prompts[2].prompt


@pytest.mark.asyncio
async def test_memory_is_backend_owned_and_survives_asks(make_app):
    rt = Recorder()
    api = Api(make_app(runtime=rt))
    await api("ceo.ask", question="hello")
    await api("ceo.ask", question="thanks")
    assert "Recent conversation" in rt.prompts[1].prompt and "- User: hello" in rt.prompts[1].prompt
    seqs = [r["seq"] for r in api.db.all("SELECT seq FROM ceo_messages ORDER BY seq")]
    assert seqs == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_separate_chats_do_not_share_ceo_memory(make_app):
    rt = Recorder()
    api = Api(make_app(runtime=rt))
    a = (await api("conversations.create", title="chat A"))["id"]
    b = (await api("conversations.create", title="chat B"))["id"]
    await api("ceo.ask", question="hello from alpha", conversation_id=a)
    await api("conversations.add_message", conversation_id=a, role="assistant", content="ALPHA-REPLY")
    # A new chat starts clean: nothing from chat A is in its prompt, and it has its own runtime thread.
    await api("ceo.ask", question="thanks", conversation_id=b)
    assert "alpha" not in rt.prompts[1].prompt.lower() and "ALPHA-REPLY" not in rt.prompts[1].prompt
    assert "Recent conversation" not in rt.prompts[1].prompt
    assert rt.prompts[1].session_title != rt.prompts[0].session_title
    assert a in rt.prompts[0].session_title and b in rt.prompts[1].session_title
    # Chat A keeps its own memory.
    await api("ceo.ask", question="and again", conversation_id=a)
    assert "- User: hello from alpha" in rt.prompts[2].prompt and "ALPHA-REPLY" in rt.prompts[2].prompt
    assert (await api.fail("ceo.ask", question="hello", conversation_id="missing")).status == 404
    # Without a conversation id the global memory is used, as before.
    await api("ceo.ask", question="hello")
    assert rt.prompts[3].session_title == "ceo-fast-gm"
    assert "alpha" not in rt.prompts[3].prompt.lower()  # scoped chats never reach the company log
    await api("ceo.ask", question="thanks")
    assert "Recent conversation" in rt.prompts[4].prompt and "- User: hello" in rt.prompts[4].prompt


@pytest.mark.asyncio
async def test_ask_validation_and_progress(api):
    assert (await api.fail("ceo.ask", question="")).status == 422
    assert (await api.fail("ceo.ask", question="   ")).status == 400
    assert (await api.fail("ceo.ask", question="hello", model_id="nope")).status == 400
    assert (await api.fail("ceo.ask", question="hello", model_id=MODEL, variant="warp")).status == 400
    assert (await api.fail("ceo.ask_progress", request_id="unknown")).status == 404
    await api("ceo.ask", question="show me missions", request_id="req-1")
    prog = await api("ceo.ask_progress", request_id="req-1")
    assert prog["done"] is True and prog["stage"] == "done" and prog["iteration"] == 2
    assert prog["started_at"] and prog["updated_at"]


@pytest.mark.asyncio
async def test_runtime_failure_is_a_clean_502(make_app):
    class Broken(MockRuntime):
        async def execute_task(self, task):
            raise RuntimeError("provider said: sk-SECRET")

    api = Api(make_app(runtime=Broken()))
    err = await api.fail("ceo.ask", question="show the status data", request_id="r")
    assert err.status == 502 and "SECRET" not in json.dumps(err.to_dict())
    assert (await api("ceo.ask_progress", request_id="r"))["stage"] == "error"


# -- approval gate -------------------------------------------------------------------------

async def proposal(api, question):
    return (await api("ceo.ask", question=question))["actions_proposed"][0]["id"]


@pytest.mark.asyncio
async def test_decisions_require_explicit_confirmation(api):
    aid = await proposal(api, "عدل سرعة الموظف")
    for method in ("ceo.approve", "ceo.deny"):
        assert (await api.fail(method, action_id=aid)).status == 403
        assert (await api.fail(method, action_id=aid, confirm="true")).status == 403  # must be boolean True
    assert (await api.fail("ceo.approve", action_id="nope", confirm=True)).status == 404
    assert (await api("ceo.actions"))["actions"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_approve_update_contract_then_no_double_execution(api):
    aid = await proposal(api, "عدل سرعة الموظف")
    out = await api("ceo.approve", action_id=aid, confirm=True)
    assert out["status"] == "approved" and out["decided_at"]
    assert (await api("profiles.get", profile_id="planner"))["default_variant_id"] == "low"
    assert (await api.fail("ceo.approve", action_id=aid, confirm=True)).status == 409
    assert (await api.fail("ceo.deny", action_id=aid, confirm=True)).status == 409
    assert (await api("ceo.actions"))["actions"] == []
    assert [a["id"] for a in (await api("ceo.actions", status="approved"))["actions"]] == [aid]
    assert (await api.fail("ceo.actions", status="bogus")).status == 400


@pytest.mark.asyncio
async def test_deny_changes_nothing(api):
    aid = await proposal(api, "عدل سرعة الموظف")
    out = await api("ceo.deny", action_id=aid, confirm=True)
    assert out["status"] == "denied"
    assert (await api("profiles.get", profile_id="planner"))["default_variant_id"] != "low"
    assert [a["id"] for a in (await api("ceo.actions", status="denied"))["actions"]] == [aid]


@pytest.mark.asyncio
async def test_newer_manual_edit_wins_over_stale_proposal(api):
    aid = await proposal(api, "عدل سرعة الموظف")
    await api("profiles.update", profile_id="planner", description="manually edited later")
    out = await api("ceo.approve", action_id=aid, confirm=True)
    assert out["status"] == "failed" and out["decision_note"] == "Action failed."
    planner = await api("profiles.get", profile_id="planner")
    assert planner["default_variant_id"] != "low" and planner["description"] == "manually edited later"


@pytest.mark.asyncio
async def test_ceo_creates_agents_and_teams_without_approval(api):
    before = len((await api("profiles.list"))["profiles"])
    out = await api("ceo.ask", question="أضف موظف جديد")
    assert out["actions_proposed"] == [] and len(out["agents_created"]) == 1
    created = out["agents_created"][0]
    assert created["id"].startswith("custom-") and created["role_type"] == "researcher"
    assert (await api("profiles.get", profile_id=created["id"]))["name"].startswith("Nadia")
    assert (await api("ceo.actions"))["actions"] == []  # nothing waits for approval

    out = await api("ceo.ask", question="create a security team")
    assert len(out["agents_created"]) == 3 and out["actions_proposed"] == []
    assert len((await api("profiles.list"))["profiles"]) == before + 1 + 3


@pytest.mark.asyncio
async def test_unknown_roles_become_custom_agents(api):
    bad_team = {"name": "T", "members": [{"name": "wiz", "role_type": "wizard", "instructions": "i"}]}
    api.db.insert("pending_actions", {"id": "w1", "kind": "new_team", "payload_json": json.dumps(bad_team),
                                      "summary": "s", "status": "pending", "created_at": "2026-01-01 00:00:00.000000"})
    out = await api("ceo.approve", action_id="w1", confirm=True)
    assert out["status"] == "approved" and out["created_profiles"][0]["role_type"] == "custom"


@pytest.mark.asyncio
async def test_invalid_team_is_atomic_zero_profiles_created(api):
    bad_team = {"name": "T", "members": [
        {"name": "ok", "role_type": "coder", "instructions": "i"},
        {"name": "bad", "role_type": "coder", "instructions": ""}]}
    api.db.insert("pending_actions", {"id": "a1", "kind": "new_team", "payload_json": json.dumps(bad_team),
                                      "summary": "s", "status": "pending", "created_at": "2026-01-01 00:00:00.000000"})
    out = await api("ceo.approve", action_id="a1", confirm=True)
    assert out["status"] == "failed" and out["decision_note"] == "Team member requires name and instructions."
    assert len((await api("profiles.list"))["profiles"]) == 10  # no zombie rows
    api.db.insert("pending_actions", {"id": "a2", "kind": "bogus_kind", "payload_json": "{}", "summary": "s",
                                      "status": "pending", "created_at": "2026-01-01 00:00:00.000000"})
    assert (await api("ceo.approve", action_id="a2", confirm=True))["status"] == "failed"


@pytest.mark.asyncio
async def test_proposal_tools_validate_before_recording(make_app):
    from backend.pyworker.orchestration import ceo_tools

    api = Api(make_app())
    db = api.db
    assert "unsupported contract fields" in ceo_tools.run_tool(
        db, "propose_update_contract", {"profile": "planner", "changes": {"id": "hacked"}})
    assert "not found" in ceo_tools.run_tool(db, "propose_update_contract",
                                             {"profile": "ghost", "changes": {"name": "x"}})
    assert "recorded" in ceo_tools.run_tool(db, "propose_new_profile",
                                            {"name": "n", "role": "wizard", "instructions": "i"})  # unknown role -> custom
    assert ceo_tools.run_tool(db, "propose_new_team", {"name": "t", "members": []}).startswith("ERROR: invalid args")
    assert ceo_tools.run_tool(db, "get_profile", {"identifier": "dr_sara"}).startswith("planner |")
    assert ceo_tools.run_tool(db, "list_missions", {"limit": 99}).startswith("ERROR: invalid args")
    assert db.scalar("SELECT COUNT(*) FROM pending_actions") == 1  # only the valid new_profile request


@pytest.mark.asyncio
async def test_team_members_carry_the_team_name_in_settings(api):
    out = await api("ceo.ask", question="create a security team")
    assert len(out["agents_created"]) == 3
    for created in out["agents_created"]:
        profile = await api("profiles.get", profile_id=created["id"])
        assert json.loads(profile["settings_json"])["team_name"]  # the UI groups members by this key
    names = {json.loads((await api("profiles.get", profile_id=c["id"]))["settings_json"])["team_name"]
             for c in out["agents_created"]}
    assert len(names) == 1


@pytest.mark.asyncio
async def test_overlapping_asks_only_claim_their_own_proposals(make_app):
    import asyncio

    release_alpha, release_beta = asyncio.Event(), asyncio.Event()

    def call(name):
        return json.dumps({"tool_call": {"name": "propose_new_profile", "args": {
            "name": name, "role": "researcher", "instructions": "i"}}})

    class Overlap(MockRuntime):
        def __init__(self):
            super().__init__()
            self.calls = {"alpha": 0, "beta": 0}

        async def execute_task(self, task):
            # beta's prompt also carries alpha's recorded turn in its history, so test beta first
            who = "beta" if "beta status question" in task.prompt else "alpha"
            self.calls[who] += 1
            n = self.calls[who]
            if who == "alpha":
                if n == 1:
                    return AgentResult(text=call("AlphaAgent"), model_id=task.model_id)
                await release_alpha.wait()  # alpha finishes only after beta recorded its proposal
                release_beta.set()  # beta finishes only after alpha has returned and acted
                return AgentResult(text='{"final": "alpha done"}', model_id=task.model_id)
            if n == 1:
                release_alpha.set()
                return AgentResult(text=call("BetaAgent"), model_id=task.model_id)
            await release_beta.wait()
            return AgentResult(text='{"final": "beta done"}', model_id=task.model_id)

    api = Api(make_app(runtime=Overlap()))

    async def beta():
        await asyncio.sleep(0.05)  # let alpha record its proposal first
        return await api("ceo.ask", question="beta status question")

    alpha_out, beta_out = await asyncio.gather(api("ceo.ask", question="alpha status question"), beta())
    assert [a["name"] for a in alpha_out["agents_created"]] == ["AlphaAgent"]
    assert [a["name"] for a in beta_out["agents_created"]] == ["BetaAgent"]
