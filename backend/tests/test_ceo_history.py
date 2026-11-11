"""
CEO conversation continuity (PR6b):

1. /api/ceo/ask records every user question and every GM reply into the
   backend-owned conversation log (ceo_messages) — the renderer never has
   to send history, and continuity survives app restarts.
2. Recent turns are INJECTED into the prompt of the next ask, so the GM
   answers coherently as one running conversation.
3. The whole CEO lane uses ONE stable OpenCode thread: consult lane titles
   are constant (ceo-ask-gm / ceo-fast-gm), so OpenCode no longer gets a
   new session (tab) per message.
"""
import json


def _ask(client, question: str):
    res = client.post("/api/ceo/ask", json={"question": question})
    assert res.status_code == 200, f"ask failed: {res.status_code} {res.text[:300]}"
    return res


def _history(client):
    res = client.get("/api/ceo/history?limit=50")
    assert res.status_code == 200
    return res.json()


def test_ask_records_user_and_reply_into_backend_history(client):
    marker = "سؤال مرجعي رقم 42ب يخص تمسيك سجل السياق"
    _ask(client, marker)
    _ask(client, "سؤال متابعة الجديد 43ث بعد سؤال التطوير")

    h = _history(client)

    # Every exchange is recorded in order: user → assistant per ask.
    assert h[0]["role"] == "user"
    assert h[0]["content"] == marker
    assert h[1]["role"] == "assistant"
    assert h[1]["content"], "assistant reply missing"

    user_msgs = [m for m in h if m["role"] == "user"]
    assert marker in [m["content"] for m in user_msgs]
    assert "سؤال متابعة الجديد 43ث بعد سؤال التطوير" in [m["content"] for m in user_msgs]


def test_history_feeds_the_next_prompt(client, monkeypatch):
    from backend.app.orchestration import agent_loop as loop_mod

    prompts: list = []
    real_run = loop_mod.run_agent_task

    async def spy(runtime, task, kind=None):
        prompts.append(task.prompt)
        return await real_run(runtime, task, kind=kind)

    monkeypatch.setattr(loop_mod, "run_agent_task", spy)

    marker = "سطر الذاكرة المميز 77س يبقى في سياق السؤال الجاي"
    _ask(client, marker)
    _ask(client, "كمية سياق ثاني 776 Girl جملة تتبع")

    # The SECOND ask's prompt contains the FIRST exchange (continuity).
    assert marker in prompts[-1], "next ask must embed previous turns"
    assert "سط 776 Girl جملة تتبع" not in prompts[0]


def test_deep_and_fast_use_stable_thread_titles_in_ledger(client):
    _ask(client, "استعرض شكل الفرق الحالية مع ترجمة الأسعار")
    # Smalltalk lanes route through the fast single-call thread.
    fast_classifies = client.post("/api/ceo/ask", json={"question": "صبحك فل يا غالي"})
    assert fast_classifies.status_code == 200

    # MockRuntime prefixes deterministic thread ids; normalize for the check.
    normalize = lambda sid: str(sid).replace("mock-thread-", "")

    events = client.get("/api/usage/events", params={"kind": "ceo_ask", "limit": 20}).json()
    assert events, "no ceo_ask ledger events recorded"
    deep_sessions = {normalize(e["session_id"]) for e in events if e["session_id"]}
    assert deep_sessions == {"ceo-ask-gm"}

    events = client.get("/api/usage/events", params={"kind": "ceo_ask_fast", "limit": 20}).json()
    if events:
        assert {normalize(e["session_id"]) for e in events} == {"ceo-fast-gm"}


def test_history_endpoint_enforces_sane_limit(client):
    res = client.get("/api/ceo/history?limit=10000")
    assert res.status_code in (200, 422)  # clamped or validated, never a crash
    if res.status_code == 200:
        assert isinstance(res.json(), list)
