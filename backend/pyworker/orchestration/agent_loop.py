"""
CEO self-reasoning loop (ReAct over the single-shot runtime).

Two paths (PR3 fast path):
- run_ceo_ask:   the bounded tool loop. Each iteration asks the model for
                 exactly one JSON envelope:
                   {"tool_call": {"name": "...", "args": {...}}}  or  {"final": "..."}
                 Tool results return as "[tool_observation] ..." transcript lines.
                 Hard cap: 3 iterations, then the best collected summary.
- run_ceo_fast:  small talk has no data intent — one plain-text call with no
                 tool catalog and no envelope, at reasoning effort "low".
Write tools only ever RECORD pending proposals (see ceo_tools) — the loop
itself cannot mutate contracts or profiles.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from backend.pyworker.appsettings import settings
from backend.pyworker.db.core import Database
from backend.pyworker.engine.context import sanitize_model_output
from backend.pyworker.orchestration import ceo_tools
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.ledger import run_agent_task, KIND_CEO_ASK, KIND_CEO_ASK_FAST

MAX_ITERATIONS = 3

# One conversation thread for the CEO lane: every ask reuses the SAME OpenCode
# session (thread reuse by stable title is the runtime's contract), and the
# recorded history is injected per prompt. No more "new tab per message".
CEO_ASK_THREAD_TITLE = "ceo-ask-gm"
CEO_FAST_THREAD_TITLE = "ceo-fast-gm"

# The company Loqman leads. Injected into every CEO prompt so the conversational
# CEO always knows the two teams and the contract that binds them.
_ORG_CONTEXT = (
    "Company organization: you, Loqman, are the CEO. No other agents ship with the app. "
    "You may create agents and teams whenever the user needs them: call propose_new_profile "
    "(one agent) or propose_new_team (several) with a clear name and full working instructions. "
    "Those two requests are applied immediately, with no approval step. "
    "Always write the name Loqman exactly in English, even inside an Arabic reply.\n"
)


def _history_block(history: Optional[List[Dict[str, Any]]]) -> str:
    """Render recent conversation turns as context for the current question."""
    if not history:
        return ""
    lines: List[str] = []
    for h in history[-8:]:
        role = "User" if h.get("role") == "user" else "CEO"
        lines.append(f"- {role}: {str(h.get('content') or '')[:500]}")
    return (
        "Recent conversation (oldest first) — you are continuing the SAME "
        "conversation with the user, never restart or re-greet:\n"
        + "\n".join(lines)
        + "\n\n"
    )


def build_loop_system_prompt(question: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
    return (
        "ceo tools — you are Loqman, CEO of the company, answering with live company data.\n"
        + _ORG_CONTEXT
        + "You have these tools (call each at most once per distinct need):\n"
        f"{ceo_tools.tool_catalog_prompt()}\n"
        "Rules:\n"
        "- Every reply must be EXACTLY one JSON object, no other text, no markdown fences:\n"
        '  {"tool_call": {"name": "<tool>", "args": {...}}}  OR  {"final": "<answer>"}.\n'
        "- Use tools to look up real data before answering factual questions.\n"
        "- Greetings and small talk need no company data: answer immediately with "
        '{"final": "<answer>"} and no tool calls.\n'
        "- propose_* tools only RECORD a proposal (status pending) — never execute anything. "
        "After recording one, do NOT call it again for the same change; "
        "mention in your final answer that it awaits approval.\n"
        "- Answer in the user's own language and dialect (Egyptian Arabic if they write it).\n"
        "- Never invent mission ids, statuses, or profile data — only quote tool observations.\n"
        + _history_block(history)
        + f"User question: {question.strip()}"
    )


def build_fast_system_prompt(
    question: str, history: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Single-call plain-text prompt for small talk (no tools, no envelope)."""
    return (
        "ceo fast reply — you are Loqman, CEO of the company, in warm casual conversation.\n"
        + _ORG_CONTEXT
        + "This exchange does not involve company data. Reply naturally in the user's "
        "own language and dialect (Egyptian Arabic if they write Egyptian Arabic); "
        "match the vibe — a greeting gets a friendly line, a friendly question gets "
        "a friendly real answer.\n"
        "Plain text only — no JSON, no markdown fences, no tool talk, and no mention "
        "of missions, employees, approvals, or sessions.\n"
        + _history_block(history)
        + f"User question: {question.strip()}"
    )


def _parse_envelope(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(sanitize_model_output(text))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _emit(progress: Optional[Callable[[str, str, int], None]], stage: str,
          detail: str, iteration: int) -> None:
    """Report loop progress; reporting must never break the ask itself."""
    if progress is None:
        return
    try:
        progress(stage, detail, iteration)
    except Exception:
        pass


async def run_ceo_ask(
    question: str,
    model_id: str,
    provider_id: str = "opencode",
    variant: Optional[str] = None,
    runtime=None,
    db: Optional[Database] = None,
    max_iterations: int = MAX_ITERATIONS,
    timeout: int = 120,
    progress: Optional[Callable[[str, str, int], None]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the bounded tool loop. Returns {reply, actions_proposed, iterations}."""
    if runtime is None or db is None:
        raise ValueError("runtime and db are required")
    question = (question or "").strip()
    if not question:
        raise ValueError("Empty question.")

    system = build_loop_system_prompt(question, history)
    transcript: List[str] = []
    used_write_calls: set = set()
    own_actions: List[str] = []  # proposals THIS request recorded (never diffed from the shared table)
    observations: List[str] = []
    iterations = 0

    for i in range(max_iterations):
        iterations = i + 1
        _emit(progress, "thinking", "", iterations)
        prompt = system
        if transcript:
            prompt += "\n\nConversation so far:\n" + "\n".join(transcript)
        prompt = prompt[: settings.MISSION_MAX_PROMPT_CHARS]

        res = await run_agent_task(runtime, ExecuteAgentTask(
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            variant=variant,
            session_title=f"{CEO_ASK_THREAD_TITLE}-{thread_id}" if thread_id else CEO_ASK_THREAD_TITLE,
            timeout=timeout,
            agent_id=settings.CHAT_AGENT_ID,
        ), kind=KIND_CEO_ASK)
        raw = (res.text or "").strip()
        if not raw:
            transcript.append(f"[tool_observation] ERROR: empty model reply at iteration {iterations}.")
            continue
        envelope = _parse_envelope(raw)
        if envelope is None:
            transcript.append(
                "[tool_observation] ERROR: reply was not a JSON envelope. "
                'Return exactly {"tool_call": {...}} or {"final": "..."} next.'
            )
            continue
        if isinstance(envelope.get("final"), str):
            reply = envelope["final"].strip() or _fallback_summary(observations)
            _emit(progress, "final", "", iterations)
            return _result(db, own_actions, reply, iterations)
        call = envelope.get("tool_call")
        if not isinstance(call, dict):
            transcript.append(
                "[tool_observation] ERROR: envelope must contain 'tool_call' or 'final'."
            )
            continue
        name = call.get("name")
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        spec = ceo_tools.TOOLS.get(name)
        if spec is None:
            transcript.append(
                f"[tool_observation] ERROR: unknown tool '{name}'. "
                f"Available: {sorted(ceo_tools.TOOLS)}."
            )
            continue
        if spec.kind == "write":
            key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
            if key in used_write_calls:
                transcript.append(
                    "[tool_observation] This proposal was already recorded. "
                    "Do NOT call it again — give your final answer now."
                )
                continue
            used_write_calls.add(key)
        observation = ceo_tools.run_tool(db, name, args, own_actions)
        observations.append(observation)
        transcript.append(f"[tool_observation] {observation}")
        _emit(progress, "tool", str(name), iterations)

    _emit(progress, "final", "limit", iterations)
    return _result(
        db, own_actions,
        _fallback_summary(observations) + " (Reached the reasoning limit; partial answer.)",
        iterations,
    )


def _fallback_summary(observations: List[str]) -> str:
    useful = [o for o in observations if not o.startswith("ERROR")]
    if not useful:
        return "I could not complete the request within the reasoning limit."
    return " | ".join(useful[-2:])[:2000]


async def run_ceo_fast(
    question: str,
    model_id: str,
    provider_id: str = "opencode",
    variant: Optional[str] = None,
    runtime=None,
    timeout: int = 60,
    history: Optional[List[Dict[str, Any]]] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Single-call plain-text path for small talk.

    Same result shape as run_ceo_ask: always iterations=1, never any tool
    calls (no envelope, no tool catalog in the prompt), so the model cannot
    propose actions here — actions_proposed is structurally empty.
    """
    if runtime is None:
        raise ValueError("runtime is required")
    question = (question or "").strip()
    if not question:
        raise ValueError("Empty question.")
    # PR6: one stable smalltalk thread — the GM remembers previous greetings.
    res = await run_agent_task(runtime, ExecuteAgentTask(
        prompt=build_fast_system_prompt(question, history)[: settings.MISSION_MAX_PROMPT_CHARS],
        model_id=model_id,
        provider_id=provider_id,
        variant=variant,
        session_title=f"{CEO_FAST_THREAD_TITLE}-{thread_id}" if thread_id else CEO_FAST_THREAD_TITLE,
        timeout=timeout,
        agent_id=settings.CHAT_AGENT_ID,
    ), kind=KIND_CEO_ASK_FAST)
    reply = sanitize_model_output(res.text or "").strip()
    # Unwrap harmless JSON wrappers without ever re-encoding.
    if reply.startswith("{") and reply.endswith("}"):
        try:
            data = json.loads(reply)
            if isinstance(data, dict) and isinstance(data.get("reply"), str):
                reply = data["reply"].strip() or reply
        except Exception:
            pass
    if not reply:
        reply = "حاضر معاك — لو عندك أي حاجة 말ها"
    return {"reply": reply[:2000], "actions_proposed": [], "iterations": 1}


def _result(db: Database, own_actions: List[str], reply: str, iterations: int) -> Dict[str, Any]:
    fresh = db.all("SELECT id, kind, summary FROM pending_actions WHERE status='pending'")
    proposed = [
        {"id": a["id"], "kind": a["kind"], "summary": a["summary"]}
        for a in fresh if a["id"] in own_actions
    ]
    return {"reply": reply, "actions_proposed": proposed, "iterations": iterations}
