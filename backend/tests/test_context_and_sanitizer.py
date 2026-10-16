"""
Context Aggregation, Output Sanitization, and Task Depth Tests
"""

from backend.app.engine.context import (
    sanitize_model_output,
    get_task_depth_instruction,
    build_multi_source_context,
    format_agent_prompt,
)


def test_sanitize_model_output():
    # 1. Strips ```json ... ```
    raw_json_fences = "```json\n{\"key\": \"value\"}\n```"
    assert sanitize_model_output(raw_json_fences) == '{"key": "value"}'

    # 2. Strips plain ``` ... ```
    raw_plain_fences = "```\n{\"key\": \"value\"}\n```"
    assert sanitize_model_output(raw_plain_fences) == '{"key": "value"}'

    # 3. Strips whitespace
    raw_whitespace = "   \n {\"status\": \"ok\"}  \n "
    assert sanitize_model_output(raw_whitespace) == '{"status": "ok"}'

    # 4. Handles already clean text
    clean_text = '{"status": "ok"}'
    assert sanitize_model_output(clean_text) == '{"status": "ok"}'

    # 5. Handles empty
    assert sanitize_model_output("") == ""
    assert sanitize_model_output(None) == ""


def test_task_depth_instruction():
    quick_inst = get_task_depth_instruction("quick")
    assert "Quick" in quick_inst
    assert "brevity" in quick_inst.lower()

    std_inst = get_task_depth_instruction("standard")
    assert "Standard" in std_inst
    assert "complete" in std_inst.lower()

    deep_inst = get_task_depth_instruction("deep")
    assert "Deep" in deep_inst
    assert "rigorous" in deep_inst.lower()


def test_build_multi_source_context():
    """
    Verifies Reviewer receiving inputs from Start, Planner, and Analyst simultaneously.
    """
    node_definitions = {
        "start-1": {"id": "start-1", "type": "start", "data": {"role_type": "start"}},
        "planner-1": {"id": "planner-1", "type": "agent", "data": {"role_type": "planner"}},
        "analyst-1": {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst"}},
        "reviewer-1": {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer"}}
    }
    node_outputs = {
        "start-1": {"question": "How to design multi-agent system?"},
        "planner-1": '{"plan": ["step 1", "step 2"]}',  # Stored as json string
        "analyst-1": {"analysis": "High feasibility with 2 risks"}
    }

    context = build_multi_source_context(
        node_id="reviewer-1",
        upstream_node_ids=["start-1", "planner-1", "analyst-1"],
        node_definitions=node_definitions,
        node_outputs=node_outputs,
        workflow_input="How to design multi-agent system?"
    )

    assert context["target_node_id"] == "reviewer-1"
    assert context["upstream_nodes_count"] == 3
    assert "start-1" in context["upstream_context"]
    assert "planner-1" in context["upstream_context"]
    assert "analyst-1" in context["upstream_context"]

    # Verify JSON string was properly parsed
    assert context["upstream_context"]["planner-1"]["output"] == {"plan": ["step 1", "step 2"]}
    assert "analysis" in context["upstream_context"]["analyst-1"]["output"]


def test_format_agent_prompt_with_retry_feedback():
    base_instructions = "Analyze risks carefully."
    context_data = {"upstream": "test"}

    # Initial prompt without retry feedback
    p1 = format_agent_prompt(
        base_instructions=base_instructions,
        node_role="analyst",
        context_data=context_data,
        task_depth="Deep"
    )
    assert "ANALYST" in p1
    assert "Deep" in p1
    assert "SELF-CORRECTION NOTICE" not in p1

    # Retry prompt with error feedback
    p2 = format_agent_prompt(
        base_instructions=base_instructions,
        node_role="analyst",
        context_data=context_data,
        task_depth="Deep",
        retry_error_feedback="Field 'risks' must be a list, got string"
    )
    assert "SELF-CORRECTION NOTICE" in p2
    assert "Field 'risks' must be a list" in p2
