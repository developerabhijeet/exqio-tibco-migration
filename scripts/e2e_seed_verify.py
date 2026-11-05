"""Record a verify run for a build from a parity fixture, for the e2e run (no generated service
has to start): one scenario run per report and one field diff per field and side effect, as the
verify job stores them (``services/verify``). The parity view and the Jira issue source then read
it like a real run.

    python scripts/e2e_seed_verify.py <build id> web/e2e/fixtures/parity.json

``DATABASE_URL`` names the database (the e2e API's). Prints the job id of the seeded run.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from api.app.db import make_engine, make_sessionmaker, new_id
from api.app.models import Build, FieldDiff, Scenario, ScenarioRun

REPORT_KEYS = ("run_id", "scenario_key", "scenario_name", "status", "error")


async def seed(build_id: str, fixture: Path) -> str:
    parity = json.loads(fixture.read_text(encoding="utf-8"))
    engine = make_engine(os.environ.get("DATABASE_URL", "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo"))
    job_id = f"e2e-seed-{new_id()[:8]}"
    try:
        async with make_sessionmaker(engine)() as s:
            # Only the columns this needs: the script runs against whatever schema the API migrated.
            process_id = await s.scalar(select(Build.process_id).where(Build.id == build_id))
            if process_id is None:
                raise SystemExit(f"build {build_id} not found")
            for rep in parity["reports"]:
                key = rep["scenario_key"]
                scenario = await s.scalar(select(Scenario).where(Scenario.process_id == process_id,
                                                                Scenario.key == key))
                if scenario is None:
                    scenario = Scenario(process_id=process_id, key=key, name=rep.get("scenario_name") or key,
                                        data={})
                    s.add(scenario)
                    await s.flush()
                report: dict[str, Any] = {k: v for k, v in rep.items() if k not in REPORT_KEYS}
                run = ScenarioRun(build_id=build_id, scenario_id=scenario.id, job_id=job_id, status=rep["status"],
                                  report=report, parity=rep.get("parity"))
                s.add(run)
                await s.flush()
                for kind, rows in (("field", rep.get("fields") or []), ("side_effect", rep.get("side_effects") or [])):
                    for d in rows:
                        s.add(FieldDiff(run_id=run.id, kind=kind, field=d["path"], expected=d.get("expected"),
                                        actual=d.get("actual"), classification=d["status"],
                                        finding_key=d.get("finding_id")))
            await s.commit()
    finally:
        await engine.dispose()
    return job_id


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    print(asyncio.run(seed(argv[0], Path(argv[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
