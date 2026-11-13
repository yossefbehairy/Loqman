import pytest

from backend.pyworker.appsettings import settings
from backend.pyworker.app import Application
from backend.pyworker.runtime import ledger
from backend.pyworker.runtime.mock_runtime import MockRuntime

from .helpers import Api
from .legacy_org import seed_legacy_specialists


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Factory for worker Applications on throwaway databases."""
    created = []
    monkeypatch.setattr(settings, "RUNTIME_MODE", settings.RUNTIME_MODE)  # restored on teardown

    def make(runtime=None, mode="mock", runtime_factory=None, specialists=True, **kw):
        monkeypatch.setattr(settings, "RUNTIME_MODE", mode)
        extra = {} if runtime_factory is not None else {"runtime": runtime or MockRuntime()}
        app = Application.create(db_path=str(tmp_path / f"w{len(created)}.db"),
                                 runtime_factory=runtime_factory, **extra, **kw)
        if specialists:
            seed_legacy_specialists(app.ctx.db)
        created.append(app)
        return app

    yield make
    for app in created:
        ledger.bind(None)
        app.ctx.db.close()


@pytest.fixture
def api(make_app):
    return Api(make_app())
