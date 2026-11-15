"""CEO control plane: ask (read-only reasoning loop) + pending-action approvals.

- ``ceo.ask``       bounded tool loop -> {reply, actions_proposed, iterations, fast}
- ``ceo.actions``   pending action inbox
- ``ceo.approve`` / ``ceo.deny``
    * require ``confirm: true`` (local UI confirmation; replaces the legacy
      ``X-AgentOS-Confirm`` header) else 403, so no caller can auto-approve.
    * only pending actions transition; anything else is 409 (no double execution).
    * approve for update_contract rechecks expected_updated_at (optimistic
      concurrency): a newer contract edit wins, the proposal fails instead.
    * approve for new_profile / new_team creates profiles (a team is validated
      completely before the first write).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.pyworker.appsettings import settings
from backend.pyworker.core import preferences as prefs_mod
from backend.pyworker.core import profiles as profile_repo
from backend.pyworker.db.core import Database, iso, iso_utc, new_id, ts
from backend.pyworker.engine.events import event_hub
from backend.pyworker.errors import WorkerError, api_error
from backend.pyworker.orchestration.agent_loop import run_ceo_ask, run_ceo_fast
from backend.pyworker.orchestration.ceo_tools import ALLOWED_CONTRACT_FIELDS
from backend.pyworker.orchestration import routing
from backend.pyworker.orchestration.planner import KNOWN_ROLES
from backend.pyworker.runtime.providers import engine_title, ledger_provider
from backend.pyworker.validate import check_keys, opt_enum, opt_int, opt_str, req_str

from .registry import method

# Conversation memory (backend-owned): recent turns feed every prompt.
CEO_HISTORY_TURNS = 8
_ASK_PROGRESS_LIMIT = 100

_SMALLTALK_PATTERNS = (
    "صباح الخير", "مساء الخير", "اهلا", "أهلا", "هلا", "اهلاً", "أهلاً",
    "ازيك", "إزيك", "عامل ايه", "عاملة ايه", "عامل إيه", "عاملة إيه",
    "تمام", "شكرا", "شكراً", "شكرًا", "متشكر", "مرحبا", "سلام",
    "مين انت", "مين إنت", "انت مين", "إنت مين", "بتعمل ايه", "بتعمل إيه",
    "hello", "hi", "hey", "good morning", "good evening", "good night",
    "thanks", "thank you", "مرحبتين",
)
_DATA_INTENT_PATTERNS = (
    "مهمة", "مهمه", "مهام", "mission", "task", "فريق", "team",
    "موظف", "employee", "profile", "شخص", "تقرير", "report", "summary", "ملخص",
    "اعرض", "أعرض", "انشئ", "أنشئ", "انشاء", "أضف", "اضف", "جدول", "timestamp",
    "عدل", "عدّل", "تعديل", "احذف", "امسح", "approve", "موافقة", "موافقه",
    "حالة", "الحاله", "status", "بيانات", "data", "أعطني", "اعطني", "give me",
    "احكيلي عن", "ايه اللي", "إيه اللي", "القائم", "قائمة", "قائمتك",
)
_SMALLTALK_MAX_CHARS = 120


def _word_safe(pattern: str) -> str:
    """ASCII small-talk words must not match inside other words ('hi' in 'this')."""
    return rf"\b{re.escape(pattern)}\b" if pattern.isascii() else re.escape(pattern)


_SMALLTALK_RE = re.compile("|".join(_word_safe(p) for p in _SMALLTALK_PATTERNS), re.IGNORECASE)
_DATA_INTENT_RE = re.compile("|".join(re.escape(p) for p in _DATA_INTENT_PATTERNS))


def _is_smalltalk(question: str) -> bool:
    q = question.strip().lower()
    if not q or len(q) > _SMALLTALK_MAX_CHARS:
        return False
    if _DATA_INTENT_RE.search(q):
        return False
    return _SMALLTALK_RE.search(q) is not None


def _progress_write(ctx, request_id: str, stage: str, detail: str = "",
                    iteration: Optional[int] = None, done: bool = False) -> None:
    record = ctx.ask_progress.get(request_id)
    if record is None:
        return
    record["stage"] = stage
    record["detail"] = (detail or "")[:200]
    if iteration is not None:
        record["iteration"] = iteration
    record["done"] = done
    record["updated_at"] = datetime.now(timezone.utc).isoformat()


_CONV_CONTENT_CAP = 60000


def _store_message(db: Database, conversation_id: str, role: str, content: str, client_id: str) -> bool:
    """Idempotent write into a chat (unique conversation + client id). True when this call inserted the row."""
    content = content[:_CONV_CONTENT_CAP] or "(empty reply)"
    row = db.one("SELECT role, content FROM conversation_messages WHERE conversation_id=? AND client_message_id=?",
                 (conversation_id, client_id))
    if row:
        if row["role"] != role or row["content"] != content:
            raise api_error(409, "Request id already used for a different message.")
        return False
    now = ts()
    with db.tx():
        db.execute("INSERT INTO conversation_messages(conversation_id,client_message_id,role,content,created_at) "
                   "VALUES(?,?,?,?,?)", (conversation_id, client_id, role, content, now))
        db.update("conversations", {"updated_at": now}, "id=?", (conversation_id,))
    return True


@method("ceo.ask")
async def ceo_ask(ctx, params):
    check_keys(params, {"question"}, ("model_id", "variant", "request_id", "conversation_id", "engine"))
    question = req_str(params, "question", 1, 2000).strip()
    if not question:
        raise api_error(400, "Question must not be empty.")
    engine_in = opt_str(params, "engine", 1, 64)
    model_in = opt_str(params, "model_id")
    variant = opt_str(params, "variant")
    progress_id = (opt_str(params, "request_id", 0, 64) or "").strip()
    conversation_id = (opt_str(params, "conversation_id", 1, 64) or "").strip() or None
    if conversation_id and not ctx.db.one("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
        raise api_error(404, "Conversation not found.")
    # Engine and model: the caller's explicit choice, else the worker default (Settings). "Free only" is the
    # engine's own policy (OpenCode), checked against that engine's catalog.
    default_engine = prefs_mod.default_engine_id()
    if engine_in is not None and engine_in != "mock" and engine_in not in prefs_mod.ENGINE_IDS:
        raise api_error(400, f"Unsupported engine '{engine_in[:64]}'.")
    if engine_in == "mock" and settings.RUNTIME_MODE != "mock":
        raise api_error(400, "Engine 'mock' is only available in explicit mock mode.")
    if settings.RUNTIME_MODE == "mock" and engine_in not in (None, "mock"):
        raise api_error(400, "Real engines are unavailable in mock mode.")
    engine = engine_in or default_engine
    if engine == default_engine:
        runtime = ctx.get_runtime()
        model_id = model_in or prefs_mod.default_model_id()
    else:
        if not model_in:
            raise api_error(400, f"A model_id is required with engine '{engine}'.")
        model_id = model_in
        try:
            runtime = ctx.engine_factory(engine)
        except Exception:
            raise api_error(503, f"{engine_title(engine)} is unavailable.")
    models = await routing.load_catalog(runtime, engine)
    target = routing.check_model(models, engine, model_id, variant)
    provider_id = target.provider_id if engine in routing.OPENCODE_STYLE else ledger_provider(engine)

    is_fast = _is_smalltalk(question)
    fast_variant = variant
    if is_fast and fast_variant is None and engine in routing.OPENCODE_STYLE:
        fast_variant = "low" if (target.variants and "low" in target.variants) else None
    action_ids: List[str] = []
    active_id = progress_id or new_id()
    if active_id:
        now_iso = datetime.now(timezone.utc).isoformat()
        ctx.ask_progress[active_id] = {
            "stage": "fast" if is_fast else "starting", "detail": "", "iteration": 1 if is_fast else 0,
            "started_at": now_iso, "updated_at": now_iso, "done": False, "action_ids": action_ids}
        if len(ctx.ask_progress) > _ASK_PROGRESS_LIMIT:
            for stale in list(ctx.ask_progress)[:-_ASK_PROGRESS_LIMIT]:
                if ctx.ask_progress[stale].get("done"):
                    ctx.ask_progress.pop(stale, None)

    def _report(stage: str, detail: str, iteration: int) -> None:
        if progress_id:
            _progress_write(ctx, progress_id, stage, detail, iteration)

    db = ctx.db
    # Backend-owned memory: recent turns feed every prompt and every exchange is recorded.
    record_cap = 12000
    ask_key = progress_id or new_id()
    user_key, reply_key = f"ask:{ask_key}:user", f"ask:{ask_key}:assistant"
    stored_user = False
    if conversation_id:
        # A chat remembers only itself: its own stored exchange, not the company-wide log. The current question
        # is read out of the history (it may already be stored by an earlier attempt with the same request id).
        recent = db.all("SELECT role, content FROM conversation_messages WHERE conversation_id=? "
                        "AND role IN ('user', 'assistant') AND COALESCE(client_message_id, '') <> ? "
                        "ORDER BY seq DESC LIMIT ?", (conversation_id, user_key, 2 * CEO_HISTORY_TURNS))
        # Durable before generating: a reload mid-generation still finds the user's message in its chat.
        stored_user = _store_message(db, conversation_id, "user", question, user_key)
    else:
        recent = db.all("SELECT role, content FROM ceo_messages ORDER BY seq DESC, id DESC LIMIT ?",
                        (2 * CEO_HISTORY_TURNS,))
    history = [{"role": m["role"], "content": m["content"]} for m in reversed(recent)]
    next_seq = 0
    if not conversation_id:
        # Unscoped exchanges (no chat of their own) live only in the company-wide log. Their ids carry a "g"
        # prefix so conversations.import_ceo can tell them from rows written while scoped asks were also logged.
        next_seq = (db.scalar("SELECT MAX(seq) FROM ceo_messages") or 0) + 1
        db.insert("ceo_messages", {"id": "g" + uuid.uuid4().hex, "seq": next_seq, "role": "user",
                                   "content": question[:record_cap], "fast": 1 if is_fast else 0, "created_at": ts()})
    try:
        if is_fast:
            result = await run_ceo_fast(question=question, model_id=model_id, provider_id=provider_id,
                                        variant=fast_variant, runtime=runtime, history=history,
                                        thread_id=conversation_id, engine=engine)
        else:
            result = await run_ceo_ask(question=question, model_id=model_id, provider_id=provider_id,
                                       variant=variant, runtime=runtime, db=db,
                                       progress=_report if progress_id else None, history=history,
                                       thread_id=conversation_id, engine=engine, action_ids=action_ids)
    except Exception:
        ctx.ask_progress[active_id]["action_ids"] = []
        if stored_user:
            # Nothing was answered: take the question back so the restored draft can be re-sent without a duplicate.
            db.execute("DELETE FROM conversation_messages WHERE conversation_id=? AND client_message_id=?",
                       (conversation_id, user_key))
        _progress_write(ctx, active_id, "error", "CEO ask failed.", done=True)
        raise api_error(502, "CEO ask failed.")
    if not conversation_id:
        db.insert("ceo_messages", {"id": "g" + uuid.uuid4().hex, "seq": next_seq + 1, "role": "assistant",
                                   "content": result["reply"][:record_cap], "fast": 1 if is_fast else 0,
                                   "created_at": ts()})
    # The CEO may create agents and teams on its own: no approval step for those two kinds.
    # Agents and teams it creates are stamped with the engine and model of this ask (explicit values).
    stamp = {"engine": None if engine == "mock" else engine, "model_id": model_id,
             "valid": None if engine == "mock" else {m.id for m in models}}
    created_agents: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    try:
        for action in result["actions_proposed"]:
            if action["kind"] in ("new_profile", "new_team"):
                outcome = _decide(ctx, action["id"], True, "Created by the CEO", True, stamp)
                if outcome["status"] == "approved":
                    created_agents.extend(outcome["created_profiles"])
                    continue
            remaining.append(action)
    finally:
        ctx.ask_progress[active_id]["action_ids"] = []
        _progress_write(ctx, active_id, "done", "", result["iterations"], done=True)
    if conversation_id:
        made = [p["name"] for p in created_agents]
        label = "Agent created" if len(made) == 1 else "Agents created"
        note = f"\n\n✓ {label}: {', '.join(made)}" if made else ""
        _store_message(db, conversation_id, "assistant", f"{result['reply']}{note}", reply_key)
    return {"reply": result["reply"], "actions_proposed": remaining, "agents_created": created_agents,
            "iterations": result["iterations"], "fast": is_fast}


@method("ceo.ask_progress")
async def ceo_ask_progress(ctx, params):
    check_keys(params, {"request_id"})
    record = ctx.ask_progress.get(req_str(params, "request_id", 1, 64).strip())
    if record is None:
        raise api_error(404, "Unknown ask request id.")
    return {"stage": record["stage"], "detail": record.get("detail", ""), "iteration": record.get("iteration", 0),
            "started_at": record.get("started_at"), "updated_at": record.get("updated_at"),
            "done": record.get("done", False)}


def _action_out(a: Dict[str, Any], created_profiles=None, team_name=None) -> Dict[str, Any]:
    return {"id": a["id"], "kind": a["kind"], "summary": a["summary"], "status": a["status"],
            "created_by": a["created_by"], "decided_at": iso(a["decided_at"]),
            "decision_note": a["decision_note"], "created_profiles": created_profiles or [],
            "team_name": team_name}


@method("ceo.actions")
async def ceo_actions(ctx, params):
    check_keys(params, optional=("status",))
    status = opt_str(params, "status") or "pending"
    if status not in ("pending", "approved", "denied", "failed"):
        raise api_error(400, "Unknown action status.")
    rows = ctx.db.all("SELECT * FROM pending_actions WHERE status=? ORDER BY created_at DESC", (status,))
    hidden = {aid for record in ctx.ask_progress.values() for aid in record.get("action_ids", [])}
    rows = [a for a in rows if not (a["id"] in hidden and a["kind"] in ("new_profile", "new_team"))]
    return {"actions": [_action_out(a) for a in rows]}


def _apply_update_contract(db: Database, payload: Dict[str, Any]) -> None:
    prof = profile_repo.get_by_id(db, payload.get("profile_id", ""))
    if not prof:
        raise ValueError("Target profile no longer exists.")
    changes = {k: v for k, v in (payload.get("changes") or {}).items() if k in ALLOWED_CONTRACT_FIELDS}
    if not changes:
        raise ValueError("No applicable contract changes.")
    expected = payload.get("expected_updated_at")
    if expected and prof["updated_at"]:
        current = iso_utc(prof["updated_at"])
        if current and current > str(expected):
            raise ValueError("Contract changed after this proposal (newer edit wins) — refusing overwrite.")
    values = {k: v for k, v in changes.items() if v is not None}
    values["updated_at"] = ts()
    db.update("agent_profiles", values, "id=?", (prof["id"],))


def _default_stamp() -> Dict[str, Any]:
    """Stamp for creations that are not tied to an ask (a manual approval): the worker default."""
    engine = prefs_mod.default_engine_id()
    return {"engine": None if engine == "mock" else engine, "model_id": prefs_mod.default_model_id(),
            "valid": None if engine == "mock" else set()}


def _stamped(proposal_model: Any, proposal_variant: Any, stamp: Dict[str, Any]) -> Tuple[str, Any]:
    """(model, variant) for a new agent: the proposal's own model only when this engine offers it, else the
    engine/model in effect. A variant is kept only with a model the proposal chose for an OpenCode-style engine."""
    valid = stamp.get("valid")
    own = isinstance(proposal_model, str) and bool(proposal_model) and (
        valid is None or proposal_model in valid or proposal_model == stamp["model_id"])
    model = proposal_model if own else stamp["model_id"]
    keep_variant = own and (stamp["engine"] in (None, "opencode"))
    return model, (proposal_variant if keep_variant else None)


def _apply_new_profile(db: Database, payload: Dict[str, Any], stamp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    stamp = stamp or _default_stamp()
    role = str(payload.get("role_type", "")).lower()
    if role not in KNOWN_ROLES:
        role = "custom"
    model, variant = _stamped(payload.get("default_model_id"), payload.get("default_variant_id"), stamp)
    data = {"id": f"custom-{uuid.uuid4().hex[:8]}", "name": payload.get("name"), "role_type": role,
            "description": payload.get("description"), "instructions": payload.get("instructions"),
            "default_model_id": model, "default_variant_id": variant, "engine": stamp["engine"]}
    if not data["name"] or not data["instructions"]:
        raise ValueError("New profile requires name and instructions.")
    return profile_repo.create(db, data)


def _apply_new_team(db: Database, payload: Dict[str, Any],
                    stamp: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], str]:
    """Create all team member profiles in one decision. The WHOLE batch is
    validated BEFORE the first write; a failed team leaves no zombie rows."""
    stamp = stamp or _default_stamp()
    team_name = str(payload.get("name") or "").strip()
    if not team_name:
        raise ValueError("New team requires a name.")
    members = payload.get("members")
    if not isinstance(members, list) or not (1 <= len(members) <= 8):
        raise ValueError("New team requires 1..8 members.")
    planned: List[Dict[str, Any]] = []
    for i, member in enumerate(members, start=1):
        if not isinstance(member, dict):
            raise ValueError(f"Team member #{i} must be an object.")
        role = str(member.get("role_type", "")).strip().lower()
        if role not in KNOWN_ROLES:
            role = "custom"
        name = str(member.get("name") or "").strip()
        if not name or not member.get("instructions"):
            raise ValueError(f"Team member #{i} requires name and instructions.")
        model, variant = _stamped(member.get("default_model_id"), member.get("default_variant_id"), stamp)
        planned.append({"name": name, "role_type": role, "description": member.get("description"),
                        "instructions": member.get("instructions"),
                        "default_model_id": model, "default_variant_id": variant, "engine": stamp["engine"],
                        # The UI derives team membership only from settings_json.team_name.
                        "settings_json": json.dumps({"team_name": team_name}, ensure_ascii=False)})
    created = [profile_repo.create(db, {"id": f"custom-{uuid.uuid4().hex[:8]}", **m}) for m in planned]
    return created, team_name


def _decide(ctx, action_id: str, approve: bool, note: Optional[str], confirm: Any,
            stamp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if confirm is not True:
        raise api_error(403, "Deciding actions requires local UI confirmation (confirm: true).")
    db = ctx.db
    if not db.one("SELECT id FROM pending_actions WHERE id=?", (action_id,)):
        raise api_error(404, f"Action '{action_id}' not found.")
    now = ts()
    # The claim lives in the same transaction as the action's database changes.
    with db.tx():
        if not db.update("pending_actions", {"status": "executing"}, "id=? AND status='pending'", (action_id,)):
            raise api_error(409, "Action already decided or being processed.")
        action = db.one("SELECT * FROM pending_actions WHERE id=?", (action_id,))
        if not approve:
            db.update("pending_actions", {"status": "denied", "decided_at": now, "decision_note": note},
                      "id=?", (action_id,))
            return _action_out(db.one("SELECT * FROM pending_actions WHERE id=?", (action_id,)))

        created: List[Dict[str, Any]] = []
        team_name: Optional[str] = None
        try:
            with db.tx():  # savepoint: a failed apply leaves no partial profiles
                payload = json.loads(action["payload_json"] or "{}")
                if action["kind"] == "update_contract":
                    _apply_update_contract(db, payload)
                elif action["kind"] == "new_profile":
                    created = [_apply_new_profile(db, payload, stamp)]
                elif action["kind"] == "new_team":
                    created, team_name = _apply_new_team(db, payload, stamp)
                else:
                    raise ValueError(f"Unknown action kind '{action['kind']}'.")
        except WorkerError:
            raise
        except Exception as e:
            message = str(e)
            if isinstance(e, ValueError) and "has unknown role" in message:
                decision_note = "Team member has unknown role."
            elif isinstance(e, ValueError) and "requires name and instructions" in message:
                decision_note = "Team member requires name and instructions."
            else:
                decision_note = "Action failed."
            db.update("pending_actions", {"status": "failed", "decided_at": now, "decision_note": decision_note},
                      "id=?", (action_id,))
            return _action_out(db.one("SELECT * FROM pending_actions WHERE id=?", (action_id,)))
        db.update("pending_actions", {"status": "approved", "decided_at": now, "decision_note": note},
                  "id=?", (action_id,))
        out = _action_out(db.one("SELECT * FROM pending_actions WHERE id=?", (action_id,)),
                          created_profiles=[{"id": p["id"], "name": p["name"], "role_type": p["role_type"]}
                                            for p in created], team_name=team_name)
    return out


async def _decide_and_broadcast(ctx, params, approve: bool):
    check_keys(params, {"action_id"}, ("confirm", "note"))
    action_id = req_str(params, "action_id", 1, 64)
    out = _decide(ctx, action_id, approve, opt_str(params, "note", 0, 1000), params.get("confirm"))
    await event_hub.broadcast(action_id, "ceo.action.decided", {"actionId": action_id, "status": out["status"]})
    return out


@method("ceo.approve")
async def ceo_approve(ctx, params):
    return await _decide_and_broadcast(ctx, params, True)


@method("ceo.deny")
async def ceo_deny(ctx, params):
    return await _decide_and_broadcast(ctx, params, False)
