"""
CEO mission runner: real end-to-end orchestration.

Flow: persist (queued) -> planning (real model) -> dispatch tasks
(dependency levels, bounded concurrency) -> synthesis (real model).
Every state is persisted; failures are recorded, never faked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.db.models import Mission, MissionTask
from backend.app.db.repository import AgentProfileRepository, MissionRepository
from backend.app.engine.events import event_hub
from backend.app.orchestration.planner import (
    build_planning_prompt,
    build_synthesis_prompt,
    parse_and_validate_plan,
)
from backend.app.runtime.base import ExecuteAgentTask

logger = logging.getLogger("agentos.missions")

_mission_cancel: Dict[str, bool] = {}


def cancel_mission(mission_id: str) -> None:
    _mission_cancel[mission_id] = True


def is_mission_cancelled(mission_id: str) -> bool:
    return _mission_cancel.get(mission_id, False)


def _clear_cancel(mission_id: str) -> None:
    _mission_cancel.pop(mission_id, None)


def idempotency_key(objective: str, roles: List[str], model_id: str) -> str:
    h = hashlib.sha256(f"{objective.strip()}|{','.join(sorted(roles))}|{model_id}".encode()).hexdigest()
    return h[:48]


async def _broadcast(mission_id: str, event_type: str, data: Dict[str, Any]) -> None:
    payload = dict(data)
    payload.setdefault("missionId", mission_id)
    await event_hub.broadcast(mission_id, event_type, payload)


def _levels(tasks: List[MissionTask]) -> List[List[MissionTask]]:
    """Topological levels for bounded-concurrency execution."""
    by_key = {t.task_key: t for t in tasks}
    indeg = {t.task_key: 0 for t in tasks}
    children: Dict[str, List[str]] = {t.task_key: [] for t in tasks}
    for t in tasks:
        try:
            deps = json.loads(t.depends_on_json or "[]")
        except Exception:
            deps = []
        for d in deps:
            if d in by_key:
                indeg[t.task_key] += 1
                children[d].append(t.task_key)
    levels: List[List[MissionTask]] = []
    ready = [k for k, d in indeg.items() if d == 0]
    seen = set()
    while ready:
        levels.append([by_key[k] for k in ready])
        nxt: List[str] = []
        for k in ready:
            seen.add(k)
            for c in children[k]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        ready = nxt
    # Any leftover (cycle shouldn't happen post-validation) runs last.
    leftover = [t for t in tasks if t.task_key not in seen]
    if leftover:
        levels.append(leftover)
    return levels


async def _execute_single_task(
    db_factory,
    runtime,
    mission: Mission,
    task: MissionTask,
    dep_outputs: Dict[str, Any],
    sem: asyncio.Semaphore,
) -> None:
    from backend.app.db.session import SessionLocal as _SL  # local to avoid cycles

    factory = db_factory or _SL
    db = factory()
    try:
        repo = MissionRepository(db)
        t = repo.get(task.id) if hasattr(repo, "get_task") else db.query(MissionTask).filter(MissionTask.id == task.id).first()
        if not t:
            return
        # Dependency gate: skip if any dependency did not succeed.
        try:
            deps = json.loads(t.depends_on_json or "[]")
        except Exception:
            deps = []
        dep_failed = False
        for d in deps:
            dep_task = db.query(MissionTask).filter(
                MissionTask.mission_id == mission.id, MissionTask.task_key == d
            ).first()
            if not dep_task or dep_task.status != "succeeded":
                dep_failed = True
                break
        if dep_failed or is_mission_cancelled(mission.id):
            t.status = "cancelled" if is_mission_cancelled(mission.id) else "skipped"
            t.finished_at = datetime.now(timezone.utc)
            t.error_message = "Skipped: upstream dependency did not succeed." if dep_failed else "Cancelled by user."
            db.commit()
            await _broadcast(mission.id, "task.status.changed", {"taskKey": t.task_key, "status": t.status})
            return

        # Resolve employee config (role instructions + model).
        profiles = AgentProfileRepository(db)
        profile = None
        if t.agent_profile_id:
            profile = profiles.get_by_id(t.agent_profile_id)
        if not profile:
            # Fallback: first profile matching role_type.
            for p in profiles.list_all():
                if (p.role_type or "").lower() == (t.employee_role or "").lower():
                    profile = p
                    break
        instructions = (profile.instructions if profile else t.instructions) or t.instructions
        model_id = t.model_id or (profile.default_model_id if profile else None) or settings.DEFAULT_MODEL_ID
        variant = t.variant or (profile.default_variant_id if profile else None)

        context_parts = [t.input_context or ""]
        for d in deps:
            out = dep_outputs.get(d)
            if out is not None:
                excerpt = json.dumps(out, ensure_ascii=False)[:2000]
                context_parts.append(f"[Dependency {d} output]: {excerpt}")
        full_prompt = (
            f"You are the '{t.employee_role}' employee. {instructions}\n"
            f"Task: {t.title}\nDetails: {t.instructions}\n"
            f"Mission context: {(t.input_context or '')[:4000]}\n"
            + ("\n".join(context_parts[1:]) if len(context_parts) > 1 else "")
            + "\nReturn STRICT JSON only matching the expected output. No markdown fences."
        )[: settings.MISSION_MAX_PROMPT_CHARS]

        async with sem:
            for attempt in range(1, settings.MISSION_MAX_RETRIES + 2):
                if is_mission_cancelled(mission.id):
                    t.status = "cancelled"
                    t.finished_at = datetime.now(timezone.utc)
                    t.error_message = "Cancelled by user."
                    db.commit()
                    await _broadcast(mission.id, "task.status.changed", {"taskKey": t.task_key, "status": "cancelled"})
                    return
                t.status = "running"
                if attempt == 1:
                    t.started_at = datetime.now(timezone.utc)
                t.attempt_count = attempt
                db.commit()
                await _broadcast(mission.id, "task.status.changed", {"taskKey": t.task_key, "status": "running", "attempt": attempt})
                try:
                    req = ExecuteAgentTask(
                        prompt=full_prompt,
                        model_id=model_id,
                        provider_id="opencode",
                        variant=variant,
                        session_title=f"mission-{mission.id[:8]}-{t.task_key}",
                        timeout=settings.MISSION_TASK_TIMEOUT,
                    )
                    res = await asyncio.wait_for(
                        runtime.execute_task(req), timeout=settings.MISSION_TASK_TIMEOUT + 30
                    )
                    text = (res.text or "")[: settings.MISSION_MAX_OUTPUT_CHARS]
                    # Persist raw text + parsed JSON when possible.
                    try:
                        parsed: Any = json.loads(text)
                    except Exception:
                        parsed = {"_raw": text}
                    t.output_json = json.dumps(
                        {"result": parsed, "model": res.model_id, "variant": res.variant_id,
                         "session_id": res.session_id, "duration_ms": res.duration_ms},
                        ensure_ascii=False,
                    )
                    t.session_id = res.session_id
                    t.duration_ms = res.duration_ms
                    # Keep the task's model as the *recorded* model.
                    t.model_id = res.model_id
                    t.status = "succeeded"
                    t.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    dep_outputs[t.task_key] = parsed
                    await _broadcast(mission.id, "task.status.changed",
                                     {"taskKey": t.task_key, "status": "succeeded", "duration_ms": res.duration_ms})
                    return
                except asyncio.TimeoutError:
                    last_error = f"Task timed out after {settings.MISSION_TASK_TIMEOUT}s (attempt {attempt})."
                except Exception as e:
                    last_error = str(e)[:800]
                    # Normalize transient vs permanent for logging clarity.
                    logger.warning("Mission %s task %s attempt %d failed: %s", mission.id, t.task_key, attempt, last_error[:200])
                t.error_message = last_error
                db.commit()
                await _broadcast(mission.id, "task.attempt.failed",
                                 {"taskKey": t.task_key, "attempt": attempt, "error": last_error[:300]})
            # Exhausted retries.
            t.status = "failed"
            t.finished_at = datetime.now(timezone.utc)
            db.commit()
            await _broadcast(mission.id, "task.status.changed", {"taskKey": t.task_key, "status": "failed"})
    finally:
        db.close()


async def run_mission_background(mission_id: str, db_factory=None, runtime=None) -> Dict[str, Any]:
    """Background worker: planning -> dispatch -> synthesis. Never fabricates success."""
    from backend.app.db.session import SessionLocal as _SL
    from backend.app.runtime import get_runtime as _get_runtime

    factory = db_factory or _SL
    rt = runtime or _get_runtime()
    db = factory()
    try:
        repo = MissionRepository(db)
        mission = repo.get(mission_id)
        if not mission:
            return {"status": "failed", "error": "mission not found"}
        if mission.status not in ("queued",):
            return {"status": mission.status}

        mission.status = "planning"
        mission.started_at = datetime.now(timezone.utc)
        db.commit()
        await _broadcast(mission.id, "mission.status.changed", {"status": "planning"})

        # ---- Step B: CEO planning (real model) ----
        try:
            profiles = AgentProfileRepository(db)
            roles = sorted({(p.role_type or "").lower() for p in profiles.list_all() if p.role_type})
            plan_prompt = (
                __import__("backend.app.orchestration.planner", fromlist=["build_planning_prompt"])
                .build_planning_prompt(mission.objective, roles)
            )
            plan_model = mission.model_id or settings.DEFAULT_MODEL_ID
            plan_req = ExecuteAgentTask(
                prompt=plan_prompt[: settings.MISSION_MAX_PROMPT_CHARS],
                model_id=plan_model,
                provider_id="opencode",
                variant=mission.variant,
                session_title=f"mission-{mission.id[:8]}-planning",
                timeout=settings.MISSION_TASK_TIMEOUT,
            )
            plan_res = await asyncio.wait_for(rt.execute_task(plan_req), timeout=settings.MISSION_TASK_TIMEOUT + 30)
            from backend.app.orchestration.planner import parse_and_validate_plan as _parse

            plan = _parse(plan_res.text, mission.objective)
        except Exception as e:
            err = f"Planning failed: {str(e)[:800]}"
            mission.status = "failed"
            mission.error_summary = err
            mission.finished_at = datetime.now(timezone.utc)
            db.commit()
            await _broadcast(mission.id, "mission.status.changed", {"status": "failed", "error": err[:300]})
            return {"status": "failed", "error": err}

        mission.plan_json = json.dumps({"objective": plan.objective, "tasks": [t.model_dump() for t in plan.tasks]}, ensure_ascii=False)
        mission.status = "running"
        db.commit()
        await _broadcast(mission.id, "mission.status.changed", {"status": "running", "tasks": len(plan.tasks)})

        # ---- Step C/D: persist tasks + dispatch with dependencies ----
        profiles = AgentProfileRepository(db)
        by_role_profile: Dict[str, Any] = {}
        for p in profiles.list_all():
            by_role_profile.setdefault((p.role_type or "").lower(), p)
        created: List[MissionTask] = []
        for pt in plan.tasks:
            prof = by_role_profile.get(pt.employee_role)
            created.append(repo.create_task(
                mission_id=mission.id,
                task_key=pt.key,
                employee_role=pt.employee_role,
                title=pt.title,
                instructions=pt.instructions,
                input_context=mission.objective,
                depends_on=pt.depends_on,
                agent_profile_id=prof.id if prof else None,
                model_id=(prof.default_model_id if prof else None) or mission.model_id or settings.DEFAULT_MODEL_ID,
                variant=(prof.default_variant_id if prof else None) or mission.variant,
            ))
        await _broadcast(mission.id, "mission.tasks.created", {"tasks": [c.task_key for c in created]})

        sem = asyncio.Semaphore(max(1, settings.MISSION_MAX_CONCURRENCY))
        dep_outputs: Dict[str, Any] = {}
        for level in _levels(created):
            if is_mission_cancelled(mission.id):
                break
            await asyncio.gather(*[_execute_single_task(factory, rt, mission, t, dep_outputs, sem) for t in level])

        # Refresh statuses.
        db.expire_all()
        mission = repo.get(mission_id)
        tasks = db.query(MissionTask).filter(MissionTask.mission_id == mission_id).all()
        if is_mission_cancelled(mission.id):
            mission.status = "cancelled"
            for t in tasks:
                if t.status in ("queued", "running"):
                    t.status = "cancelled"
                    t.finished_at = t.finished_at or datetime.now(timezone.utc)
            mission.finished_at = datetime.now(timezone.utc)
            db.commit()
            await _broadcast(mission.id, "mission.status.changed", {"status": "cancelled"})
            return {"status": "cancelled"}

        ok = [t for t in tasks if t.status == "succeeded"]
        bad = [t for t in tasks if t.status in ("failed", "skipped", "cancelled")]

        # ---- Step F: CEO synthesis (real model, best-effort) ----
        summary_text = ""
        try:
            results = []
            for t in tasks:
                try:
                    out = json.loads(t.output_json or "{}")
                    excerpt = json.dumps(out.get("result", out), ensure_ascii=False)[:1500]
                except Exception:
                    excerpt = (t.output_json or "")[:1500]
                results.append({"task_key": t.task_key, "title": t.title, "employee_role": t.employee_role,
                                "status": t.status, "output_excerpt": excerpt})
            from backend.app.orchestration.planner import build_synthesis_prompt as _syn

            syn_req = ExecuteAgentTask(
                prompt=_syn(mission.objective, results)[: settings.MISSION_MAX_PROMPT_CHARS],
                model_id=mission.model_id or settings.DEFAULT_MODEL_ID,
                provider_id="opencode",
                variant=mission.variant,
                session_title=f"mission-{mission.id[:8]}-synthesis",
                timeout=settings.MISSION_TASK_TIMEOUT,
            )
            syn_res = await asyncio.wait_for(rt.execute_task(syn_req), timeout=settings.MISSION_TASK_TIMEOUT + 30)
            summary_text = (syn_res.text or "")[: settings.MISSION_MAX_OUTPUT_CHARS]
        except Exception as e:
            summary_text = json.dumps({"summary": f"Synthesis unavailable: {str(e)[:300]}",
                                       "completed": [t.task_key for t in ok],
                                       "failed": [t.task_key for t in bad],
                                       "unverified": [t.task_key for t in ok]})

        mission.final_summary = summary_text
        if ok and not bad:
            mission.status = "succeeded"
        elif ok and bad:
            mission.status = "partially_succeeded"
        else:
            mission.status = "failed"
            if not mission.error_summary:
                mission.error_summary = "; ".join([(t.error_message or t.task_key) for t in bad])[:1000]
        mission.finished_at = datetime.now(timezone.utc)
        db.commit()
        await _broadcast(mission.id, "mission.status.changed",
                         {"status": mission.status, "succeeded": len(ok), "failed": len(bad)})
        return {"status": mission.status, "succeeded": len(ok), "failed": len(bad)}
    finally:
        _clear_cancel(mission_id)
        db.close()
