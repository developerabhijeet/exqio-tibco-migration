#!/usr/bin/env python3
"""Acceptance run (design spec section 12) against a running API.

Drives the API end to end on the synthetic estate: create a project, upload the synthetic zip,
scan, discover (domains, naming, graph), analyze ``DealerFundingRequest`` and
``CreditApplicationIntake``, decide rules and findings and approve G1 to G3 as named users, build,
check the generated infrastructure, verify parity and run the plan in connect-later mode. Prints a
pass/fail table and exits non-zero when any row failed.

Two modes, because two parts of the run need the internet:

* ``full``: NuGet (or the offline cache in ``.tools/nuget-cache``) and the Terraform/OpenTofu
  provider registry are reachable. The spec's assertions as written: every test green, the
  infrastructure validated, the service started, parity 100% on the generated scenarios except
  the intended fields tied to findings decided Fix.
* ``offline``: a probe failed. The same flow, asserting the honest failure states instead:
  restore failed with the NuGet reason and the ``make nuget-cache`` remedy, build and test
  skipped, G4 not enabled, the service not running and verify refused, the infrastructure
  validation reporting the unreachable registry. The plan still runs in connect-later mode.

Without ``--mode`` the probes decide. The script starts nothing: ``make demo-check`` starts an API
when none answers. It never prints key values.

    python scripts/demo_check.py [--api URL] [--mode full|offline] [--zip PATH]

Without ``--api`` the API URL comes from API_URL, else from API_PORT in the environment or .env
(the same key the Makefile and the web read), so this never probes a port the API has moved off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]


def default_api_url() -> str:
    """API_URL, else API_PORT from the environment or .env, else the port the Makefile defaults to."""
    if os.environ.get("API_URL"):
        return os.environ["API_URL"]
    port = os.environ.get("API_PORT")
    if not port:
        env_file = REPO / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "API_PORT" and value.strip():
                    port = value.strip()
    return f"http://localhost:{port or '9000'}"
TARGETS = ("DealerFundingRequest", "CreditApplicationIntake")
SME, ARCHITECT, ENGINEER, OPS = "sme.vicky", "architect.lee", "engineer.ravi", "ops.sam"
NUGET_INDEX = "https://api.nuget.org/v3/index.json"
REGISTRIES = {"opentofu": "https://registry.opentofu.org/.well-known/terraform.json",
              "terraform": "https://registry.terraform.io/.well-known/terraform.json"}
JOB_TIMEOUT = {"scan": 300, "discover": 300, "analyse": 600, "design": 600, "build": 1500, "verify": 900}
MASKED_SSN = re.compile(r"^(\*{3}-\*{2}-\d{4}|\*+)$")  # last four kept, or fully masked
RAW_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


class StepFailed(Exception):
    """A check the rest of the step depends on failed; the step stops here."""


@dataclass
class Row:
    step: str
    check: str
    ok: bool
    detail: str


@dataclass
class Run:
    api: str
    rows: list[Row] = field(default_factory=list)

    def check(self, step: str, name: str, ok: bool, detail: Any = "", *, fatal: bool = False) -> bool:
        self.rows.append(Row(step, name, bool(ok), _short(detail)))
        if fatal and not ok:
            raise StepFailed(f"{step}: {name}")
        return bool(ok)


def _short(value: Any, limit: int = 110) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ------------------------------------------------------------------ probes
def probe(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 - any failure means unreachable
        return False, f"{type(exc).__name__}: {_short(str(exc), 70)}"


def nuget_probe() -> tuple[bool, str]:
    cache = REPO / ".tools" / "nuget-cache"
    if cache.is_dir() and any(cache.iterdir()):
        return True, f"offline cache present at {cache.relative_to(REPO)}"
    ok, why = probe(NUGET_INDEX)
    return ok, f"api.nuget.org {'answered' if ok else 'unreachable'} ({why})"


def registry_probe(terraform_version: str | None) -> tuple[bool, str]:
    name = "opentofu" if "opentofu" in (terraform_version or "").lower() else "terraform"
    ok, why = probe(REGISTRIES[name])
    host = REGISTRIES[name].split("/")[2]
    return ok, f"{host} {'answered' if ok else 'unreachable'} ({why})"


# ------------------------------------------------------------------ the flow
class Flow:
    def __init__(self, run: Run, client: httpx.Client, mode: str, zip_path: Path) -> None:
        self.run, self.c, self.mode, self.zip = run, client, mode, zip_path
        self.full = mode == "full"
        self.project_id = ""
        self.process_ids: dict[str, str] = {}
        self.builds: dict[str, str] = {}

    # helpers
    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.c.get(path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.c.post(path, **kw)

    def wait_job(self, job_id: str, kind: str) -> dict[str, Any]:
        deadline = time.monotonic() + JOB_TIMEOUT.get(kind, 600)
        while time.monotonic() < deadline:
            job = self.get(f"/jobs/{job_id}").json()
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(1.5)
        raise StepFailed(f"{kind} job {job_id} did not finish within {JOB_TIMEOUT.get(kind, 600)} s")

    def job_ok(self, step: str, job: dict[str, Any]) -> None:
        self.run.check(step, f"{job['kind']} job finished", job["status"] == "done",
                       job.get("error") or f"status {job['status']}", fatal=True)

    # steps
    def preflight(self) -> None:
        s = "preflight"
        health = self.get("/health").json()
        self.run.check(s, "API /health", health.get("status") == "ok", f"provider {health.get('provider')}",
                       fatal=True)
        tools = self.get("/settings").json().get("toolchains") or {}
        for name in ("dotnet", "terraform", "terragrunt"):
            t = tools.get(name) or {}
            self.run.check(s, f"{name} present", t.get("present") is True, t.get("version") or "missing")
        self.run.check(s, "synthetic zip on disk", self.zip.is_file(), str(self.zip), fatal=True)

    def upload(self) -> None:
        s = "upload"
        project = self.post("/projects", json={"name": f"acceptance {time.strftime('%Y-%m-%d %H:%M:%S')}"})
        self.run.check(s, "project created", project.status_code == 201, project.status_code, fatal=True)
        self.project_id = project.json()["id"]
        with self.zip.open("rb") as fh:
            up = self.post(f"/projects/{self.project_id}/upload",
                           files={"file": (self.zip.name, fh, "application/zip")})
        self.run.check(s, "zip uploaded", up.status_code == 201, up.text if up.status_code != 201 else
                       f"{len(up.json()['files'])} files, root {up.json()['scan_root']}", fatal=True)
        files = up.json()["files"]
        bwp = sum(1 for f in files if f["path"].endswith(".bwp"))
        self.run.check(s, "BW6 process files listed", bwp > 0, f"{bwp} .bwp files")

    def scan(self) -> None:
        s = "scan"
        job = self.wait_job(self.post(f"/projects/{self.project_id}/scan").json()["job_id"], "scan")
        self.job_ok(s, job)
        rep = self.get(f"/projects/{self.project_id}/report").json()
        self.run.check(s, "every file parsed", rep["ok"] and rep["files_parsed"] == rep["files_found"],
                       f"{rep['files_parsed']} of {rep['files_found']} files, "
                       f"{len(rep['unknown_activity_types'])} unknown activity types")
        self.run.check(s, "estate labeled synthetic", rep["synthetic"] is True, rep["synthetic"])
        procs = self.get(f"/projects/{self.project_id}/processes").json()
        self.process_ids = {p["name"]: p["id"] for p in procs}
        missing = [t for t in TARGETS if t not in self.process_ids]
        self.run.check(s, "target processes found", not missing,
                       f"{len(procs)} processes; missing {missing}" if missing else f"{len(procs)} processes",
                       fatal=True)

    def discover(self) -> None:
        s = "discover"
        domains = self.get(f"/projects/{self.project_id}/domains").json()
        self.run.check(s, "domains discovered", len(domains) > 0, f"{len(domains)} domains")
        job = self.wait_job(self.post(f"/projects/{self.project_id}/domains/name").json()["job_id"], "discover")
        self.job_ok(s, job)
        result = job.get("result") or {}
        named = self.get(f"/projects/{self.project_id}/domains").json()
        self.run.check(s, "domains named", all(d["name"] for d in named),
                       f"{result.get('named', 0)} named: " + ", ".join(d["name"] for d in named))
        homes = {pid: d["name"] for d in named for pid in d["process_ids"]}
        self.run.check(s, "targets placed in a domain", all(self.process_ids[t] in homes for t in TARGETS),
                       "; ".join(f"{t} in {homes.get(self.process_ids[t])}" for t in TARGETS))
        graph = self.get(f"/projects/{self.project_id}/graph").json()
        self.run.check(s, "knowledge graph", len(graph["nodes"]) > 0 and len(graph["edges"]) > 0,
                       f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

    def analyze(self, name: str) -> None:
        s = f"analyze {name}"
        pid = self.process_ids[name]
        job = self.wait_job(self.post(f"/processes/{pid}/analyse").json()["job_id"], "analyse")
        self.job_ok(s, job)
        an = self.get(f"/processes/{pid}/analysis").json()
        ver, cov = an["verification"], an["coverage"]
        self.run.check(s, "rules extracted", len(an["rules"]) > 0,
                       f"{len(an['rules'])} rules, {len(an['findings'])} findings, provider {an['provider']}")
        self.run.check(s, "citations verified", ver["passed"], f"{ver['verified']} of {ver['total']}")
        self.run.check(s, "coverage complete", cov["complete"], f"{cov['covered']} of {cov['total']}")
        self.run.check(s, "no blockers", not an["blockers"], an["blockers"] or "none")

    def gates(self, name: str) -> None:
        s = f"gates {name}"
        pid = self.process_ids[name]
        agent = self.post(f"/gates/{pid}/G1", json={"state": "approved"}, headers={"X-Agent": "builder"})
        self.run.check(s, "agent refused on a gate", agent.status_code == 403, f"HTTP {agent.status_code}")
        an = self.get(f"/processes/{pid}/analysis").json()
        for rule in an["rules"]:
            r = self.c.patch(f"/rules/{rule['id']}", json={"status": "approved"}, headers={"X-User": SME})
            if r.status_code != 200:
                self.run.check(s, f"rule {rule['rule_key']} approved", False, r.text, fatal=True)
        self.run.check(s, "rules approved", True, f"{len(an['rules'])} rules by {SME}")
        self._gate(s, pid, "G1", SME)
        # Static findings are decided Fix (their fields become intended differences in parity);
        # findings raised by the model are kept as they are.
        decisions: dict[str, int] = {"fix": 0, "keep": 0}
        for f in an["findings"]:
            decision = "fix" if f["source"] == "static" else "keep"
            r = self.c.patch(f"/findings/{f['id']}", json={"decision": decision, "note": "acceptance run"},
                             headers={"X-User": SME})
            if r.status_code != 200:
                self.run.check(s, f"finding {f['finding_key']} decided", False, r.text, fatal=True)
            decisions[decision] += 1
        self.run.check(s, "findings decided", True, f"{decisions['fix']} fix, {decisions['keep']} keep by {SME}")
        self._gate(s, pid, "G2", SME)
        job = self.wait_job(self.post(f"/processes/{pid}/design").json()["job_id"], "design")
        self.job_ok(s, job)
        self._gate(s, pid, "G3", ARCHITECT)

    def _gate(self, step: str, pid: str, gate: str, user: str) -> None:
        r = self.post(f"/gates/{pid}/{gate}", json={"state": "approved", "note": "acceptance run"},
                      headers={"X-User": user})
        body = r.json()
        ok = r.status_code == 200 and body.get("state") == "approved" and body.get("decided_by") == user
        self.run.check(step, f"{gate} approved by {user}", ok,
                       f"decided_by {body.get('decided_by')}" if r.status_code == 200 else body.get("detail"),
                       fatal=True)

    def build(self, name: str) -> None:
        s = f"build {name}"
        pid = self.process_ids[name]
        r = self.post(f"/processes/{pid}/build", json={"runtime": "container_app"}, headers={"X-User": ENGINEER})
        self.run.check(s, "build queued", r.status_code == 202, r.text if r.status_code != 202 else "HTTP 202",
                       fatal=True)
        build_id = r.json()["build_id"]
        self.builds[name] = build_id
        self.job_ok(s, self.wait_job(r.json()["job_id"], "build"))
        b = self.get(f"/builds/{build_id}").json()
        steps = {st["name"]: st for st in b["steps"]}
        tests = self.get(f"/builds/{build_id}/tests").json()["summary"]
        files = self.get(f"/builds/{build_id}/files").json()["counts"]
        self.run.check(s, "solution generated", sum(files.values()) > 0,
                       ", ".join(f"{k} {v}" for k, v in sorted(files.items())))
        te = (b.get("builder") or {}).get("test_engineer")
        self.run.check(s, "test engineer stage ran", te is not None,
                       f"{len(te['filled'])} filled, {len(te['rejections'])} rejected, "
                       f"{len(te['warnings'])} warnings; review findings "
                       f"{sum(len(v) for v in (b.get('review_findings') or {}).values())}" if te else "missing")
        self.run.check(s, "format check", steps.get("format", {}).get("ok") is True,
                       _first_line(steps.get("format", {}).get("log")))
        g4 = b.get("g4") or {}
        restore = steps.get("restore", {})
        if self.full:
            self.run.check(s, "restore passed", restore.get("ok") is True,
                           _tail_line(restore.get("log") or "", "NuGet unreachable"))
            self.run.check(s, "compile passed", steps.get("build", {}).get("ok") is True,
                           _first_line(steps.get("build", {}).get("log")))
            green = tests["total"] > 0 and tests["passed"] == tests["total"]
            self.run.check(s, "every test green", green,
                           f"{tests['passed']} of {tests['total']} passed, {tests['failed']} failed, "
                           f"{tests['not_run']} not run")
            self.run.check(s, "build status passed", b["status"] == "passed", b["status"])
            self.run.check(s, "G4 enabled", g4.get("enabled") is True, g4.get("reason") or "enabled")
        else:
            log = restore.get("log") or ""
            self.run.check(s, "restore failed honestly", restore.get("ok") is False and "NuGet unreachable" in log,
                           _tail_line(log, "NuGet unreachable"))
            self.run.check(s, "remedy named", "make nuget-cache" in log, "restore log names `make nuget-cache`")
            skipped = [n for n in ("build", "test") if steps.get(n, {}).get("skipped") is True]
            self.run.check(s, "build and test skipped", skipped == ["build", "test"],
                           "; ".join(_first_line(steps.get(n, {}).get("log")) for n in ("build", "test")))
            self.run.check(s, "tests listed, none run", tests["total"] > 0 and tests["not_run"] == tests["total"],
                           f"{tests['total']} tests, {tests['not_run']} not run")
            self.run.check(s, "build status failed", b["status"] == "failed", b["status"])
            self.run.check(s, "G4 not enabled, reason given",
                           g4.get("enabled") is False and "restore failed" in (g4.get("reason") or ""),
                           g4.get("reason"))

    def infra(self, name: str) -> None:
        s = f"infra {name}"
        build_id = self.builds[name]
        files = [f["path"] for f in self.get(f"/builds/{build_id}/infra").json().get("files") or []]
        live = [p for p in files if p.startswith("infra/live/") and p.endswith("terragrunt.hcl")]
        modules_tf = [p for p in files if p.startswith("infra/modules/") and p.endswith(".tf")]
        self.run.check(s, "Terraform and Terragrunt generated", bool(live) and bool(modules_tf),
                       f"{len(files)} files: {len(modules_tf)} module .tf, {len(live)} live terragrunt.hcl")
        v = self.get(f"/builds/{build_id}").json().get("infra_validation") or {}
        self.run.check(s, "terraform fmt and terragrunt hclfmt", v.get("fmt_ok") is True and v.get("hclfmt_ok") is True,
                       f"fmt {v.get('fmt_ok')}, hclfmt {v.get('hclfmt_ok')}")
        modules = v.get("modules") or []
        if self.full:
            bad = [m["name"] for m in modules if not (m.get("init_ok") and m.get("validate_ok"))]
            self.run.check(s, "infra validated", v.get("ok") is True and not bad,
                           f"{len(modules)} modules validated" if not bad else f"not validated: {bad}")
        else:
            unreachable = v.get("registry_unreachable") is True and all(m.get("registry_unreachable") for m in modules)
            self.run.check(s, "registry unreachable reported", unreachable and v.get("ok") is False,
                           (v.get("notes") or ["no note"])[0])

    def verify(self, name: str) -> None:
        s = f"verify {name}"
        pid, build_id = self.process_ids[name], self.builds[name]
        gen = self.post(f"/processes/{pid}/scenarios/generate", json={})
        body = gen.json() if gen.status_code == 200 else {}
        scenarios = body.get("scenarios") or []
        self.run.check(s, "scenarios generated", gen.status_code == 200 and len(scenarios) > 0,
                       f"{len(scenarios)} scenarios, label '{body.get('label')}'" if body else gen.text, fatal=True)
        self.run.check(s, "expected output labeled", "derived from the TIBCO model" in (body.get("label") or ""),
                       body.get("label"))
        if self.full:
            run = self.post(f"/builds/{build_id}/run")
            self.run.check(s, "service started", run.status_code == 200 and run.json().get("running") is True,
                           (run.json().get("swagger_url") if run.status_code == 200 else run.json().get("detail")),
                           fatal=True)
            try:
                swagger = run.json().get("swagger_url")
                if swagger:
                    sw = httpx.get(swagger, timeout=30)
                    self.run.check(s, "Swagger answers", sw.status_code == 200, f"HTTP {sw.status_code}")
                job_id = self.post(f"/builds/{build_id}/verify").json()["job_id"]
                self.job_ok(s, self.wait_job(job_id, "verify"))
                p = self.get(f"/builds/{build_id}/parity").json()
                counted_ok = p["compared"] > 0 and p["matched"] + p["intended"] == p["compared"]
                self.run.check(s, "parity 100% except intended",
                               counted_ok and p["unexplained"] == 0 and p["errors"] == 0,
                               f"{p['matched']} matched + {p['intended']} intended of {p['compared']} fields, "
                               f"{p['unexplained']} unexplained, {p['errors']} errors, "
                               f"parity {round((p['parity'] or 0) * 100, 1)}%")
                self.run.check(s, "unconfirmed intended shown", "unconfirmed_intended" in p,
                               f"{p.get('unconfirmed_intended')} not confirmed: {p.get('unconfirmed_note')}")
            finally:
                self.c.delete(f"/builds/{build_id}/run")
        else:
            state = self.get(f"/builds/{build_id}/run").json()
            self.run.check(s, "service not running", state.get("running") is False, f"running {state.get('running')}")
            start = self.post(f"/builds/{build_id}/run")
            detail = start.json().get("detail") or ""
            nu = re.search(r"NU1301[^.]*\.", detail)
            self.run.check(s, "start refused with the NuGet reason",
                           start.status_code == 409 and "nuget" in detail.lower(),
                           f"HTTP {start.status_code}: {detail.split(':')[0]}: {nu.group(0) if nu else _first_line(detail)}")
            ver = self.post(f"/builds/{build_id}/verify")
            self.run.check(s, "verify refused", ver.status_code == 409, f"HTTP {ver.status_code}: "
                           f"{ver.json().get('detail')}")
            parity = self.get(f"/builds/{build_id}/parity")
            self.run.check(s, "no parity claimed", parity.status_code == 404, f"HTTP {parity.status_code}")

    def plan(self, name: str) -> None:
        s = f"plan {name}"
        build_id = self.builds[name]
        r = self.post(f"/builds/{build_id}/plan")
        body = r.json()
        plan = body.get("plan") or {}
        self.run.check(s, "connect-later plan", r.status_code == 200 and body.get("connected") is False
                       and plan.get("status") == "connect_later", body.get("reason") or body, fatal=True)
        last = body.get("last_validation") or {}
        if self.full:
            self.run.check(s, "plan carries a passing validation", last.get("ok") is True, f"ok {last.get('ok')}")
        else:
            self.run.check(s, "plan carries the registry reason", last.get("registry_unreachable") is True,
                           (last.get("notes") or ["no note"])[0])
        self.run.check(s, "apply not offered", (plan.get("apply") or {}).get("enabled") is False,
                       (plan.get("apply") or {}).get("reason"))
        ack = self.post(f"/builds/{build_id}/plan/acknowledge", headers={"X-User": OPS},
                        json={"note": "Subscription access is pending with the platform team."})
        a = ack.json()
        self.run.check(s, f"acknowledged by {OPS}", ack.status_code == 200 and a.get("status") == "acknowledged"
                       and a.get("decided_by") == OPS, f"status {a.get('status')}, decided_by {a.get('decided_by')}")

    def evidence(self) -> None:
        s = "evidence"
        pid = self.process_ids["CreditApplicationIntake"]
        td = self.get(f"/processes/{pid}/test-data").json()
        ssns = list(_values(td.get("inputs"), "ssn"))
        raw = RAW_SSN.findall(json.dumps(td.get("inputs")))
        masked = bool(ssns) and all(MASKED_SSN.match(str(v)) for v in ssns) and not raw
        self.run.check(s, "test data masked (CreditApplicationIntake)", "ssn" in td.get("masked_fields", []) and masked,
                       f"{len(ssns)} SSN values masked, no raw SSN in the inputs" if masked
                       else f"unmasked or none: {[v for v in ssns if not MASKED_SSN.match(str(v))][:3]} {raw[:3]}")
        ready = self.get(f"/projects/{self.project_id}/readiness").json()
        rows = {r["key"]: r for r in ready["rows"]}
        counts: dict[str, int] = {}
        for r in ready["rows"]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        self.run.check(s, "readiness computed", len(rows) > 0 and all(r["evidence"] or r["gap"] for r in rows.values()),
                       f"{len(rows)} rows: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        self.run.check(s, "synthetic estate met", rows.get("synthetic_estate", {}).get("status") == "met",
                       rows.get("synthetic_estate", {}).get("status"))
        audit = self.get(f"/projects/{self.project_id}/audit").json()
        actors = sorted({a["actor"] for a in audit if a.get("gate")})
        self.run.check(s, "gate decisions audited by named users", {SME, ARCHITECT} <= set(actors),
                       f"{len(audit)} audit lines; gate actors {actors}")


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _tail_line(text: str, needle: str) -> str:
    for line in (text or "").splitlines():
        if needle in line:
            return line.strip()
    return _first_line(text)


def _values(obj: Any, key: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                yield v
            else:
                yield from _values(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v, key)


# ------------------------------------------------------------------ main
def print_table(run: Run, header: list[str]) -> None:
    for line in header:
        print(line)
    w_step = max(len(r.step) for r in run.rows)
    w_check = max(len(r.check) for r in run.rows)
    print()
    print(f"{'step'.ljust(w_step)}  {'check'.ljust(w_check)}  result  detail")
    print(f"{'-' * w_step}  {'-' * w_check}  ------  {'-' * 40}")
    for r in run.rows:
        print(f"{r.step.ljust(w_step)}  {r.check.ljust(w_check)}  {'PASS' if r.ok else 'FAIL':6}  {r.detail}")
    failed = sum(1 for r in run.rows if not r.ok)
    print()
    print(f"{len(run.rows) - failed} passed, {failed} failed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default=default_api_url(), help="API base URL (default %(default)s)")
    ap.add_argument("--mode", choices=("full", "offline"), help="force a mode (default: decided by the probes)")
    ap.add_argument("--zip", type=Path, default=REPO / "samples" / "synthetic-gmf.zip",
                    help="the synthetic estate zip (make samples-zip)")
    args = ap.parse_args(argv)

    client = httpx.Client(base_url=args.api.rstrip("/"), timeout=httpx.Timeout(180.0, connect=10.0))
    run = Run(args.api)
    started = time.monotonic()
    try:
        tools = client.get("/settings").json().get("toolchains") or {}
    except httpx.HTTPError as exc:
        print(f"API not reachable at {args.api}: {exc}. Start it (make api, or make demo-check starts one).")
        return 2
    nuget_ok, nuget_why = nuget_probe()
    registry_ok, registry_why = registry_probe((tools.get("terraform") or {}).get("version"))
    probes_pass = nuget_ok and registry_ok
    if args.mode:
        mode, why = args.mode, "selected with --mode"
    else:
        mode = "full" if probes_pass else "offline"
        why = "both probes passed" if probes_pass else "a probe failed"
    header = [f"ExiQO Migrate acceptance run against {args.api}",
              f"mode: {mode} ({why})",
              f"  NuGet probe:    {'pass' if nuget_ok else 'fail'}: {nuget_why}",
              f"  registry probe: {'pass' if registry_ok else 'fail'}: {registry_why}"]

    flow = Flow(run, client, mode, args.zip)
    # (name, function, later steps depend on it): a failure in a step others depend on stops the
    # run; infra, verify, plan and evidence only end their own step.
    steps: list[tuple[str, Callable[[], None], bool]] = [
        ("preflight", flow.preflight, True), ("upload", flow.upload, True), ("scan", flow.scan, True),
        ("discover", flow.discover, False)]
    steps += [(f"analyze {t}", lambda t=t: flow.analyze(t), True) for t in TARGETS]
    steps += [(f"gates {t}", lambda t=t: flow.gates(t), True) for t in TARGETS]
    for t in TARGETS:
        steps += [(f"build {t}", lambda t=t: flow.build(t), True),
                  (f"infra {t}", lambda t=t: flow.infra(t), False),
                  (f"verify {t}", lambda t=t: flow.verify(t), False),
                  (f"plan {t}", lambda t=t: flow.plan(t), False)]
    steps.append(("evidence", flow.evidence, False))

    stopped_at: str | None = None
    for name, fn, blocking in steps:
        if stopped_at is not None:
            run.check(name, "not reached", False, f"stopped at {stopped_at}")
            continue
        try:
            fn()
        except StepFailed as exc:
            if blocking:
                stopped_at = str(exc)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            run.check(name, "unexpected error", False, f"{type(exc).__name__}: {exc}")
            if blocking:
                stopped_at = name
    if flow.project_id:
        header.append(f"project: {flow.project_id}")
    print_table(run, header)
    print(f"elapsed {time.monotonic() - started:.0f} s")
    return 1 if any(not r.ok for r in run.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
