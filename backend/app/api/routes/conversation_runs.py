"""Persisted engine turns for conversations + durable lifecycle events (SSE replay)."""
import asyncio
import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.run_inputs import InputFileError
from backend.app.core.run_skills import SkillSelectionError
from backend.app.core.conversation_runs import (
    ActiveRunExists, ConversationNotFound, ConversationRunManager, RunNotFound, TooManyRuns,
    UnsupportedEngine, RequestKeyConflict,
)

router = APIRouter(tags=["conversation-runs"])

_manager: Optional[ConversationRunManager] = None


def get_run_manager() -> ConversationRunManager:
    """Process-wide manager; override via app.dependency_overrides in tests."""
    global _manager
    if _manager is None:
        _manager = ConversationRunManager()
    return _manager


class SkillRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=64)
    skill_id: str = Field(min_length=1, max_length=100)


class SkillMeta(BaseModel):
    source_id: str
    skill_id: str
    relative_path: str | None = None
    sha256: str | None = None
    chars: int | None = None


class InputFileMeta(BaseModel):
    path: str | None = None
    sha256: str | None = None
    chars: int | None = None


class TurnCreate(BaseModel):
    skills: list[SkillRef] = Field(default_factory=list, max_length=3)
    input_files: list[str] = Field(default_factory=list, max_length=3)
    engine: Literal["claude-code", "codex", "opencode", "antigravity", "mock"]
    model_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=24000)
    timeout: int = Field(default=90, ge=5, le=600)
    variant: str | None = Field(default=None, max_length=64)
    request_key: str | None = Field(default=None, min_length=1, max_length=128)


class RunOut(BaseModel):
    id: str
    conversation_id: Optional[str] = None
    kind: str = "conversation"
    mission_id: Optional[str] = None
    mission_task_id: Optional[str] = None
    attempt: Optional[int] = None
    engine: str
    model_id: str
    variant: str | None = None
    status: str
    user_message_seq: Optional[int] = None
    assistant_message_seq: Optional[int] = None
    assistant_content: Optional[str] = None
    error: Optional[str] = None
    skills: list[SkillMeta] = Field(default_factory=list)
    input_files: list[InputFileMeta] = Field(default_factory=list)
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


@router.post("/api/conversations/{conversation_id}/turns", response_model=RunOut, status_code=202)
async def create_turn(conversation_id: str, payload: TurnCreate,
                      manager: ConversationRunManager = Depends(get_run_manager)):
    try:
        return await manager.start_turn_async(conversation_id, payload.engine, payload.model_id,
                                  payload.prompt, payload.timeout, payload.variant, payload.request_key,
                                  [(s.source_id, s.skill_id) for s in payload.skills],
                                  payload.input_files)
    except (SkillSelectionError, InputFileError) as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except ConversationNotFound:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except ActiveRunExists:
        raise HTTPException(status_code=409, detail="Conversation already has an active run")
    except RequestKeyConflict:
        raise HTTPException(status_code=409, detail="Request key already used for different input")
    except TooManyRuns:
        raise HTTPException(status_code=429, detail="Too many pending runs")
    except UnsupportedEngine:
        raise HTTPException(status_code=422, detail="Unsupported engine")


@router.get("/api/conversation-runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, manager: ConversationRunManager = Depends(get_run_manager)):
    try:
        return manager.get_run(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found")


def _sse(event: dict) -> str:
    # Ids are integers and data is JSON on one line, so no SSE injection is possible.
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


@router.get("/api/conversation-runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request,
                            manager: ConversationRunManager = Depends(get_run_manager)):
    """SSE lifecycle events (queued/running/succeeded/failed/cancelled/interrupted).

    Resume with the standard `Last-Event-ID` header (never a URL token). The
    stream ends after the terminal event, or if this client is too slow, in
    which case reconnect with the last id seen to replay what was missed."""
    raw = request.headers.get("last-event-id")
    last = 0
    if raw is not None and raw.strip():
        try:
            last = int(raw.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
        if last < 0 or last > 2**63 - 1:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
    try:
        await asyncio.to_thread(manager.get_run, run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found")
    if manager.subscriber_capacity_exhausted():
        raise HTTPException(status_code=429, detail="Too many event streams")

    async def body():
        yield "retry: 2000\n\n"
        try:
            async for ev in manager.iter_events(run_id, last):
                if await request.is_disconnected():
                    return
                yield ": keep-alive\n\n" if ev is None else _sse(ev)
        except (RunNotFound, TooManyRuns):
            return

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/conversation-runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: str, manager: ConversationRunManager = Depends(get_run_manager)):
    try:
        run = await manager.cancel(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] not in ("cancelled",):
        raise HTTPException(status_code=409, detail=f"Run already {run['status']}")
    return run
