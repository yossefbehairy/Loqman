"""
Deterministic State Machine and Transition Guard for AgentOS Workflows
Enforces allowed MVP states, dependency completion, verified outputs, and immutability.
"""

from typing import Dict, List, Set, Optional, Any
from enum import Enum


class NodeState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Strict Directed State Transitions Map
ALLOWED_NODE_TRANSITIONS: Dict[str, Set[str]] = {
    NodeState.PENDING.value: {
        NodeState.READY.value,
        NodeState.SKIPPED.value,
        NodeState.CANCELLED.value,
    },
    NodeState.READY.value: {
        NodeState.RUNNING.value,
        NodeState.CANCELLED.value,
    },
    NodeState.RUNNING.value: {
        NodeState.SUCCEEDED.value,
        NodeState.FAILED.value,
        NodeState.CANCELLED.value,
    },
    # Terminal states have zero allowed transitions
    NodeState.SUCCEEDED.value: set(),
    NodeState.FAILED.value: set(),
    NodeState.SKIPPED.value: set(),
    NodeState.CANCELLED.value: set(),
}

ALLOWED_WORKFLOW_TRANSITIONS: Dict[str, Set[str]] = {
    WorkflowState.PENDING.value: {
        WorkflowState.RUNNING.value,
        WorkflowState.CANCELLED.value,
    },
    WorkflowState.RUNNING.value: {
        WorkflowState.SUCCEEDED.value,
        WorkflowState.FAILED.value,
        WorkflowState.CANCELLED.value,
    },
    WorkflowState.SUCCEEDED.value: set(),
    WorkflowState.FAILED.value: set(),
    WorkflowState.CANCELLED.value: set(),
}


def is_valid_node_transition(current_state: str, new_state: str) -> bool:
    """Checks if a node transition is legally permitted."""
    allowed = ALLOWED_NODE_TRANSITIONS.get(current_state, set())
    return new_state in allowed


def is_valid_workflow_transition(current_state: str, new_state: str) -> bool:
    """Checks if a workflow transition is legally permitted."""
    allowed = ALLOWED_WORKFLOW_TRANSITIONS.get(current_state, set())
    return new_state in allowed


def can_node_transition_to_ready(
    upstream_node_ids: List[str],
    completed_node_states: Dict[str, str]
) -> bool:
    """
    Dependency Ordering Rule:
    A node may only transition from 'pending' to 'ready' after ALL of its upstream
    dependency nodes have achieved the 'succeeded' state.
    """
    if not upstream_node_ids:
        # Start node or zero upstream dependencies
        return True

    for parent_id in upstream_node_ids:
        parent_status = completed_node_states.get(parent_id)
        if parent_status != NodeState.SUCCEEDED.value:
            return False
    return True


def should_node_be_skipped(
    upstream_node_ids: List[str],
    completed_node_states: Dict[str, str]
) -> bool:
    """
    If any upstream dependency failed, was skipped, or was cancelled,
    downstream nodes must be skipped.
    """
    for parent_id in upstream_node_ids:
        parent_status = completed_node_states.get(parent_id)
        if parent_status in (
            NodeState.FAILED.value,
            NodeState.SKIPPED.value,
            NodeState.CANCELLED.value,
        ):
            return True
    return False


def validate_completion_output(output: Optional[Any]) -> bool:
    """
    Verified Completion Rule:
    A node or workflow must NEVER transition to 'succeeded' without a non-null,
    persisted output object/string.
    """
    if output is None:
        return False
    if isinstance(output, str) and not output.strip():
        return False
    if isinstance(output, dict) and len(output) == 0:
        return False
    return True
