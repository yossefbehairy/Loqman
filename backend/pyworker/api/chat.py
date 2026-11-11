"""Executive chat/consultation, persisted OpenCode session mapping and the
OpenCode SDK proxy."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from backend.pyworker.appsettings import settings
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.db.core import iso, ts
from backend.pyworker.engine.context import sanitize_model_output
from backend.pyworker.errors import api_error
from backend.pyworker.runtime.base import ExecuteAgentTask
from backend.pyworker.runtime.ledger import KIND_CHAT_CONSULT, run_agent_task
from backend.pyworker.validate import bad, check_keys, opt_dict, opt_list, opt_str, req_enum, req_str

from .registry import method


def _history_item(item: Any) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("content"), str):
        raise bad("each history item must be an object with string 'content'")
    if "role" in item and not isinstance(item["role"], str):
        raise bad("history 'role' must be a string")


async def _consultation(ctx, manager_id: str, params: dict) -> Dict[str, Any]:
    message = req_str(params, "message", 1)
    history = opt_list(params, "history", 10_000, _history_item) or []
    agent_name = opt_str(params, "agent_name")
    agent_role = opt_str(params, "agent_role")
    agent_instructions = opt_str(params, "agent_instructions")
    req_model = opt_str(params, "model_id")
    req_variant = opt_str(params, "variant")

    profile = profile_repo.get_by_id(ctx.db, manager_id) if manager_id else None
    if not profile and not agent_name:
        raise api_error(404, f"Manager profile '{manager_id}' not found")

    profile_settings: Dict[str, Any] = {}
    if profile and profile["settings_json"]:
        try:
            parsed = json.loads(profile["settings_json"])
            if isinstance(parsed, dict):
                profile_settings = parsed
        except Exception:
            profile_settings = {}

    manager_name = agent_name or (profile["name"] if profile else None) or profile_settings.get("arabic_name")
    title = (agent_role or profile_settings.get("title") or (profile["description"] if profile else None)
             or "مدير الإدارة")
    department = profile_settings.get("department") or (profile["role_type"] if profile else agent_role)
    instructions = agent_instructions or (profile["instructions"] if profile else
                                          "You are a helpful AI agent. Answer the user's request directly and clearly.")
    model_id = req_model or (profile["default_model_id"] if profile else None) or "deepseek-v4.1-flash-free"
    variant = req_variant if req_model else ((profile["default_variant_id"] if profile else None) or "high")

    prompt_lines = [
        "[CONSULTATION — direct conversational reply, no JSON, no mission talk]",
        f"You are {manager_name}, {title} ({department}).",
        f"Persona instructions: {instructions}",
    ]
    if history:
        prompt_lines.append("Prior Conversation History:")
        for h in history:
            prompt_lines.append(f"- {h.get('role', 'user')}: {h['content']}")
    prompt_lines.append(f"User Message: {message}")
    prompt_lines.append(
        "Reply now as the persona, naturally and warmly, in the user's own "
        "language and dialect (Egyptian Arabic if they write Egyptian Arabic). "
        "Be yourself: a short question takes a short answer, and a real question "
        "deserves a real one with your own point of view. "
        "Plain text only — no JSON, no markdown fences, no headers, "
        "no mention of missions, tasks, sessions, or employees.")
    task = ExecuteAgentTask(
        prompt="\n".join(prompt_lines), model_id=model_id, variant=variant,
        # One thread per manager: continuity lives inside the engine session.
        session_title=f"consult-{manager_id or manager_name}", agent_id=settings.CHAT_AGENT_ID)
    result = await run_agent_task(ctx.get_runtime(), task, kind=KIND_CHAT_CONSULT)
    clean_text = sanitize_model_output(result.text)
    reply: Optional[str] = None
    try:
        data = json.loads(clean_text)
        if isinstance(data, dict):
            reply = data.get("reply") or data.get("guidance") or data.get("summary")
    except Exception:
        pass
    if not reply:
        reply = clean_text
    # A chat reply never creates or suggests an automatic mission.
    return {"reply": reply, "manager_id": manager_id, "manager_name": manager_name, "title": title,
            "suggested_mission": None}


_CONSULT_KEYS = ("message", "history", "agent_name", "agent_role", "agent_instructions", "model_id", "variant")


@method("chat.consult")
async def chat_consult(ctx, params):
    check_keys(params, {"message"}, ("manager_id", *_CONSULT_KEYS[1:]))
    return await _consultation(ctx, opt_str(params, "manager_id") or "general_manager", params)


@method("managers.consult")
async def managers_consult(ctx, params):
    check_keys(params, {"manager_id", "message"}, _CONSULT_KEYS[1:])
    return await _consultation(ctx, req_str(params, "manager_id", 1, 128), params)


# -- persisted OpenCode session mapping ------------------------------------------

def _session_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"conversation_id": row["conversation_id"], "session_id": row["session_id"],
            "model_id": row["model_id"], "provider_id": row["provider_id"], "agent_id": row["agent_id"]}


@method("chat.session.get")
async def session_get(ctx, params):
    check_keys(params, {"conversation_id"})
    row = ctx.db.one("SELECT * FROM opencode_conversations WHERE conversation_id=?",
                     (req_str(params, "conversation_id", 1, 128),))
    return {"session": _session_out(row) if row else None}


def _save_session(ctx, conversation_id, session_id, model_id, provider_id, agent_id) -> Dict[str, Any]:
    allowed = {settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID}
    if agent_id not in allowed:
        raise ValueError(f"OpenCode agent '{agent_id}' is not allowed. Allowed: {sorted(allowed)}.")
    db = ctx.db
    row = db.one("SELECT * FROM opencode_conversations WHERE conversation_id=?", (conversation_id,))
    now = ts()
    if row:
        agent_changed = row["agent_id"] != agent_id
        if row["session_id"] != session_id and not agent_changed:
            raise ValueError("This conversation already belongs to another OpenCode session.")
        # Security migration: a conversation pinned to an unrestricted agent
        # (legacy "build") is re-pointed at the locked agent.
        values = {"model_id": model_id, "provider_id": provider_id, "agent_id": agent_id, "updated_at": now}
        if agent_changed:
            values["session_id"] = session_id
        db.update("opencode_conversations", values, "conversation_id=?", (conversation_id,))
    else:
        db.insert("opencode_conversations", {
            "conversation_id": conversation_id, "session_id": session_id, "model_id": model_id,
            "provider_id": provider_id, "agent_id": agent_id, "created_at": now, "updated_at": now})
    return db.one("SELECT * FROM opencode_conversations WHERE conversation_id=?", (conversation_id,))


@method("chat.session.save")
async def session_save(ctx, params):
    check_keys(params, {"conversation_id", "session_id", "model_id", "provider_id"}, ("agent_id",))
    conversation_id = req_str(params, "conversation_id", 1, 128)
    session_id = req_str(params, "session_id", 1, 128)
    model_id = req_str(params, "model_id", 1, 128)
    provider_id = req_str(params, "provider_id", 1, 128)
    agent_id = opt_str(params, "agent_id", 1, 128, default=settings.CHAT_AGENT_ID)
    models = await ctx.get_runtime().list_available_models(free_only=True)
    if not next((m for m in models if m.id == model_id and m.provider_id == provider_id), None):
        raise api_error(400, "Selected model/provider is not verified as free and active.")
    if agent_id not in {settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID}:
        raise api_error(400, "Only AgentOS' locked OpenCode agents are supported: "
                             f"{sorted({settings.CHAT_AGENT_ID, settings.WORK_AGENT_ID})}.")
    try:
        row = _save_session(ctx, conversation_id, session_id, model_id, provider_id, agent_id)
    except ValueError as exc:
        raise api_error(409, str(exc))
    return _session_out(row)


_PROXY_PATH = re.compile(r"session(?:/[A-Za-z0-9_-]+(?:/message)?)?")


@method("chat.opencode_proxy")
async def opencode_proxy(ctx, params):
    """Local SDK transport; only session reads/prompts are exposed to the renderer."""
    check_keys(params, {"method", "path"}, ("query", "body"))
    http_method = req_enum(params, "method", ("GET", "POST"))
    path = req_str(params, "path", 1, 512)
    query = opt_str(params, "query", 0, 2048, default="") or ""
    if not _PROXY_PATH.fullmatch(path):
        raise api_error(404, "OpenCode SDK endpoint is not available through this proxy.")
    data = opt_dict(params, "body") if http_method == "POST" else None
    runtime = ctx.get_runtime()
    if path == "session" and http_method == "POST":
        model_ref = (data or {}).get("model") or {}
        models = await runtime.list_available_models(free_only=True)
        model = next((m for m in models if m.id == model_ref.get("id")
                      and m.provider_id == model_ref.get("providerID")), None)
        if not model:
            raise api_error(400, "Selected OpenCode model is not verified as free and active.")
        variant = model_ref.get("variant")
        if variant and model.variants and variant not in model.variants:
            raise api_error(400, "Selected reasoning variant is not supported by this model.")
    if path.endswith("/message") and http_method == "POST" and (data or {}).get("model"):
        model_ref = data["model"]
        models = await runtime.list_available_models(free_only=True)
        model = next((m for m in models if m.id == model_ref.get("modelID")
                      and m.provider_id == model_ref.get("providerID")), None)
        if not model:
            raise api_error(400, "Selected OpenCode model is not verified as free and active.")
        variant = (data or {}).get("variant")
        if variant and model.variants and variant not in model.variants:
            raise api_error(400, "Selected reasoning variant is not supported by this model.")
    request_api = getattr(runtime, "request_api", None)
    if not request_api:
        raise api_error(502, "Configured runtime does not support persistent OpenCode sessions.")
    api_path = f"/api/{path}" + (f"?{query}" if query else "")
    try:
        return {"data": await request_api(http_method, api_path, data)}
    except Exception:
        raise api_error(502, "OpenCode request failed.")
