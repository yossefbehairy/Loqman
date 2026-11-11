"""Isolated tests: own FastAPI app + own SQLite file; does not import backend.app.main."""
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import workspaces as ws_routes
from backend.app.db.models import Artifact, Workspace
from backend.app.db.session import Base, get_db


@pytest.fixture
def env(tmp_path):
    db_file = tmp_path / "t.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(ws_routes.router)
    app.include_router(ws_routes.artifacts_router)
    app.dependency_overrides[get_db] = override
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    yield TestClient(app), root, outside, Session
    engine.dispose()


def mk(client, root):
    r = client.post("/api/workspaces", json={"path": str(root)})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_list_and_persist(env):
    client, root, _, Session = env
    ws = mk(client, root)
    assert ws["name"] == "work"
    assert client.get("/api/workspaces").json()[0]["id"] == ws["id"]
    assert client.get(f"/api/workspaces/{ws['id']}").status_code == 200
    with Session() as s:  # new session = persisted
        assert s.get(Workspace, ws["id"]).path == os.path.realpath(root)
    assert client.post("/api/workspaces", json={"path": str(root)}).status_code == 409


@pytest.mark.parametrize("bad", ["relative/dir", "", "..", "C:rel"])
def test_workspace_rejects_non_absolute(env, bad):
    client, *_ = env
    assert client.post("/api/workspaces", json={"path": bad}).status_code == 422


def test_workspace_missing_and_file(env, tmp_path):
    client, root, *_ = env
    assert client.post("/api/workspaces", json={"path": str(root / "nope")}).status_code == 404
    f = root / "f.txt"
    f.write_text("x")
    assert client.post("/api/workspaces", json={"path": str(f)}).status_code == 422
    assert client.post("/api/workspaces", json={"path": str(root / ".." / "work")}).status_code == 422
    assert client.post("/api/workspaces", json={"path": os.path.abspath(os.path.sep)}).status_code == 422


def test_normal_file_register_and_download(env):
    client, root, _, Session = env
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_bytes(b"hello")
    ws = mk(client, root)
    r = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "sub\\a.txt"})
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["relative_path"] == "sub/a.txt" and art["size"] == 5 and art["mime"] == "text/plain"
    assert client.get(f"/api/artifacts/{art['id']}").json()["run_id"] is None
    assert client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "sub/a.txt", "run_id": "missing"}).status_code == 404
    c = client.get(f"/api/artifacts/{art['id']}/content")
    assert c.status_code == 200 and c.content == b"hello"
    assert c.headers["x-content-type-options"] == "nosniff"
    assert len(client.get(f"/api/workspaces/{ws['id']}/artifacts").json()) == 1
    assert client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "sub/a.txt"}).status_code == 409
    with Session() as s:
        assert s.query(Artifact).count() == 1


def test_opened_handle_outside_workspace_is_rejected(env, monkeypatch):
    client, root, outside, _ = env
    (root / "safe.txt").write_text("safe")
    ws = mk(client, root)
    artifact = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "safe.txt"}).json()
    monkeypatch.setattr(ws_routes.fs, "_opened_path", lambda _: str(outside / "secret.txt"))
    assert client.get(f"/api/artifacts/{artifact['id']}/content").status_code == 403


@pytest.mark.parametrize("bad", [
    "../outside/secret.txt", "sub/../../outside/secret.txt", "..\\outside\\secret.txt",
    "/etc/passwd", "C:\\Windows\\win.ini", "\\\\host\\share\\x", "a.txt:stream", "", ".",
])
def test_traversal_and_absolute_rejected(env, bad):
    client, root, *_ = env
    (root / "a.txt").write_text("x")
    ws = mk(client, root)
    r = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": bad})
    assert r.status_code in (404, 422) and r.status_code != 201


def test_absolute_path_inside_workspace_still_rejected(env):
    client, root, *_ = env
    (root / "a.txt").write_text("x")
    ws = mk(client, root)
    assert client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": str(root / "a.txt")}).status_code == 422


def test_missing_directory_and_oversize(env, monkeypatch):
    client, root, *_ = env
    (root / "d").mkdir()
    (root / "big.bin").write_bytes(b"x" * 20)
    ws = mk(client, root)
    base = f"/api/workspaces/{ws['id']}/artifacts"
    assert client.post(base, json={"path": "missing.txt"}).status_code == 404
    assert client.post(base, json={"path": "d"}).status_code == 422
    monkeypatch.setattr("backend.app.core.workspace_fs.MAX_ARTIFACT_BYTES", 10)
    assert client.post(base, json={"path": "big.bin"}).status_code == 413


def test_unknown_ids(env):
    client, *_ = env
    assert client.get("/api/workspaces/nope").status_code == 404
    assert client.get("/api/artifacts/nope").status_code == 404
    assert client.get("/api/artifacts/nope/content").status_code == 404
    assert client.post("/api/workspaces/nope/artifacts", json={"path": "a"}).status_code == 404


def test_download_after_delete_or_growth(env, monkeypatch):
    client, root, *_ = env
    f = root / "a.txt"
    f.write_text("x")
    ws = mk(client, root)
    art = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "a.txt"}).json()
    f.write_bytes(b"y" * 50)
    monkeypatch.setattr("backend.app.core.workspace_fs.MAX_ARTIFACT_BYTES", 10)
    assert client.get(f"/api/artifacts/{art['id']}/content").status_code == 413
    f.unlink()
    assert client.get(f"/api/artifacts/{art['id']}/content").status_code == 404


def _symlink(link, target, is_dir):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")


def test_symlink_file_and_dir_escape_rejected(env):
    client, root, outside, _ = env
    _symlink(root / "link.txt", outside / "secret.txt", False)
    _symlink(root / "linkdir", outside, True)
    ws = mk(client, root)
    base = f"/api/workspaces/{ws['id']}/artifacts"
    assert client.post(base, json={"path": "link.txt"}).status_code == 403
    assert client.post(base, json={"path": "linkdir/secret.txt"}).status_code == 403


def test_symlink_swapped_after_registration_blocked(env):
    client, root, outside, _ = env
    f = root / "a.txt"
    f.write_text("ok")
    ws = mk(client, root)
    art = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "a.txt"}).json()
    f.unlink()
    _symlink(f, outside / "secret.txt", False)
    r = client.get(f"/api/artifacts/{art['id']}/content")
    assert r.status_code == 403 and b"secret" not in r.content


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_junction_escape_rejected(env):
    client, root, outside, _ = env
    p = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "junc"), str(outside)], capture_output=True)
    if p.returncode != 0:
        pytest.skip("junction creation failed")
    ws = mk(client, root)
    r = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "junc/secret.txt"})
    assert r.status_code == 403


def test_workspace_root_replaced_by_link_detected(env):
    client, root, outside, _ = env
    (root / "a.txt").write_text("x")
    ws = mk(client, root)
    art = client.post(f"/api/workspaces/{ws['id']}/artifacts", json={"path": "a.txt"}).json()
    (outside / "a.txt").write_text("elsewhere")
    (root / "a.txt").unlink()
    root.rmdir()
    _symlink(root, outside, True)
    r = client.get(f"/api/artifacts/{art['id']}/content")
    assert r.status_code in (403, 404, 409) and b"elsewhere" not in r.content


def test_delete_workspace_cascades_not_exposed_and_no_write_routes(env):
    env  # fixture unused; inspect routers directly
    routes = ws_routes.router.routes + ws_routes.artifacts_router.routes
    methods = {m for r in routes for m in r.methods}
    assert methods <= {"GET", "POST", "HEAD", "OPTIONS"}
    paths = [r.path for r in routes if "POST" in r.methods]
    assert sorted(paths) == ["/api/workspaces", "/api/workspaces/{workspace_id}/artifacts"]

