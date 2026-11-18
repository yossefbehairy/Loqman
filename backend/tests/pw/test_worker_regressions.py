import pytest

from backend.pyworker.core.approvals import APPROVAL_CAPABILITIES
from backend.pyworker.orchestration.agent_loop import build_fast_system_prompt, build_loop_system_prompt
from backend.pyworker.orchestration.ceo import _route_task
from backend.pyworker.runtime.providers import PROVIDERS
from .helpers import Api
from .test_routing import FakeEngine


@pytest.mark.asyncio
async def test_mock_ceo_never_calls_explicit_engine_factory(make_app):
    app = make_app()
    app.ctx.engine_factory = lambda engine: pytest.fail('real engine factory reached')
    result = await Api(app).fail('ceo.ask', question='hello', engine='claude-code', model_id='sonnet')
    assert result.status == 400


@pytest.mark.asyncio
async def test_mission_route_uses_captured_identity(monkeypatch):
    from backend.pyworker.orchestration import ceo
    monkeypatch.setattr(ceo, '_default_runtime_identity', lambda: ('codex', 'openai'))
    runtime = FakeEngine('claude-code')
    _, provider, _, _, _ = await _route_task(None, runtime, None, {}, 'claude-sonnet-x', None,
                                               'claude-sonnet-x', ('claude-code', 'anthropic'))
    assert provider == 'anthropic'


@pytest.mark.asyncio
async def test_engine_test_chooses_luna_and_blocks_mock(make_app):
    from .test_routing import entries
    class Catalog(FakeEngine):
        async def list_available_models(self, free_only=True):
            return entries(('gpt-6.1-sol', 'gpt-6-luna'), 'openai', False)
    rt = Catalog('codex')
    api = Api(make_app(mode='real', runtime_factory=lambda _: rt))
    out = await api('engines.test', engine_id='codex')
    assert out['model_id'] == 'gpt-6-luna'
    mock = Api(make_app())
    mock.app.ctx.engine_factory = lambda _: pytest.fail('factory reached')
    assert (await mock('engines.test', engine_id='codex'))['error_code'] == 'mock_mode'


@pytest.mark.asyncio
async def test_actions_hide_only_active_own_proposals(api):
    from backend.pyworker.orchestration.ceo_tools import _record_action
    db = api.app.ctx.db
    own = _record_action(db, 'new_profile', {}, 'own')
    other = _record_action(db, 'new_profile', {}, 'other')
    contract = _record_action(db, 'update_contract', {}, 'contract')
    api.app.ctx.ask_progress['active'] = {'action_ids': [own, contract], 'done': False}
    assert {a['id'] for a in (await api('ceo.actions'))['actions']} == {other, contract}
    api.app.ctx.ask_progress['active']['action_ids'] = []
    assert own in {a['id'] for a in (await api('ceo.actions'))['actions']}


def test_ceo_prompts_use_latest_message_language():
    for build in (build_fast_system_prompt, build_loop_system_prompt):
        prompt = build('hello')
        assert 'language of the user\'s latest message' in prompt
        assert 'name Loqman exactly in English' in prompt


def test_approval_capabilities_use_provider_ids():
    assert set(APPROVAL_CAPABILITIES) == {p.engine_id for p in PROVIDERS}
