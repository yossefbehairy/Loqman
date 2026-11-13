import json

import pytest

from backend.pyworker.engine.output_validation import validate_output
from backend.pyworker.runtime.base import AgentResult
from .helpers import Api
from .test_runs import wait_status


def test_output_requires_a_result_and_enforces_nested_schema():
    schema = {"type": "object", "required": ["changes"], "properties": {
        "changes": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["id", "difference"], "properties": {
                "id": {"type": "string", "minLength": 1}, "difference": {"type": "number"}}}}}}
    validate_output({"changes": [{"id": "A", "difference": 10}]}, schema)
    for invalid in ({}, {"changes": []}, {"changes": [{"id": "", "difference": True}]}):
        with pytest.raises(ValueError):
            validate_output(invalid, schema)
    with pytest.raises(ValueError):
        validate_output({})


@pytest.mark.asyncio
async def test_conversation_uses_frozen_profile_and_checks_output(make_app):
    class Runtime:
        async def execute_task(self, task):
            assert "Answer with an approval decision." in task.prompt
            return AgentResult(text='{"approved":"yes"}', model_id="m")

    api = Api(make_app(runtime_factory=lambda _: Runtime(), mode="real"))
    profile = api.db.one("SELECT * FROM agent_profiles LIMIT 1")
    api.db.update("agent_profiles", {
        "instructions": "Answer with an approval decision.",
        "output_schema_json": json.dumps({"type": "object", "required": ["approved"],
                                         "properties": {"approved": {"type": "boolean"}}}),
    }, "id=?", (profile["id"],))
    conv = await api("conversations.create", title="Review", profile_id=profile["id"])
    run = await api("runs.start_turn", conversation_id=conv["id"], engine="claude-code",
                    model_id="m", prompt="Review this.")
    done = await wait_status(api, run["id"], "failed")
    assert done["assistant_content"] is None
    frozen = json.loads(api.db.scalar("SELECT profile_snapshot_json FROM conversation_runs WHERE id=?", (run["id"],)))
    assert frozen["instructions"] == "Answer with an approval decision."
