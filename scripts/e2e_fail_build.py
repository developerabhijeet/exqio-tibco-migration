"""Record a finished build as failed, for the e2e run: a failed ``build`` step with a compiler error
and a failing test, as the build job stores them (``services/build``). The Build tab, the failures an
iteration collects and Re-enter design then read it like a real failed build, whether or not NuGet
was reachable when the build ran (so the e2e is deterministic).

    python scripts/e2e_fail_build.py <build id>

``DATABASE_URL`` names the database (the e2e API's). The build's files on disk are left as they
are, so its download still works.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete

from api.app.db import make_engine, make_sessionmaker
from api.app.models import Build, TestResult

COMPILER_ERROR = "src/Gmf.Core/Rules/FundingStatusLookupConditions.cs(12,9): error CS0103: The name 'tier' does not exist"
FAILING_TEST = "Gmf.Tests.FundingStatusLookupRulesTests.R1_funding_status_is_returned"


async def fail(build_id: str) -> None:
    engine = make_engine(os.environ.get("DATABASE_URL", "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo"))
    try:
        async with make_sessionmaker(engine)() as s:
            build = await s.get(Build, build_id)
            if build is None:
                raise SystemExit(f"build {build_id} not found")
            if build.status in ("queued", "running"):
                raise SystemExit(f"build {build_id} has not finished")
            steps = [st for st in (build.steps or []) if st.get("name") not in ("build", "test")]
            steps.append({"name": "build", "ok": False, "skipped": False, "seconds": 1.0,
                          "log": f"Build FAILED.\n{COMPILER_ERROR}\n    1 Error(s)"})
            build.steps = steps  # a new list, so SQLAlchemy sees the JSONB change
            build.status = "failed"
            build.error = "the build failed: 1 compiler error"
            await s.execute(delete(TestResult).where(TestResult.build_id == build_id, TestResult.name == FAILING_TEST))
            s.add(TestResult(build_id=build_id, name=FAILING_TEST, display_name=FAILING_TEST, kind="rule",
                             outcome="failed", has_body=True, message="Assert.Equal() Failure: expected 200, actual 503"))
            await s.commit()
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    asyncio.run(fail(argv[0]))
    print(argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
