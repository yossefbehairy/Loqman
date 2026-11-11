"""Workspaces/artifacts, skill sources, and skills/files frozen into runs."""

import asyncio
import base64
import hashlib
import os

import pytest

from .helpers import Api
from .test_runs import FakeRuntime, new_conv, wait_status


def can_symlink(tmp_path) -> bool:
    try:
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "workspace"
    (root / "sub").mkdir(parents=True)
    (root / "notes.txt").write_text("hello notes", encoding="utf-8")
    (root / "sub" / "data.md").write_text("# data", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_workspace_registration_rules(api, ws, tmp_path):
    out = await api("workspaces.create", path=str(ws))
    assert out["name"] == "workspace" and os.path.normcase(out["path"]) == os.path.normcase(os.path.realpath(ws))
    assert (await api.fail("workspaces.create", path=str(ws))).status == 409
    assert (await api.fail("workspaces.create", path="relative/dir")).status == 422
    assert (await api.fail("workspaces.create", path=str(tmp_path / "missing"))).status == 404
    assert (await api.fail("workspaces.create", path=str(ws / "notes.txt"))).status == 422  # not a dir
    assert (await api.fail("workspaces.create", path=str(ws / ".." / "x"))).status == 422
    assert (await api.fail("workspaces.create", path=os.path.abspath(os.sep))).status == 422  # filesystem root
    assert (await api.fail("workspaces.create", path="\\\\server\\share")).status == 422
    named = await api("workspaces.create", path=str(ws / "sub"), name="Sub")
    assert named["name"] == "Sub"
    assert len((await api("workspaces.list"))["workspaces"]) == 2
    assert (await api("workspaces.get", workspace_id=named["id"]))["id"] == named["id"]
    assert (await api.fail("workspaces.get", workspace_id="nope")).status == 404


@pytest.mark.asyncio
async def test_artifact_register_and_download_revalidates(api, ws):
    w = await api("workspaces.create", path=str(ws))
    art = await api("artifacts.register", workspace_id=w["id"], path="notes.txt")
    assert art["relative_path"] == "notes.txt" and art["size"] == 11 and art["mime"] == "text/plain"
    assert (await api.fail("artifacts.register", workspace_id=w["id"], path="notes.txt")).status == 409
    nested = await api("artifacts.register", workspace_id=w["id"], path="sub\\data.md")
    assert nested["relative_path"] == "sub/data.md"  # POSIX separators
    content = await api("artifacts.content", artifact_id=art["id"])
    assert base64.b64decode(content["content_base64"]) == b"hello notes"
    assert content["headers"]["Content-Security-Policy"] == "sandbox" and content["disposition"] == "attachment"
    (ws / "notes.txt").write_text("changed!", encoding="utf-8")  # re-read on every access
    assert base64.b64decode((await api("artifacts.content", artifact_id=art["id"]))["content_base64"]) == b"changed!"
    os.remove(ws / "notes.txt")
    assert (await api.fail("artifacts.content", artifact_id=art["id"])).status == 404
    assert [a["id"] for a in (await api("artifacts.list", workspace_id=w["id"]))["artifacts"]] == [art["id"], nested["id"]]
    assert (await api("artifacts.get", artifact_id=art["id"]))["id"] == art["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../outside.txt", "sub/../../outside.txt", "/etc/passwd", "C:\\Windows\\win.ini",
                                 "notes.txt:stream", "", "sub", "missing.txt"])
async def test_artifact_path_escapes_and_non_files_are_refused(api, ws, bad):
    w = await api("workspaces.create", path=str(ws))
    (ws.parent / "outside.txt").write_text("secret", encoding="utf-8")
    if bad == "":
        err = await api.fail("artifacts.register", workspace_id=w["id"], path=bad)
    else:
        err = await api.fail("artifacts.register", workspace_id=w["id"], path=bad)
    assert err.status in (403, 404, 422)


@pytest.mark.asyncio
async def test_symlinks_are_refused(api, ws, tmp_path):
    if not can_symlink(tmp_path):
        pytest.skip("symlink creation not permitted")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, ws / "link.txt")
    w = await api("workspaces.create", path=str(ws))
    assert (await api.fail("artifacts.register", workspace_id=w["id"], path="link.txt")).status == 403


@pytest.mark.asyncio
async def test_artifact_run_binding(make_app, ws):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    w = await api("workspaces.create", path=str(ws))
    other = await api("workspaces.create", path=str(ws / "sub"))
    cid = (await api("conversations.create", title="c", workspace_id=w["id"]))["id"]
    run = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="x")
    await wait_status(api, run["id"], "succeeded")
    ok = await api("artifacts.register", workspace_id=w["id"], path="notes.txt", run_id=run["id"])
    assert ok["run_id"] == run["id"]
    assert (await api.fail("artifacts.register", workspace_id=other["id"], path="data.md",
                           run_id=run["id"])).status == 409
    assert (await api.fail("artifacts.register", workspace_id=w["id"], path="sub/data.md",
                           run_id="nope")).status == 404
    assert (await api.fail("artifacts.register", workspace_id="nope", path="x")).status == 404


# -- skills ----------------------------------------------------------------------------

@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "skills"
    (root / "good").mkdir(parents=True)
    (root / "good" / "SKILL.md").write_text(
        "---\nname: Good Skill\ndescription: Does good\n---\nUse **good** practices.", encoding="utf-8")
    (root / "plain").mkdir()
    (root / "plain" / "SKILL.md").write_text("no front matter here", encoding="utf-8")
    (root / "not a skill!").mkdir()
    (root / "empty").mkdir()
    (root / "good" / "evil.py").write_text("raise SystemExit", encoding="utf-8")  # never read/executed
    return root


@pytest.mark.asyncio
async def test_skill_source_listing_and_reading(api, skills_dir):
    src = await api("skills.sources.register", path=str(skills_dir))
    assert src["name"] == "skills"
    assert (await api.fail("skills.sources.register", path=str(skills_dir))).status == 409
    listing = await api("skills.list", source_id=src["id"])
    by_id = {s["id"]: s for s in listing["skills"]}
    assert set(by_id) == {"good", "plain"}
    assert by_id["good"]["name"] == "Good Skill" and by_id["good"]["metadata_valid"] is True
    assert by_id["plain"]["metadata_valid"] is False and by_id["plain"]["warnings"]
    assert {s["entry"] for s in listing["skipped"]} == {"not a skill!"}
    assert "guidance only" in listing["notice"].lower()
    read = await api("skills.read", source_id=src["id"], skill_id="good", max_chars=10)
    assert read["content_truncated"] is True and len(read["content"]) == 10
    assert read["authority"] == "guidance_only" and read["grants_permissions"] is False
    full = await api("skills.read", source_id=src["id"], skill_id="good")
    assert full["provenance"]["sha256"] == hashlib.sha256(full["content"].encode()).hexdigest()
    assert (await api.fail("skills.read", source_id=src["id"], skill_id="../good")).status == 422
    assert (await api.fail("skills.read", source_id=src["id"], skill_id="missing")).status == 404
    assert (await api.fail("skills.read", source_id=src["id"], skill_id="good", max_chars=0)).status == 422
    await api("skills.sources.delete", source_id=src["id"])
    assert (skills_dir / "good" / "SKILL.md").exists()  # files untouched
    assert (await api.fail("skills.list", source_id=src["id"])).status == 404


@pytest.mark.asyncio
async def test_selected_skills_are_frozen_into_the_run(make_app, skills_dir):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    src = await api("skills.sources.register", path=str(skills_dir))
    cid = await new_conv(api)
    ref = {"source_id": src["id"], "skill_id": "good"}
    run = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="do it",
                    skills=[ref], request_key="k")
    await wait_status(api, run["id"], "succeeded")
    prompt = rt.calls[0].prompt
    assert "GUIDANCE ONLY" in prompt and "Use **good** practices." in prompt and prompt.endswith("do it")
    assert prompt.index("Use **good**") < prompt.index("Current user message:")
    meta = (await api("runs.get", run_id=run["id"]))["skills"]
    assert meta[0]["skill_id"] == "good" and meta[0]["sha256"] and "content" not in meta[0]
    # editing the file later neither changes the stored run nor breaks idempotent retries
    (skills_dir / "good" / "SKILL.md").write_text("---\nname: X\n---\nTAMPERED", encoding="utf-8")
    again = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="do it",
                      skills=[ref], request_key="k")
    assert again["id"] == run["id"] and len(rt.calls) == 1


@pytest.mark.asyncio
async def test_skill_selection_errors_persist_nothing(make_app, skills_dir):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    src = await api("skills.sources.register", path=str(skills_dir))
    cid = await new_conv(api)
    good = {"source_id": src["id"], "skill_id": "good"}
    for skills, status in [([good, good], 422), ([{"source_id": "nope", "skill_id": "good"}], 404),
                           ([{"source_id": src["id"], "skill_id": "missing"}], 404),
                           ([good, good, good, good], 422)]:
        err = await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m",
                             prompt="x", skills=skills)
        assert err.status == status, skills
    assert (await api("conversations.messages", conversation_id=cid))["messages"] == []
    assert rt.calls == []


@pytest.mark.asyncio
async def test_input_files_are_frozen_untrusted_data(make_app, ws):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    w = await api("workspaces.create", path=str(ws))
    cid = (await api("conversations.create", title="c", workspace_id=w["id"]))["id"]
    run = await api("runs.start_turn", conversation_id=cid, engine="codex", model_id="m", prompt="summarize",
                    input_files=["notes.txt", "sub/data.md"])
    await wait_status(api, run["id"], "succeeded")
    prompt = rt.calls[0].prompt
    assert "UNTRUSTED DATA" in prompt and "hello notes" in prompt and "# data" in prompt
    assert prompt.endswith("summarize")
    meta = (await api("runs.get", run_id=run["id"]))["input_files"]
    assert [m["path"] for m in meta] == ["notes.txt", "sub/data.md"] and all(m["sha256"] for m in meta)
    assert "content" not in meta[0]


@pytest.mark.asyncio
async def test_input_file_rules(make_app, ws, tmp_path):
    rt = FakeRuntime(gated=False)
    api = Api(make_app(runtime_factory=lambda e: rt, mode="real"))
    w = await api("workspaces.create", path=str(ws))
    with_ws = (await api("conversations.create", title="c", workspace_id=w["id"]))["id"]
    without = await new_conv(api)
    (ws / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    (ws / "latin.txt").write_bytes("café".encode("latin-1"))
    (ws / "big.txt").write_text("x" * 7000, encoding="utf-8")

    async def attempt(cid, files):
        return await api.fail("runs.start_turn", conversation_id=cid, engine="codex", model_id="m",
                              prompt="p", input_files=files)

    assert (await attempt(without, ["notes.txt"])).status == 422  # no workspace
    assert (await attempt(with_ws, ["bin.dat"])).status == 422
    assert (await attempt(with_ws, ["latin.txt"])).status == 422
    assert (await attempt(with_ws, ["big.txt"])).status == 413
    assert (await attempt(with_ws, ["../x.txt"])).status == 422
    assert (await attempt(with_ws, ["notes.txt", "NOTES.TXT"])).status == 422  # duplicate (case-insensitive)
    assert (await attempt(with_ws, ["missing.txt"])).status == 404
    assert (await attempt(with_ws, ["a", "b", "c", "d"])).status == 422
    assert rt.calls == [] and (await api("conversations.messages", conversation_id=with_ws))["messages"] == []
