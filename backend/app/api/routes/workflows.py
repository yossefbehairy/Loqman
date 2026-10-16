"""
Workflow CRUD & Engine Execution Routes
Supports creating, loading, updating, validating, and triggering workflow executions.
"""

import json
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db, SessionLocal
from backend.app.db.repository import WorkflowRepository, ExecutionRepository
from backend.app.schemas.workflow import WorkflowCreate, WorkflowUpdate, WorkflowResponse
from backend.app.engine.validator import validate_workflow_graph, ValidationResult
from backend.app.engine.executor import execute_workflow
from backend.app.runtime import get_runtime, AgentRuntime

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowRunRequest(BaseModel):
    input: Optional[Any] = Field(default="Start workflow execution")
    sync: bool = Field(default=False, description="Wait for completion before responding (useful for tests)")


class WorkflowRunResponse(BaseModel):
    execution_id: str
    workflow_id: str
    status: str
    topological_order: List[str]
    result: Optional[Dict[str, Any]] = None


@router.get("", response_model=List[WorkflowResponse])
def list_workflows(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    repo = WorkflowRepository(db)
    return repo.list_all(skip=skip, limit=limit)


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db)):
    repo = WorkflowRepository(db)
    return repo.create(
        name=payload.name,
        description=payload.description,
        definition_json=payload.definition_json
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(workflow_id: str, db: Session = Depends(get_db)):
    repo = WorkflowRepository(db)
    wf = repo.get_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return wf


@router.put("/{workflow_id}", response_model=WorkflowResponse)
def update_workflow(workflow_id: str, payload: WorkflowUpdate, db: Session = Depends(get_db)):
    repo = WorkflowRepository(db)
    wf = repo.update(
        workflow_id=workflow_id,
        name=payload.name,
        description=payload.description,
        definition_json=payload.definition_json
    )
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(workflow_id: str, db: Session = Depends(get_db)):
    repo = WorkflowRepository(db)
    success = repo.delete(workflow_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return None


@router.post("/{workflow_id}/validate", response_model=ValidationResult)
def validate_workflow(workflow_id: str, db: Session = Depends(get_db)):
    """
    Validates graph structure, cycle-free DAG property, terminal nodes, and agent configs.
    """
    repo = WorkflowRepository(db)
    wf = repo.get_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    try:
        definition = json.loads(wf.definition_json) if isinstance(wf.definition_json, str) else wf.definition_json
    except Exception as e:
        return ValidationResult(valid=False, errors=[f"Malformed workflow definition JSON: {e}"])

    return validate_workflow_graph(definition)


def get_session_factory():
    return SessionLocal


async def _run_execution_task(
    execution_id: str,
    wf_def: Dict[str, Any],
    initial_input: Any,
    runtime: AgentRuntime,
    session_factory=None
):
    """Background runner with its own dedicated database session."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        await execute_workflow(
            execution_id=execution_id,
            db=db,
            runtime=runtime,
            workflow_definition=wf_def,
            initial_input=initial_input
        )
    finally:
        db.close()


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(
    workflow_id: str,
    payload: Optional[WorkflowRunRequest] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    runtime: AgentRuntime = Depends(get_runtime),
    session_factory=Depends(get_session_factory)
):
    """
    Validates graph, takes immutable snapshot, creates execution record, and triggers run.
    """
    repo = WorkflowRepository(db)
    exec_repo = ExecutionRepository(db)

    wf = repo.get_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    # 1. Parse and validate definition
    try:
        wf_def = json.loads(wf.definition_json) if isinstance(wf.definition_json, str) else wf.definition_json
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid definition JSON: {e}")

    validation = validate_workflow_graph(wf_def)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Workflow graph validation failed", "errors": validation.errors}
        )

    # 2. Extract initial input
    input_data = payload.input if payload else "Start workflow execution"
    input_json_str = json.dumps(input_data, ensure_ascii=False) if not isinstance(input_data, str) else input_data

    # 3. Create immutable execution record with snapshot
    wf_exec = exec_repo.create_execution(
        workflow_id=wf.id,
        input_json=input_json_str,
        workflow_snapshot_json=wf.definition_json
    )

    # 4. Run either synchronously or in background
    sync_mode = payload.sync if payload else False
    result_data = None

    if sync_mode:
        # Run immediately in this request
        result_data = await execute_workflow(
            execution_id=wf_exec.id,
            db=db,
            runtime=runtime,
            workflow_definition=wf_def,
            initial_input=input_data
        )
        status_out = result_data.get("status", "completed")
    else:
        # Launch background task
        background_tasks.add_task(
            _run_execution_task,
            execution_id=wf_exec.id,
            wf_def=wf_def,
            initial_input=input_data,
            runtime=runtime,
            session_factory=session_factory
        )
        status_out = "pending"

    return WorkflowRunResponse(
        execution_id=wf_exec.id,
        workflow_id=wf.id,
        status=status_out,
        topological_order=validation.topological_order,
        result=result_data
    )
