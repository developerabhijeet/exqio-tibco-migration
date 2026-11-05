"""deploy/kind/worker_kill.py: which endings of a job whose worker was killed count as a pass."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "kind"))

import worker_kill as wk


class FakeCluster:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.deleted = rows, []

    def job_row(self, job_id: str) -> dict[str, Any]:
        return self.rows[0] if len(self.rows) == 1 else self.rows.pop(0)

    def delete_pod(self, name: str) -> None:
        self.deleted.append(name)

    def worker_pods(self) -> list[str]:
        return ["exiqo-worker-a", "exiqo-worker-b"]


def api(statuses: list[dict[str, Any]]) -> httpx.Client:
    """POST build queues j1; GET /jobs/j1 walks through ``statuses`` (the last one repeats)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/processes/p1/build":
            return httpx.Response(202, json={"build_id": "b1", "job_id": "j1"})
        job = statuses[0] if len(statuses) == 1 else statuses.pop(0)
        return httpx.Response(200, json={"id": "j1", "stage": "builder", "error": None, **job})

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(wk.time, "sleep", lambda s: None)


def test_worker_lost_is_a_pass() -> None:
    k = FakeCluster([{"owner": "exiqo-worker-a", "attempts": 1}])
    c = api([{"status": "running"}, {"status": "running"},
             {"status": "failed", "error": "worker lost"}])
    assert wk.attempt(c, k, "p1", timeout=60) == "pass"
    assert k.deleted == ["exiqo-worker-a"]


def test_requeued_and_finished_elsewhere_is_a_pass() -> None:
    k = FakeCluster([{"owner": "exiqo-worker-a"}, {"owner": "exiqo-worker-b", "attempts": 2}])
    c = api([{"status": "running"}, {"status": "running"}, {"status": "done"}])
    assert wk.attempt(c, k, "p1", timeout=60) == "pass"


def test_done_by_the_killed_pod_proves_nothing() -> None:
    k = FakeCluster([{"owner": "exiqo-worker-a", "attempts": 1}])
    c = api([{"status": "running"}, {"status": "running"}, {"status": "done"}])
    assert wk.attempt(c, k, "p1", timeout=60) == "inconclusive"


def test_still_running_after_the_timeout_fails(monkeypatch) -> None:
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(wk.time, "monotonic", lambda: next(clock))
    k = FakeCluster([{"owner": "exiqo-worker-a", "status": "running", "heartbeat_at": None}])
    c = api([{"status": "running"}])
    assert wk.attempt(c, k, "p1", timeout=120) == "fail"


def test_another_failure_is_not_a_pass() -> None:
    k = FakeCluster([{"owner": "exiqo-worker-a"}])
    c = api([{"status": "running"}, {"status": "running"},
             {"status": "failed", "error": "ValueError: boom"}])
    assert wk.attempt(c, k, "p1", timeout=60) == "fail"
