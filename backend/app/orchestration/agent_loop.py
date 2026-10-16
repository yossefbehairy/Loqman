"""
CEO self-reasoning loop (ReAct over the single-shot runtime).

Each iteration asks the model for exactly one JSON envelope:
  {"tool_call": {"name": "...", "args": {...}}}  or  {"final": "..."}
Tool results return as "[tool_observation] ..." transcript lines.
Hard cap: 6 iterations, then the best collected summary is returned.
Write tools only ever RECORD pending proposals (see ceo_tools) — the loop
itself cannot mutate contracts or profiles.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import PendingAction
from backend.app.engine.context import sanitize_model_output
from backend.app.orchestration import ceo_tools
from backend.app.runtime.base import ExecuteAgentTask

MAX_ITERATIONS = 6


def build_loop_system_prompt(question: str) -> str:
    return (
        "ceo tools — you are the General Manager (المدير العام) answering with live company data.\n"
        "You have these tools (call each at most once per distinct need):\n"
        f"{ceo_tools.tool_catalog_prompt()}\n"
        "Rules:\n"
        "- Every reply must be EXACTLY one JSON object, no other text, no markdown fences:\n"
        '  {"tool_call": {"name": "<tool>", "args": {...}}}  OR  {"final": "<answer>"}.\n'
        "- Use tools to look up real data before answering factual questions.\n"
        "- propose_* tools only RECORD a proposal (status pending) — never execute anything. "
        "After recording one, do NOT call it again for the same change; "
        "mention in your final answer that it awaits approval.\n"
        "- Answer in the user's own language and dialect (Egyptian Arabic if they write it).\n"
        "- Never invent mission ids, statuses, or profile data — only quote tool observations.\n"
        f"User question: {question.strip()}"
    )


def _parse_envelope(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(sanitize_model_output(text))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def run_ceo_ask(
    question: str,
    model_id: str,
    provider_id: str = "opencode",
    variant: Optional[str] = None,
    runtime=None,
    db: Optional[Session] = None,
    max_iterations: int = MAX_ITERATIONS,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the bounded tool loop. Returns {reply, actions_proposed, iterations}."""
    if runtime is None or db is None:
        raise ValueError("runtime and db are required")
    question = (question or "").strip()
    if not question:
        raise ValueError("Empty question.")

    system = build_loop_system_prompt(question)
    transcript: List[str] = []
    used_write_calls: set = set()
    baseline_pending = {a.id for a in db.query(PendingAction).all()}
    observations: List[str] = []
    iterations = 0
    tag = uuid.uuid4().hex[:8]

    for i in range(max_iterations):
        iterations = i + 1
        prompt = system
        if transcript:
            prompt += "\n\nConversation so far:\n" + "\n".join(transcript)
        prompt = prompt[: settings.MISSION_MAX_PROMPT_CHARS]

        res = await runtime.execute_task(ExecuteAgentTask(
            prompt=prompt,
            model_id=model_id,
            provider_id=provider_id,
            variant=variant,
            session_title=f"ceo-ask-{tag}",
            timeout=timeout,
        ))
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
            return _result(db, baseline_pending, reply, iterations)
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
        observation = ceo_tools.run_tool(db, name, args)
        observations.append(observation)
        transcript.append(f"[tool_observation] {observation}")

    return _result(
        db, baseline_pending,
        _fallback_summary(observations) + " (Reached the reasoning limit; partial answer.)",
        iterations,
    )


def _fallback_summary(observations: List[str]) -> str:
    useful = [o for o in observations if not o.startswith("ERROR")]
    if not useful:
        return "I could not complete the request within the reasoning limit."
    return " | ".join(useful[-2:])[:2000]


def _result(db: Session, baseline_pending: set, reply: str, iterations: int) -> Dict[str, Any]:
    fresh = (
        db.query(PendingAction)
        .filter(PendingAction.status == "pending")
        .all()
    )
    proposed = [
        {"id": a.id, "kind": a.kind, "summary": a.summary}
        for a in fresh if a.id not in baseline_pending
    ]
    return {"reply": reply, "actions_proposed": proposed, "iterations": iterations}
