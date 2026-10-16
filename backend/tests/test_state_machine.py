"""
State Machine and Transition Guard Tests
"""

from backend.app.engine.state_machine import (
    NodeState,
    WorkflowState,
    is_valid_node_transition,
    is_valid_workflow_transition,
    can_node_transition_to_ready,
    should_node_be_skipped,
    validate_completion_output,
)


def test_valid_node_transitions():
    assert is_valid_node_transition("pending", "ready") is True
    assert is_valid_node_transition("pending", "skipped") is True
    assert is_valid_node_transition("pending", "cancelled") is True
    assert is_valid_node_transition("ready", "running") is True
    assert is_valid_node_transition("ready", "cancelled") is True
    assert is_valid_node_transition("running", "succeeded") is True
    assert is_valid_node_transition("running", "failed") is True
    assert is_valid_node_transition("running", "cancelled") is True


def test_invalid_node_transitions():
    # Direct jump to succeeded without running is illegal
    assert is_valid_node_transition("pending", "succeeded") is False
    assert is_valid_node_transition("ready", "succeeded") is False
    # Terminal states cannot transition
    assert is_valid_node_transition("succeeded", "running") is False
    assert is_valid_node_transition("failed", "ready") is False
    assert is_valid_node_transition("cancelled", "running") is False


def test_workflow_transitions():
    assert is_valid_workflow_transition("pending", "running") is True
    assert is_valid_workflow_transition("running", "succeeded") is True
    assert is_valid_workflow_transition("running", "failed") is True
    assert is_valid_workflow_transition("running", "cancelled") is True
    assert is_valid_workflow_transition("succeeded", "running") is False


def test_can_node_transition_to_ready():
    # Node depends on planner-1 and analyst-1
    upstreams = ["planner-1", "analyst-1"]

    # When both succeeded -> True
    states_all_ok = {"planner-1": "succeeded", "analyst-1": "succeeded"}
    assert can_node_transition_to_ready(upstreams, states_all_ok) is True

    # When one is still running -> False
    states_one_running = {"planner-1": "succeeded", "analyst-1": "running"}
    assert can_node_transition_to_ready(upstreams, states_one_running) is False

    # When one is pending -> False
    states_one_pending = {"planner-1": "pending", "analyst-1": "succeeded"}
    assert can_node_transition_to_ready(upstreams, states_one_pending) is False


def test_should_node_be_skipped():
    upstreams = ["planner-1", "analyst-1"]

    # If any upstream failed -> should be skipped
    states_with_failure = {"planner-1": "failed", "analyst-1": "succeeded"}
    assert should_node_be_skipped(upstreams, states_with_failure) is True

    # If all upstreams succeeded -> should NOT be skipped
    states_all_succeeded = {"planner-1": "succeeded", "analyst-1": "succeeded"}
    assert should_node_be_skipped(upstreams, states_all_succeeded) is False


def test_validate_completion_output():
    # Valid outputs
    assert validate_completion_output({"summary": "done"}) is True
    assert validate_completion_output("Output string") is True

    # Invalid outputs
    assert validate_completion_output(None) is False
    assert validate_completion_output("") is False
    assert validate_completion_output("   ") is False
    assert validate_completion_output({}) is False
