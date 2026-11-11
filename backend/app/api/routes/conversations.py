"""Persistent conversations and ordered messages."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import CeoMessage, Conversation, ConversationMessage, Workspace
from backend.app.db.session import get_db

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, max_length=36)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    workspace_id: str | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=60000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    conversation_id: str
    role: str
    content: str
    client_message_id: str | None
    created_at: datetime


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)):
    if payload.workspace_id and db.get(Workspace, payload.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    row = Conversation(title=payload.title, workspace_id=payload.workspace_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db)):
    return db.query(Conversation).order_by(Conversation.updated_at.desc(), Conversation.id).limit(500).all()


@router.post("/import/ceo")
def import_ceo_history(db: Session = Depends(get_db)):
    """Copy legacy CEO messages once while retaining the original history."""
    conversation_id = "legacy-ceo-history"
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        conversation = Conversation(id=conversation_id, title="CEO history")
        db.add(conversation)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="History import is already in progress")
    existing_ids = {row[0] for row in db.query(ConversationMessage.client_message_id)
                    .filter_by(conversation_id=conversation_id).all()}
    old_messages = db.query(CeoMessage).order_by(CeoMessage.seq, CeoMessage.id).all()
    imported = 0
    for old in old_messages:
        key = f"ceo:{old.id}"
        if key in existing_ids or old.role not in ("user", "assistant"):
            continue
        db.add(ConversationMessage(conversation_id=conversation_id, client_message_id=key,
                                   role=old.role, content=old.content, created_at=old.created_at))
        imported += 1
    if imported:
        conversation.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="History import is already in progress")
    return {"conversation_id": conversation_id, "imported": imported}


def _conversation(db: Session, conversation_id: str) -> Conversation:
    row = db.get(Conversation, conversation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(conversation_id: str, after: int = 0, limit: int = 200, db: Session = Depends(get_db)):
    if after < 0 or not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    _conversation(db, conversation_id)
    return (db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.seq > after).order_by(ConversationMessage.seq).limit(limit).all())


@router.post("/{conversation_id}/messages", response_model=MessageOut)
def add_message(conversation_id: str, payload: MessageCreate, db: Session = Depends(get_db)):
    conversation = _conversation(db, conversation_id)

    def existing():
        if not payload.client_message_id:
            return None
        return db.query(ConversationMessage).filter_by(
            conversation_id=conversation_id, client_message_id=payload.client_message_id).first()

    row = existing()
    if row:
        if row.role != payload.role or row.content != payload.content:
            raise HTTPException(status_code=409, detail="Message id already used for different content")
        return row
    row = ConversationMessage(conversation_id=conversation_id, **payload.model_dump())
    db.add(row)
    conversation.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = existing()
        if row and row.role == payload.role and row.content == payload.content:
            return row
        raise HTTPException(status_code=409, detail="Message id already used for different content")
    db.refresh(row)
    return row
