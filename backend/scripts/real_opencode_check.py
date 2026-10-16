"""
REAL OpenCode integration test — explicitly triggered, never mocked.

Verifies against the user's actual configured OpenCode runtime:
1. Runtime reachable (health ok).
2. muse-spark-1.3-contributor-free available + free (0/0).
3. A real task is submitted and a real response returns.
4. Model identity recorded accurately.
5. Output saved (mission E2E persists tasks + summary).
6. Output visible via the API (same records the UI reads).

Run:  .\.venv\Scripts\python.exe scripts\real_opencode_check.py
(Backend workdir: D:\\AgentOS\\backend — imports resolve via D:\\AgentOS.)

Exit 0 = all checks passed. Any failure exits non-zero with evidence.
Never falls back to mock, never touches paid models.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if False else r"D:\AgentOS")

from backend.app.runtime.opencode_runtime import RealOpenCodeRuntime
from backend.app.runtime.base import ExecuteAgentTask

EXPECTED_MODEL = "muse-spark-1.3-contributor-free"
MISSION_OBJECTIVE = (
    "Integration probe: describe in one short paragraph how a CEO, planner, "
    "and reviewer would collaborate on a small software task. Return STRICT JSON "
    'only: {"collaboration": "..."}'
)


def check(cond: bool, label: str, evidence: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {evidence}" if evidence else ""))
    if not cond:
        raise SystemExit(f"FAILED: {label} {evidence}")


async def main() -> None:
    rt = RealOpenCodeRuntime()

    # 1. Reachable.
    health = await rt.check_health()
    check(health.status == "ok", "runtime reachable (health ok)",
          f"status={health.status} models={health.models_count} free={health.free_models_count}")
    print(f"       service_url={health.service_url} default_model={health.default_model_status}")

    # 2. Expected free model available.
    free_models = await rt.list_available_models(free_only=True)
    spark = [m for m in free_models if m.id == EXPECTED_MODEL]
    check(len(spark) == 1, f"model '{EXPECTED_MODEL}' is available and free",
          f"free_count={len(free_models)} variants={spark[0].variants if spark else None}")
    check(spark[0].cost.input == 0 and spark[0].cost.output == 0, "model cost is 0/0 (free-only)",
          f"cost={spark[0].cost}")
    check(spark[0].status == "active", "model status is active")

    # 3+4. Real task -> real response, identity recorded.
    task = ExecuteAgentTask(
        prompt=MISSION_OBJECTIVE,
        model_id=EXPECTED_MODEL,
        provider_id=spark[0].provider_id,
        variant="minimal",
        session_title="agentos-real-integration-probe",
        timeout=150,
    )
    res = await rt.execute_task(task)
    check(bool(res.text and res.text.strip()), "real response returned", f"chars={len(res.text)}")
    check(res.model_id == EXPECTED_MODEL or "spark" in res.model_id.lower(),
          "model identity recorded accurately", f"model_id={res.model_id} variant={res.variant_id}")
    print(f"       duration_ms={res.duration_ms} session={res.session_id} cost={res.cost}")
    print(f"       text excerpt: {res.text[:300]}")
    try:
        parsed = json.loads(res.text)
        check(isinstance(parsed, dict) and "collaboration" in parsed,
              "response is parseable JSON with expected key")
    except Exception as e:
        check(False, "response is parseable JSON", str(e)[:200])

    # 5+6. Full mission E2E with the REAL runtime (persisted + visible via API).
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.app.db.session import Base
    from backend.app.main import app
    from backend.app.db.session import get_db
    from backend.app.runtime import set_runtime
    from backend.app.db.repository import AgentProfileRepository
    from backend.app.api.routes.workflows import get_session_factory
    from backend.app.api.routes.missions import get_mission_session_factory

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    seed_db = TestingSessionLocal()
    AgentProfileRepository(seed_db).seed_defaults()
    seed_db.close()

    def override_get_db():
        s = TestingSessionLocal()
        try:
            yield s
        finally:
            s.close()

    set_runtime(rt)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: TestingSessionLocal
    app.dependency_overrides[get_mission_session_factory] = lambda: TestingSessionLocal
    try:
        with TestClient(app) as tc:
            cr = tc.post("/api/missions", json={
                "objective": "Probe mission: have Planning draft a 3-step rollout and Quality review it.",
                "model_id": EXPECTED_MODEL,
                "variant": "minimal",
            })
            check(cr.status_code == 201, "mission accepted (real runtime)", f"http={cr.status_code}")
            mid = cr.json()["id"]
            import time
            final = None
            for _ in range(120):
                g = tc.get(f"/api/missions/{mid}")
                st = g.json()["status"]
                if st in ("succeeded", "partially_succeeded", "failed", "cancelled"):
                    final = g.json()
                    break
                time.sleep(2)
            check(final is not None, "mission reached terminal state")
            print(f"       mission status={final['status']} tasks={len(final['tasks'])}")
            for t in final["tasks"]:
                print(f"       - {t['task_key']} [{t['employee_role']}] {t['status']} "
                      f"model={t.get('model_id')} attempts={t.get('attempt_count')} dur={t.get('duration_ms')}")
            check(final["status"] in ("succeeded", "partially_succeeded"),
                  "mission succeeded with real outputs", f"status={final['status']} err={final.get('error_summary')}")
            ok_tasks = [t for t in final["tasks"] if t["status"] == "succeeded" and t.get("output")]
            check(len(ok_tasks) >= 2, "at least two employee tasks persisted real outputs",
                  f"ok={len(ok_tasks)}")
            check(final.get("final_summary") is not None, "CEO synthesis persisted")
    finally:
        app.dependency_overrides.clear()

    print("\nALL REAL INTEGRATION CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
