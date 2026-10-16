"""
Multi-Source Context Aggregation, Output Sanitization, and Task Depth Profiles
"""

import re
import json
from typing import Dict, Any, List, Optional


# ---------------------------------------------------------------------------
# Output Sanitizer: Strips Markdown code fences and extraneous whitespace
# ---------------------------------------------------------------------------
def sanitize_model_output(raw_text: str) -> str:
    """
    Strips accidental markdown code fences such as ```json ... ``` or ``` ... ```
    and leading/trailing whitespace before parsing or JSON schema validation.
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    # Strip leading ```json or ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    # Strip trailing ```
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Task Depth Instruction Profiles
# ---------------------------------------------------------------------------
TASK_DEPTH_PROFILES: Dict[str, str] = {
    "quick": (
        "[AgentOS Instruction Profile: Quick]\n"
        "- Prioritize brevity and immediate directness.\n"
        "- Deliver the essential structured output without extended deliberation or excessive narrative."
    ),
    "standard": (
        "[AgentOS Instruction Profile: Standard]\n"
        "- Provide a structured, complete, and well-organized response.\n"
        "- Verify requirements, state assumptions clearly, and ensure all output fields are populated."
    ),
    "deep": (
        "[AgentOS Instruction Profile: Deep]\n"
        "- Conduct a thorough, rigorous, multi-faceted analysis.\n"
        "- Actively identify boundary cases, operational risks, failure modes, and explicit mitigations.\n"
        "- Ensure rigorous logical consistency and comprehensive depth across all sections."
    )
}


def get_task_depth_instruction(task_depth: Optional[str]) -> str:
    """
    Resolves the AgentOS task depth prompt modifier.
    (Clearly an application-level prompt instruction modifier, not internal model reasoning guarantee).
    """
    normalized = (task_depth or "standard").lower().strip()
    return TASK_DEPTH_PROFILES.get(normalized, TASK_DEPTH_PROFILES["standard"])


# ---------------------------------------------------------------------------
# Multi-Source Context Aggregator
# ---------------------------------------------------------------------------
def build_multi_source_context(
    node_id: str,
    upstream_node_ids: List[str],
    node_definitions: Dict[str, Dict[str, Any]],
    node_outputs: Dict[str, Any],
    workflow_input: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Aggregates inputs from multiple upstream nodes into a structured context object.
    Allows downstream nodes (e.g. Reviewer) to inspect the original question from Start
    alongside intermediate outputs from Planner and Analyst simultaneously.
    """
    upstream_context: Dict[str, Any] = {}

    for parent_id in upstream_node_ids:
        parent_def = node_definitions.get(parent_id, {})
        parent_data = parent_def.get("data", {})
        parent_role = parent_data.get("role_type") or parent_def.get("type", "unknown")
        parent_output = node_outputs.get(parent_id)

        # Parse output if stored as JSON string
        parsed_output = parent_output
        if isinstance(parent_output, str):
            try:
                parsed_output = json.loads(parent_output)
            except Exception:
                parsed_output = parent_output

        upstream_context[parent_id] = {
            "node_id": parent_id,
            "role": parent_role,
            "output": parsed_output
        }

    return {
        "target_node_id": node_id,
        "workflow_initial_input": workflow_input,
        "upstream_nodes_count": len(upstream_node_ids),
        "upstream_context": upstream_context
    }


# ---------------------------------------------------------------------------
# Team-Chat Transcript & Schema-Safe Handover Excerpt
# ---------------------------------------------------------------------------
TEAM_TRANSCRIPT_MAX_CHARS = 6000
TEAM_EXCERPT_MAX_CHARS = 400

# Preferred JSON keys (in order) for a human-readable handover note.
# The model is instructed to put its conversational reply inside one of these
# so `team.message` excerpts never break Schema Validation.
HANDOVER_NOTE_KEYS = (
    "team_message",
    "handover_message",
    "team_note",
    "summary",
    "findings",
    "analysis",
    "review_status",
    "critique",
    "key_objective",
)


def extract_team_excerpt(parsed_output: Any, max_chars: int = TEAM_EXCERPT_MAX_CHARS) -> str:
    """
    Extracts a clean conversational excerpt from validated JSON output without
    breaking schema validation (reads optional handover fields first).
    """
    if parsed_output is None:
        return ""
    if isinstance(parsed_output, str):
        text = parsed_output.strip()
        return text[:max_chars] if len(text) > max_chars else text
    if isinstance(parsed_output, dict):
        for key in HANDOVER_NOTE_KEYS:
            val = parsed_output.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip()
                return text[:max_chars] if len(text) > max_chars else text
            if isinstance(val, list) and val:
                # e.g. plan_steps list -> join first items as readable note
                flat = "; ".join(str(x) for x in val[:3])
                if flat.strip():
                    text = flat.strip()
                    return text[:max_chars] if len(text) > max_chars else text
        # Fallback: compact JSON dump truncated
        try:
            text = json.dumps(parsed_output, ensure_ascii=False)
        except Exception:
            text = str(parsed_output)
        return text[:max_chars] if len(text) > max_chars else text
    text = str(parsed_output)
    return text[:max_chars] if len(text) > max_chars else text


def _short_output_summary(output: Any, limit: int = 220) -> str:
    """One-line readable summary of a node output for the transcript."""
    excerpt = extract_team_excerpt(output, max_chars=limit)
    flat = " ".join(excerpt.split())
    return flat[:limit] if len(flat) > limit else flat


def build_team_transcript(
    upstream_context: Dict[str, Any],
    node_turns: Optional[Dict[str, int]] = None,
    node_display_names: Optional[Dict[str, str]] = None,
    max_chars: int = TEAM_TRANSCRIPT_MAX_CHARS,
) -> str:
    """
    Converts upstream outputs into a chronological, readable team conversation
    log ordered by turn. Truncates oldest/longest details with a marker while
    preserving speaker names and key findings (never violates MAX_PAYLOAD_BYTES).
    """
    node_turns = node_turns or {}
    node_display_names = node_display_names or {}
    upstream = (upstream_context or {}).get("upstream_context", {}) or {}

    # Order: known turns ascending, unknown (no turn) last sorted by node id.
    def _sort_key(item) -> tuple:
        nid, _ = item
        turn = node_turns.get(nid)
        return (0, turn, nid) if isinstance(turn, int) else (1, 10**9, nid)

    ordered = sorted(upstream.items(), key=_sort_key)
    lines: List[str] = []
    for nid, info in ordered:
        role = (info or {}).get("role", "unknown")
        output = (info or {}).get("output")
        turn = node_turns.get(nid, "?")
        display = node_display_names.get(nid) or f"{role} ({nid})"
        summary = _short_output_summary(output)
        lines.append(f"[Turn {turn} | {display} | role={role}] said to team: {summary}")

    transcript = "\n".join(lines)
    if len(transcript) > max_chars:
        # Keep newest lines (tail) + marker; preserve speaker names.
        marker = "\n...[truncated for payload cap]...\n"
        budget = max_chars - len(marker)
        # Take from the end to preserve most recent (most relevant) turns.
        transcript = marker.strip() + "\n" + transcript[-budget:]
        transcript = transcript[:max_chars]
    return transcript


def format_agent_prompt(
    base_instructions: str,
    node_role: str,
    context_data: Dict[str, Any],
    task_depth: str = "Standard",
    retry_error_feedback: Optional[str] = None,
    team_transcript: Optional[str] = None,
) -> str:
    """
    Synthesizes the complete prompt for an AI Employee node:
    Role + Instructions + Task Depth + Structured Multi-Source Context
    + Team Conversation Transcript + Self-Correction Feedback.
    """
    prompt_parts = [
        f"You are the '{node_role.upper()}' AI Employee in an AgentOS workflow pipeline.",
        "",
        "## Core Instructions:",
        base_instructions,
        "",
        "## Execution Depth Setting:",
        get_task_depth_instruction(task_depth),
        "",
        "## Cumulative Upstream Context (Inputs from prior stages):",
        json.dumps(context_data, indent=2, ensure_ascii=False),
        "",
        "## Team Conversation Transcript:",
        (team_transcript.strip() if team_transcript and team_transcript.strip() else "(No upstream team messages yet — you are the first speaker.)"),
        "",
        "You are working as part of a sequential AI team. Read the Team Conversation Transcript above, explicitly acknowledge or address your upstream teammates by role/name (e.g., building on Planner's steps or auditing Analyst's findings), and advance the work rather than starting from scratch. IMPORTANT: Still return ONLY valid JSON matching your required output schema (you may include your conversational reply inside a team_message / summary field or within your structured JSON fields — never output plain text outside the JSON object).",
        "",
        "## Required Output Format:",
        "Output ONLY a valid JSON object matching your expected schema. Do NOT include markdown code fences or conversational text."
    ]

    # Append self-correcting validation error if this is a retry attempt
    if retry_error_feedback:
        prompt_parts.extend([
            "",
            "## [CRITICAL SELF-CORRECTION NOTICE FOR RETRY]:",
            "Your previous attempt failed validation with the following structural error:",
            retry_error_feedback,
            "Carefully fix the formatting, syntax, and schema mismatch above. Return valid JSON only."
        ])

    return "\n".join(prompt_parts)
