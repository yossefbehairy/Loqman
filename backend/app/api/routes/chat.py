"""
Executive Chat & Manager Direct Consultation Route
Handles interactive manager consultation (R1) with authentic Arabic personas
for Dr. Sara, Eng. Tarek, Prof. Yasmin, and General Manager.
"""

import json
import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.app.db.session import get_db
from backend.app.db.repository import AgentProfileRepository, OpenCodeConversationRepository
from backend.app.runtime import get_runtime, AgentRuntime
from backend.app.runtime.base import ExecuteAgentTask
from backend.app.engine.context import sanitize_model_output

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = "user"  # "user" | "assistant" | "system"
    content: str


class ManagerConsultationRequest(BaseModel):
    manager_id: Optional[str] = None
    message: str = Field(min_length=1)
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    agent_name: Optional[str] = None
    agent_role: Optional[str] = None
    agent_instructions: Optional[str] = None
    model_id: Optional[str] = None
    variant: Optional[str] = None


class SuggestedMission(BaseModel):
    title: str
    input_json: Dict[str, Any] = Field(default_factory=dict)


class ManagerConsultationResponse(BaseModel):
    reply: str
    manager_id: str
    manager_name: str
    title: str
    suggested_mission: Optional[SuggestedMission] = None


class ConversationSessionSave(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(default="build", min_length=1, max_length=128)


class ConversationSessionOut(ConversationSessionSave):
    conversation_id: str


async def _handle_consultation(
    manager_id: str,
    payload: ManagerConsultationRequest,
    db: Session,
    runtime: AgentRuntime
) -> ManagerConsultationResponse:
    repo = AgentProfileRepository(db)
    profile = repo.get_by_id(manager_id) if manager_id else None
    if not profile and not payload.agent_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Manager profile '{manager_id}' not found"
        )

    # Extract persona details
    settings: Dict[str, Any] = {}
    if profile and profile.settings_json:
        try:
            parsed = json.loads(profile.settings_json)
            if isinstance(parsed, dict):
                settings = parsed
        except Exception:
            settings = {}

    if not isinstance(settings, dict):
        settings = {}

    manager_name = payload.agent_name or settings.get("arabic_name") or profile.name
    title = payload.agent_role or settings.get("title") or (profile.description if profile else None) or "مدير الإدارة"
    department = settings.get("department") or (profile.role_type if profile else payload.agent_role)
    instructions = payload.agent_instructions or (profile.instructions if profile else "You are a helpful AI agent. Answer the user's request directly and clearly.")
    model_id = payload.model_id or (profile.default_model_id if profile else None) or "deepseek-v4.1-flash-free"
    variant = payload.variant if payload.model_id else ((profile.default_variant_id if profile else None) or "high")

    # Build consultation prompt — conversational, not bureaucratic.
    # The model must reply as the persona itself in a natural chat tone
    # (same language/dialect as the user), never as a report or JSON,
    # and never mention missions, sessions, or internal mechanics.
    prompt_lines = [
        f"[CONSULTATION — direct conversational reply, no JSON, no mission talk]",
        f"You are {manager_name}, {title} ({department}).",
        f"Persona instructions: {instructions}",
    ]
    if payload.history:
        prompt_lines.append("Prior Conversation History:")
        for h in payload.history:
            prompt_lines.append(f"- {h.role}: {h.content}")

    prompt_lines.append(f"User Message: {payload.message}")
    prompt_lines.append(
        "Reply now as the persona, naturally and warmly, in the user's own "
        "language and dialect (Egyptian Arabic if they write Egyptian Arabic). "
        "Be concise, be yourself, answer what was asked and nothing more. "
        "Plain text only — no JSON, no markdown fences, no headers, "
        "no mention of missions, tasks, sessions, or employees."
    )
    full_prompt = "\n".join(prompt_lines)

    task = ExecuteAgentTask(
        prompt=full_prompt,
        model_id=model_id,
        variant=variant,
        session_title=f"consult-{uuid.uuid4().hex[:8]}"
    )

    result = await runtime.execute_task(task)
    clean_text = sanitize_model_output(result.text)

    reply: Optional[str] = None
    suggested_mission: Optional[SuggestedMission] = None

    try:
        data = json.loads(clean_text)
        if isinstance(data, dict):
            reply = data.get("reply") or data.get("guidance") or data.get("summary")
            raw_mission = data.get("suggested_mission")
            if isinstance(raw_mission, dict) and "title" in raw_mission:
                suggested_mission = SuggestedMission(
                    title=raw_mission["title"],
                    input_json=raw_mission.get("input_json", {})
                )
    except Exception:
        pass

    if not reply:
        reply = clean_text
    # A chat reply never creates or suggests an automatic mission.
    suggested_mission = None

    return ManagerConsultationResponse(
        reply=reply,
        manager_id=manager_id,
        manager_name=manager_name,
        title=title,
        suggested_mission=suggested_mission
    )


@router.post("/chat", response_model=ManagerConsultationResponse)
async def chat_consultation(
    payload: ManagerConsultationRequest,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime)
):
    """
    Direct executive consultation endpoint (R1).
    Chats with Dr. Sara, Eng. Tarek, Prof. Yasmin, or General Manager.
    """
    manager_id = payload.manager_id or "general_manager"
    return await _handle_consultation(manager_id, payload, db, runtime)


@router.post("/managers/{manager_id}/consult", response_model=ManagerConsultationResponse)
async def manager_consultation_by_id(
    manager_id: str,
    payload: ManagerConsultationRequest,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime)
):
    """
    Direct consultation with a specific manager via path parameter.
    """
    return await _handle_consultation(manager_id, payload, db, runtime)


@router.get("/chat/sessions/{conversation_id}", response_model=Optional[ConversationSessionOut])
def get_conversation_session(conversation_id: str, db: Session = Depends(get_db)):
    row = OpenCodeConversationRepository(db).get(conversation_id)
    if not row:
        return None
    return ConversationSessionOut(
        conversation_id=row.conversation_id,
        session_id=row.session_id,
        model_id=row.model_id,
        provider_id=row.provider_id,
        agent_id=row.agent_id,
    )


@router.post("/chat/sessions/{conversation_id}", response_model=ConversationSessionOut)
async def save_conversation_session(
    conversation_id: str,
    payload: ConversationSessionSave,
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime),
):
    models = await runtime.list_available_models(free_only=True)
    model = next((m for m in models if m.id == payload.model_id and m.provider_id == payload.provider_id), None)
    if not model:
        raise HTTPException(status_code=400, detail="Selected model/provider is not verified as free and active.")
    if payload.agent_id != "build":
        raise HTTPException(status_code=400, detail="Only the configured OpenCode primary agent 'build' is supported.")
    try:
        row = OpenCodeConversationRepository(db).save(
            conversation_id, payload.session_id, payload.model_id, payload.provider_id, payload.agent_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return ConversationSessionOut(
        conversation_id=row.conversation_id,
        session_id=row.session_id,
        model_id=row.model_id,
        provider_id=row.provider_id,
        agent_id=row.agent_id,
    )


@router.api_route("/opencode-proxy/{path:path}", methods=["GET", "POST"])
async def opencode_sdk_proxy(path: str, request: Request, runtime: AgentRuntime = Depends(get_runtime)):
    """Local SDK transport; only session reads/prompts are exposed to the renderer."""
    import re

    if not re.fullmatch(r"session(?:/[A-Za-z0-9_-]+(?:/message)?)?", path):
        raise HTTPException(status_code=404, detail="OpenCode SDK endpoint is not available through this proxy.")
    data: Optional[Dict[str, Any]] = None
    if request.method == "POST":
        try:
            raw = await request.json()
            data = raw if isinstance(raw, dict) else None
        except Exception:
            data = None
    if path == "session" and request.method == "POST":
        model_ref = (data or {}).get("model") or {}
        models = await runtime.list_available_models(free_only=True)
        model = next((m for m in models if m.id == model_ref.get("id") and m.provider_id == model_ref.get("providerID")), None)
        if not model:
            raise HTTPException(status_code=400, detail="Selected OpenCode model is not verified as free and active.")
        variant = model_ref.get("variant")
        if variant and model.variants and variant not in model.variants:
            raise HTTPException(status_code=400, detail="Selected reasoning variant is not supported by this model.")
    if path.endswith("/message") and request.method == "POST" and (data or {}).get("model"):
        model_ref = data["model"]
        models = await runtime.list_available_models(free_only=True)
        model = next((m for m in models if m.id == model_ref.get("modelID") and m.provider_id == model_ref.get("providerID")), None)
        if not model:
            raise HTTPException(status_code=400, detail="Selected OpenCode model is not verified as free and active.")
        variant = (data or {}).get("variant")
        if variant and model.variants and variant not in model.variants:
            raise HTTPException(status_code=400, detail="Selected reasoning variant is not supported by this model.")
    request_api = getattr(runtime, "request_api", None)
    if not request_api:
        raise HTTPException(status_code=502, detail="Configured runtime does not support persistent OpenCode sessions.")
    api_path = f"/api/{path}"
    if request.url.query:
        api_path += f"?{request.url.query}"
    try:
        return await request_api(request.method, api_path, data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenCode request failed: {str(exc)[:300]}")
