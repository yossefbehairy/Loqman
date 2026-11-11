"""Isolated tests for the skills library (own app + SQLite; no main.py)."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes import skills as skills_routes
from backend.app.core import skills_fs as sfs
from backend.app.db import skill_models  # noqa: F401  (registers table)
from backend.app.db.session import Base, get_db

GOOD = "---\nname: Good Skill\ndescription: Does a thing\n---\n# Body\nhello\n"


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(skills_routes.router)
    app.dependency_overrides[get_db] = override
    src = tmp_path / "skills"
    src.mkdir()
    yield TestClient(app), src, tmp_path
    engine.dispose()


def mkskill(src, name, text=GOOD, extra=None):
    d = src / name
    d.mkdir()
    (d / "SKILL.md").write_bytes(text.encode("utf-8"))
    for fname, body in (extra or {}).items():
        (d / fname).write_bytes(body.encode("utf-8"))
    return d


def register(client, src):
    r = client.post("/api/skill-sources", json={"path": str(src)})
    assert r.status_code == 201, r.text
    return r.json()


def try_symlink(link, target, is_dir):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
        return
    except (OSError, NotImplementedError):
        pass
    if is_dir and os.name == "nt":  # junctions need no privilege
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode == 0:
            return
    pytest.skip("links not permitted on this system")


def test_register_list_and_read_with_provenance(env):
    client, src, _ = env
    mkskill(src, "alpha", extra={"run.sh": "rm -rf /", ".env": "TOKEN=abc123"})
    s = register(client, src)
    assert client.get("/api/skill-sources").json()[0]["id"] == s["id"]
    lst = client.get(f"/api/skill-sources/{s['id']}/skills").json()
    assert [k["id"] for k in lst["skills"]] == ["alpha"]
    assert lst["skills"][0]["name"] == "Good Skill"
    assert "content" not in lst["skills"][0]
    assert "guidance only" in lst["notice"]
    r = client.get(f"/api/skill-sources/{s['id']}/skills/alpha").json()
    assert r["content"] == GOOD
    assert r["authority"] == "guidance_only" and r["grants_permissions"] is False and r["auto_applied"] is False
    p = r["provenance"]
    assert p["source_id"] == s["id"] and p["relative_path"] == "alpha/SKILL.md" and len(p["sha256"]) == 64
    assert "abc123" not in str(r) and "rm -rf" not in str(r)


def test_nothing_written_or_executed(env):
    client, src, _ = env
    d = mkskill(src, "alpha", extra={"run.py": "open('pwned','w').write('x')"})
    s = register(client, src)
    client.get(f"/api/skill-sources/{s['id']}/skills")
    client.get(f"/api/skill-sources/{s['id']}/skills/alpha")
    assert sorted(os.listdir(d)) == ["SKILL.md", "run.py"]
    assert not (d / "pwned").exists() and not os.path.exists("pwned")
    assert sorted(os.listdir(src)) == ["alpha"]


def test_register_rejects_bad_paths(env):
    client, src, tmp = env
    assert client.post("/api/skill-sources", json={"path": "relative/dir"}).status_code == 422
    assert client.post("/api/skill-sources", json={"path": str(src / ".." / "skills")}).status_code == 422
    assert client.post("/api/skill-sources", json={"path": str(tmp / "missing")}).status_code == 404
    f = tmp / "f.txt"
    f.write_text("x")
    assert client.post("/api/skill-sources", json={"path": str(f)}).status_code == 422
    root = os.path.abspath(os.sep)
    assert client.post("/api/skill-sources", json={"path": root}).status_code == 422
    register(client, src)
    assert client.post("/api/skill-sources", json={"path": str(src)}).status_code == 409


def test_unregister_leaves_files(env):
    client, src, _ = env
    mkskill(src, "alpha")
    s = register(client, src)
    assert client.delete(f"/api/skill-sources/{s['id']}").status_code == 204
    assert (src / "alpha" / "SKILL.md").exists()
    assert client.get(f"/api/skill-sources/{s['id']}/skills").status_code == 404


def test_malformed_metadata_is_listed_with_warnings(env):
    client, src, _ = env
    mkskill(src, "nofm", "# just markdown\n")
    mkskill(src, "unclosed", "---\nname: x\nno end\n")
    mkskill(src, "noname", "---\ndescription: d\n---\nbody")
    mkskill(src, "ctrl", "---\nname: \"A\x1b[31mB\"\ndescription: ok\n---\n")
    d = src / "badutf"
    d.mkdir()
    (d / "SKILL.md").write_bytes(b"---\nname: x\n---\n\xff\xfe\x00bad")
    s = register(client, src)
    by = {k["id"]: k for k in client.get(f"/api/skill-sources/{s['id']}/skills").json()["skills"]}
    assert by["nofm"]["metadata_valid"] is False and by["nofm"]["name"] == "nofm"
    assert by["unclosed"]["metadata_valid"] is False
    assert "front matter has no name" in by["noname"]["warnings"]
    assert "\x1b" not in by["ctrl"]["name"]
    assert by["badutf"]["metadata_valid"] is False
    assert client.get(f"/api/skill-sources/{s['id']}/skills/badutf").status_code == 422


def test_oversized_skill_listed_but_not_loadable(env):
    client, src, _ = env
    mkskill(src, "big", GOOD + "x" * (sfs.MAX_SKILL_FILE_BYTES + 10))
    s = register(client, src)
    item = client.get(f"/api/skill-sources/{s['id']}/skills").json()["skills"][0]
    assert item["size_bytes"] > sfs.MAX_SKILL_FILE_BYTES and item["name"] == "Good Skill"
    assert any("cannot be loaded" in w for w in item["warnings"])
    assert client.get(f"/api/skill-sources/{s['id']}/skills/big").status_code == 413


def test_max_chars_truncation_and_bounds(env):
    client, src, _ = env
    mkskill(src, "alpha", GOOD + "y" * 500)
    s = register(client, src)
    r = client.get(f"/api/skill-sources/{s['id']}/skills/alpha?max_chars=50").json()
    assert len(r["content"]) == 50 and r["content_truncated"] is True
    assert client.get(f"/api/skill-sources/{s['id']}/skills/alpha?max_chars=0").status_code == 422
    assert client.get(f"/api/skill-sources/{s['id']}/skills/alpha?max_chars={sfs.MAX_LOAD_CHARS + 1}").status_code == 422


@pytest.mark.parametrize("bad", ["..", "../x", "..%2Fskills", "a/b", "a\\b", "C:", "alpha.", "CON", ".hidden", "%00"])
def test_traversal_ids_rejected(env, bad):
    client, src, tmp = env
    mkskill(src, "alpha")
    (tmp / "SKILL.md").write_text(GOOD)  # would be reachable via ".."
    s = register(client, src)
    r = client.get(f"/api/skill-sources/{s['id']}/skills/{bad}")
    # ".." may be normalised away by the HTTP client into a different route (405).
    assert r.status_code in (404, 405, 422), (bad, r.status_code)
    assert "Good Skill" not in r.text
    if "/" not in bad and "%" not in bad:
        with pytest.raises(sfs.WorkspaceFsError):  # defence in depth below the router
            sfs.load_skill(s["path"], bad)


def test_wrong_case_filename_not_aliased(env):
    client, src, _ = env
    d = src / "lower"
    d.mkdir()
    (d / "skill.md").write_text(GOOD)
    s = register(client, src)
    assert client.get(f"/api/skill-sources/{s['id']}/skills").json()["skills"] == []
    assert client.get(f"/api/skill-sources/{s['id']}/skills/lower").status_code == 404


def test_symlinked_skill_dir_and_file_skipped_and_refused(env):
    client, src, tmp = env
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: Secret\ndescription: leak\n---\nSECRET")
    try_symlink(src / "linkdir", outside, True)
    d = src / "linkfile"
    d.mkdir()
    try_symlink(d / "SKILL.md", outside / "SKILL.md", False)
    mkskill(src, "ok")
    s = register(client, src)
    lst = client.get(f"/api/skill-sources/{s['id']}/skills").json()
    assert [k["id"] for k in lst["skills"]] == ["ok"]
    assert {k["entry"] for k in lst["skipped"]} >= {"linkdir", "linkfile"}
    for sid in ("linkdir", "linkfile"):
        r = client.get(f"/api/skill-sources/{s['id']}/skills/{sid}")
        assert r.status_code == 403 and "SECRET" not in r.text


def test_symlinked_source_root_swapped_after_registration(env):
    client, src, tmp = env
    mkskill(src, "alpha")
    s = register(client, src)
    evil = tmp / "evil"
    evil.mkdir()
    mkskill(evil, "alpha", "---\nname: Evil\n---\nEVIL")
    os.rename(src / "alpha" / "SKILL.md", tmp / "moved.md")
    os.rmdir(src / "alpha")
    os.rmdir(src)
    try_symlink(src, evil, True)
    r = client.get(f"/api/skill-sources/{s['id']}/skills/alpha")
    assert r.status_code == 409 and "EVIL" not in r.text
    assert client.get(f"/api/skill-sources/{s['id']}/skills").status_code == 409


def test_limits_on_skill_count(env, monkeypatch):
    client, src, _ = env
    monkeypatch.setattr(sfs, "MAX_SKILLS_PER_SOURCE", 3)
    for i in range(6):
        mkskill(src, f"s{i}")
    s = register(client, src)
    lst = client.get(f"/api/skill-sources/{s['id']}/skills").json()
    assert len(lst["skills"]) == 3 and lst["truncated"] is True


def test_source_root_skill_and_nested_not_discovered(env):
    client, src, _ = env
    (src / "SKILL.md").write_text(GOOD)
    mkskill(src, "outer")
    mkskill(src / "outer", "inner")  # one level only
    s = register(client, src)
    ids = {k["id"] for k in client.get(f"/api/skill-sources/{s['id']}/skills").json()["skills"]}
    assert ids == {"_root", "outer"}
    assert client.get(f"/api/skill-sources/{s['id']}/skills/_root").json()["provenance"]["relative_path"] == "SKILL.md"
