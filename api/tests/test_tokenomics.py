"""Tokenomics: the per-agent and per-session rollups of ``model_calls`` behind the Tokenomics tab.

Every model call already records the agent identity that made it (``stage``), its tokens and its
price. These tests pin the two rollups the screen reads: ``by_agent`` across the project, and
``sessions``, one per job plus one for the calls made outside any job (Ask).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.app.models import Job, ModelCall, Project
from api.app.services import history as svc


def _call(**kw) -> ModelCall:
    base = {"provider": "claude-code", "model": "claude-opus-5-5", "tier": "strong", "tokens_in": 100,
            "tokens_out": 20, "tokens_cached": 0, "cost_usd": 0.5, "latency_ms": 1000.0, "ok": True}
    return ModelCall(**{**base, **kw})


async def _seed(app) -> str:
    """A project with two jobs: an analyse with three agents, a build with one, plus an Ask call."""
    async with app.state.sessionmaker() as s:
        project = Project(name="tokenomics")
        s.add(project)
        await s.flush()
        pid = project.id
        analyse = Job(kind="analyse", status="done", project_id=pid,
                      started_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
                      finished_at=datetime(2026, 1, 1, 9, 5, tzinfo=UTC))
        build = Job(kind="build", status="done", project_id=pid,
                    started_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC))
        s.add_all([analyse, build])
        await s.flush()
        s.add_all([
            _call(job_id=analyse.id, project_id=pid, stage="analyst", tokens_in=1000, tokens_out=200,
                  tokens_cached=300, cost_usd=1.0, latency_ms=2000.0),
            _call(job_id=analyse.id, project_id=pid, stage="analyst", tokens_in=500, tokens_out=100,
                  cost_usd=0.5, latency_ms=1000.0, model="claude-haiku-4-5", tier="efficient"),
            _call(job_id=analyse.id, project_id=pid, stage="risk", cost_usd=0.25),
            _call(job_id=analyse.id, project_id=pid, stage="challenger", cost_usd=0.25, ok=False,
                  error="boom"),
            _call(job_id=build.id, project_id=pid, stage="builder", cost_usd=2.0,
                  fallback_from="claude-code"),
            _call(job_id=None, project_id=pid, stage="ask", cost_usd=0.1),
        ])
        await s.commit()
        return pid


async def test_by_agent_rolls_calls_up_by_agent_identity(app):
    pid = await _seed(app)
    async with app.state.sessionmaker() as s:
        view = await svc.history(s, pid)

    rows = {r["agent"]: r for r in view["by_agent"]}
    assert set(rows) == {"analyst", "risk", "challenger", "builder", "ask"}

    analyst = rows["analyst"]
    assert analyst["model_calls"] == 2
    assert analyst["tokens_in"] == 1500 and analyst["tokens_out"] == 300
    assert analyst["tokens_cached"] == 300
    assert analyst["cost_usd"] == 1.5
    assert analyst["latency_ms_avg"] == 1500.0
    # both models that answered for this agent are named, not just the last one
    assert analyst["models"] == ["claude-opus-5-5", "claude-haiku-4-5"]
    assert analyst["tiers"] == ["strong", "efficient"]

    assert rows["challenger"]["failed_calls"] == 1
    assert rows["builder"]["fallbacks"] == 1
    assert rows["risk"]["failed_calls"] == 0 and rows["risk"]["fallbacks"] == 0
    # dearest first
    assert next(r["agent"] for r in view["by_agent"]) == "builder"


async def test_sessions_split_each_run_by_agent(app):
    pid = await _seed(app)
    async with app.state.sessionmaker() as s:
        view = await svc.history(s, pid)

    sessions = {x["job_kind"]: x for x in view["sessions"]}
    assert set(sessions) == {"analyse", "build", "ask"}

    analyse = sessions["analyse"]
    assert analyse["totals"]["model_calls"] == 4
    assert analyse["totals"]["cost_usd"] == 2.0
    assert {r["agent"] for r in analyse["by_agent"]} == {"analyst", "risk", "challenger"}
    assert analyse["duration_ms"] == 300_000  # five minutes

    # a call made outside any job is its own session, so every call is in exactly one
    assert sessions["ask"]["job_id"] is None
    assert sessions["ask"]["totals"]["model_calls"] == 1
    assert sum(x["totals"]["model_calls"] for x in view["sessions"]) == view["totals"]["all"]["model_calls"]


async def test_tokenomics_totals_match_the_agent_rows(app):
    """The tiles on the screen and the table under them are the same calls counted twice."""
    pid = await _seed(app)
    async with app.state.sessionmaker() as s:
        view = await svc.history(s, pid)

    total = view["totals"]["all"]
    assert sum(r["model_calls"] for r in view["by_agent"]) == total["model_calls"]
    assert sum(r["tokens_in"] for r in view["by_agent"]) == total["tokens_in"]
    assert sum(r["tokens_out"] for r in view["by_agent"]) == total["tokens_out"]
    assert round(sum(r["cost_usd"] for r in view["by_agent"]), 6) == total["cost_usd"]


async def test_history_endpoint_serves_the_tokenomics_rollups(client, app):
    pid = await _seed(app)
    r = await client.get("/history", params={"project_id": pid})
    assert r.status_code == 200
    body = r.json()
    assert [k for k in ("by_agent", "sessions") if k not in body] == []
    assert any(row["agent"] == "builder" for row in body["by_agent"])


async def test_reset_clears_the_meter_and_leaves_the_record(client, app):
    """Reset is for the number on the screen. The jobs behind it, and the audit trail, stay."""
    from sqlalchemy import select

    from api.app.models import AuditLog

    pid = await _seed(app)
    before = (await client.get("/history", params={"project_id": pid})).json()
    assert before["totals"]["all"]["model_calls"] == 6

    r = await client.delete("/history/model-calls", params={"project_id": pid},
                            headers={"X-User": "engineer.ravi"})
    assert r.status_code == 200
    assert r.json()["discarded"]["model_calls"] == 6
    assert r.json()["discarded"]["cost_usd"] == 4.1

    after = (await client.get("/history", params={"project_id": pid})).json()
    assert after["totals"]["all"]["model_calls"] == 0
    assert after["by_agent"] == [] and after["sessions"] == []
    # the runs themselves are not what was reset
    jobs = [e for e in after["timeline"] if e["kind"] == "job"]
    assert {e["job_kind"] for e in jobs} == {"analyse", "build"}
    assert not [e for e in after["timeline"] if e["kind"] == "model_call"]

    async with app.state.sessionmaker() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "tokenomics reset"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor == "engineer.ravi" and rows[0].detail["model_calls"] == 6


async def test_reset_of_an_unknown_project_is_404(client):
    r = await client.delete("/history/model-calls", params={"project_id": "nope"})
    assert r.status_code == 404
