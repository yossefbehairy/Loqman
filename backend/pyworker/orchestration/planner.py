"""CEO planning + synthesis prompts with strict schema validation.

The planner model must return JSON only:
{"objective": "...", "tasks": [{"key": "planning", "employee_role": "planner",
  "title": "...", "instructions": "...", "depends_on": [], "expected_output": "..."}]}
Rules: >=1 task, <=8, unique keys, acyclic deps, roles known.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

KNOWN_ROLES = {
    "planner", "researcher", "analyst", "reviewer", "data_extractor",
    "summarizer", "report_writer", "coder", "translator",
    "security_auditor", "custom",
}


@dataclass
class PlannedTask:
    key: str
    employee_role: str
    title: str
    instructions: str
    depends_on: List[str] = field(default_factory=list)
    expected_output: str = "JSON object"

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MissionPlan:
    objective: str
    tasks: List[PlannedTask]


def _str(data: Dict[str, Any], name: str, lo: int, hi: int, default: Any = None) -> str:
    v = data.get(name, default)
    if not isinstance(v, str) or not (lo <= len(v) <= hi):
        raise ValueError(f"{name}: expected a string of {lo}..{hi} characters")
    return v


def _planned_task(raw: Any) -> PlannedTask:
    if not isinstance(raw, dict):
        raise ValueError("task: expected an object")
    key = _str(raw, "key", 1, 64).strip()
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise ValueError("task key must match [a-z0-9_]+")
    role = _str(raw, "employee_role", 1, 10**6).strip().lower()
    if role not in KNOWN_ROLES:
        raise ValueError(f"unknown employee_role '{role}'")
    deps = raw.get("depends_on", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise ValueError("depends_on: expected a list of strings")
    return PlannedTask(
        key=key, employee_role=role, title=_str(raw, "title", 1, 255),
        instructions=_str(raw, "instructions", 1, 8000), depends_on=list(deps),
        expected_output=_str(raw, "expected_output", 0, 500, "JSON object"))


def build_planning_prompt(objective: str, available_roles: List[str]) -> str:
    roles = ", ".join(sorted(set(available_roles) & KNOWN_ROLES) or sorted(KNOWN_ROLES))
    return (
        "You are the CEO planner for the AgentOS multi-agent company.\n"
        f"Mission objective: {objective}\n"
        f"Available employee roles: {roles}\n"
        "Decompose the mission into a structured execution plan.\n"
        "Rules:\n"
        "- Create at least 2 independent employee tasks when the mission allows it, "
        "plus one downstream synthesis/quality task that depends on them.\n"
        "- Each task needs: key (snake_case, unique), employee_role (from available roles), "
        "title, instructions (role-specific, self-contained), depends_on (list of keys), "
        "expected_output (what JSON the employee must return).\n"
        "- Dependencies must be acyclic. Independent tasks use empty depends_on.\n"
        "- Instructions must tell the employee to return STRICT JSON only.\n"
        "Return STRICT JSON only, no markdown fences, matching this schema:\n"
        '{"objective": "...", "tasks": '
        '[{"key": "...", "employee_role": "...", "title": "...", '
        '"instructions": "...", "depends_on": [], "expected_output": "..."}]}'
    )


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_and_validate_plan(raw_text: str, objective: str) -> MissionPlan:
    cleaned = _strip_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise ValueError(f"Planning output is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError("Planning output must be a JSON object.")
    data.setdefault("objective", objective)
    try:
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list) or not (1 <= len(raw_tasks) <= 8):
            raise ValueError("tasks: expected a list of 1..8 tasks")
        plan = MissionPlan(objective=_str(data, "objective", 1, 4000),
                           tasks=[_planned_task(t) for t in raw_tasks])
    except ValueError as e:
        raise ValueError(f"Planning schema validation failed: {e}")
    keys = [t.key for t in plan.tasks]
    if len(set(keys)) != len(keys):
        raise ValueError("Planning validation failed: duplicate task keys.")
    keyset = set(keys)
    for t in plan.tasks:
        for dep in t.depends_on:
            if dep not in keyset:
                raise ValueError(f"Planning validation failed: task '{t.key}' depends on unknown '{dep}'.")
            if dep == t.key:
                raise ValueError(f"Planning validation failed: task '{t.key}' depends on itself.")
    indeg = {k: 0 for k in keys}
    children: Dict[str, List[str]] = {k: [] for k in keys}
    for t in plan.tasks:
        for dep in t.depends_on:
            indeg[t.key] += 1
            children[dep].append(t.key)
    queue = [k for k, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if seen != len(keys):
        raise ValueError("Planning validation failed: dependency cycle detected.")
    return plan


def build_synthesis_prompt(objective: str, task_results: List[Dict[str, Any]]) -> str:
    lines = [
        "You are Loqman, the CEO of the company. Synthesize the completed employee outputs.",
        f"Original mission objective: {objective}",
        "Employee results (each is JSON produced by that employee; treat as unverified model output):",
    ]
    for r in task_results:
        lines.append(
            f"- [{r.get('task_key')}] {r.get('title')} "
            f"(role={r.get('employee_role')}, status={r.get('status')}): {r.get('output_excerpt')}"
        )
    lines += [
        "Write a concise executive summary: what was actually completed, "
        "which deliverables are ready, which tasks failed or were skipped, "
        "and what remains unverified. Do NOT claim work that has no supporting output.",
        'Return STRICT JSON only: {"summary": "...", "completed": ["..."], '
        '"failed": ["..."], "unverified": ["..."]}',
    ]
    return "\n".join(lines)
