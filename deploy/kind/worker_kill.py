#!/usr/bin/env python3
"""Kill a worker pod mid-build and prove the job still ends (``make kind-e2e``, after the demo).

A worker that dies without draining (OOM kill, node loss, ``kubectl delete --force``) leaves its job
``running`` with a heartbeat that stops. The other workers' reaper fails such a job with
"worker lost" once the heartbeat is older than ``JOB_STALE_SECONDS`` (20 in the CI overlay, checked
every 30 s); a worker that did get to drain requeues it and another worker finishes it. Either is
an outcome; a job left ``running`` for good is the failure this checks for.

The run rebuilds ``DealerFundingRequest`` of the newest project (the demo check approved its G3),
waits until a worker has claimed the job, reads the owner (the worker's pod name) from the jobs
table, force-deletes that pod and polls the job. Conclusive endings: failed with "worker lost", or
done by another worker (requeued). A job the killed pod finished before the kill landed proves
nothing, so the run tries again (up to ``--attempts``). Exits 0 on a conclusive ending within
``--timeout`` seconds of the kill, 1 otherwise. Prints the evidence; it never prints secrets.

    python deploy/kind/worker_kill.py --api http://exiqo.localtest.me:18080/api
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from typing import Any

import httpx

TARGET = "DealerFundingRequest"
ENGINEER = "engineer.ravi"
LOST = "worker lost"


class Cluster:
    """kubectl against the e2e cluster: the jobs table (psql in the Postgres pod) and pod deletes."""

    def __init__(self, kubectl: str, context: str, namespace: str, db_namespace: str) -> None:
        self.kubectl = [kubectl, *(["--context", context] if context else [])]
        self.ns, self.db_ns = namespace, db_namespace

    def job_row(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{8,64}", job_id):
            raise ValueError(f"unexpected job id {job_id!r}")
        sql = ("SELECT row_to_json(j) FROM (SELECT status, owner, attempts, error, heartbeat_at "
               f"FROM api.jobs WHERE id = '{job_id}') j")  # every API table is in schema api
        out = subprocess.run([*self.kubectl, "-n", self.db_ns, "exec", "deploy/postgres", "--", "psql", "-U", "exiqo",
                              "-d", "exiqo", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                             check=False, capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"reading job {job_id} from the jobs table failed: {out.stderr.strip()}")
        return json.loads(out.stdout.strip() or "{}")

    def delete_pod(self, name: str) -> None:
        subprocess.run([*self.kubectl, "-n", self.ns, "delete", "pod", name, "--grace-period=0", "--force",
                        "--wait=false"], check=True, capture_output=True, text=True)

    def worker_pods(self) -> list[str]:
        out = subprocess.run([*self.kubectl, "-n", self.ns, "get", "pods", "-l", "app.kubernetes.io/component=worker",
                              "-o", "jsonpath={.items[*].metadata.name}"], capture_output=True, text=True,
                             check=True).stdout
        return out.split()


def say(text: str) -> None:
    print(f"worker-kill: {text}", flush=True)


def target_process(c: httpx.Client) -> str:
    projects = c.get("/projects").json()
    if not projects:
        raise SystemExit("worker-kill: no project; run the demo check first")
    newest = max(projects, key=lambda p: p.get("created_at") or "")
    procs = {p["name"]: p["id"] for p in c.get(f"/projects/{newest['id']}/processes").json()}
    if TARGET not in procs:
        raise SystemExit(f"worker-kill: {TARGET} not in project {newest['id']}")
    return procs[TARGET]


def attempt(c: httpx.Client, k: Cluster, process_id: str, timeout: float) -> str:
    """One kill; returns "pass", "fail" or "inconclusive"."""
    r = c.post(f"/processes/{process_id}/build", json={"runtime": "container_app"}, headers={"X-User": ENGINEER})
    if r.status_code != 202:
        say(f"build not queued: HTTP {r.status_code} {r.text[:200]}")
        return "fail"
    job_id = r.json()["job_id"]
    say(f"build {r.json()['build_id']} queued as job {job_id}; workers {k.worker_pods()}")
    owner, deadline = None, time.monotonic() + 120
    while time.monotonic() < deadline:
        job = c.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            say(f"job ended ({job['status']}) before it could be seen running")
            return "inconclusive"
        if job["status"] == "running":
            owner = k.job_row(job_id).get("owner")
            if owner:
                break
        time.sleep(0.2)
    if not owner:
        say("no worker claimed the job within 120 s")
        return "fail"
    stage = c.get(f"/jobs/{job_id}").json().get("stage")
    k.delete_pod(owner)
    killed_at = time.monotonic()
    say(f"job {job_id} running on {owner} (stage {stage!r}); force-deleted pod {owner}")
    while time.monotonic() - killed_at < timeout:
        job = c.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            row = k.job_row(job_id)
            waited = time.monotonic() - killed_at
            say(f"job {job_id} {job['status']} after {waited:.0f} s: error {job.get('error')!r}, "
                f"owner {row.get('owner')}, attempts {row.get('attempts')}")
            if job["status"] == "failed" and job.get("error") == LOST:
                say("PASS: the reaper failed the lost worker's job with \"worker lost\"")
                return "pass"
            if job["status"] == "done" and row.get("owner") != owner:
                say(f"PASS: requeued and finished by {row.get('owner')}")
                return "pass"
            if job["status"] == "done":
                say(f"the killed pod finished the job before the kill took effect (stage was {stage!r})")
                return "inconclusive"
            say(f"FAIL: unexpected outcome {job['status']} {job.get('error')!r}")
            return "fail"
        time.sleep(2)
    row = k.job_row(job_id)
    say(f"FAIL: job {job_id} still {row.get('status')} {timeout:.0f} s after the kill "
        f"(owner {row.get('owner')}, last heartbeat {row.get('heartbeat_at')})")
    return "fail"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", required=True, help="the API through the ingress, http://<host>:<port>/api")
    ap.add_argument("--kubectl", default="kubectl")
    ap.add_argument("--context", default="", help="kubeconfig context (default: the current one)")
    ap.add_argument("--namespace", default="exiqo")
    ap.add_argument("--db-namespace", default="exiqo-db")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds after the kill (default 120)")
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args(argv)

    k = Cluster(args.kubectl, args.context, args.namespace, args.db_namespace)
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=httpx.Timeout(60.0, connect=10.0)) as c:
        process_id = target_process(c)
        for n in range(1, args.attempts + 1):
            say(f"attempt {n} of {args.attempts}")
            outcome = attempt(c, k, process_id, args.timeout)
            if outcome != "inconclusive":
                return 0 if outcome == "pass" else 1
    say(f"FAIL: no conclusive kill in {args.attempts} attempts (every build finished before the kill)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
