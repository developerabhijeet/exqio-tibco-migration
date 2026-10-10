"""Parity evaluator, scenario generation and comparison against the synthetic GMF estate.

Expected values are the estate's own behaviour (read from the .bwp files, seed.sql and the
WireMock stubs), not the business policy: PaymentPosting charges the late fee on day 10
because the source compares with ``>= 10`` (finding PAY-F1).
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import pytest

from engine.analysis.models import Citation, Finding
from engine.ir.models import Application
from engine.parity.compare import NoiseRule, NumericRule, build_lineage, compare, flatten_result
from engine.parity.db import ScenarioDB
from engine.parity.evaluator import Evaluator
from engine.parity.models import ExpectedResult, Scenario, StubResponse
from engine.parity.predicates import parse_condition
from engine.parity.scenarios import generate_and_evaluate, generate_scenarios, success_links
from engine.parity.schema import SchemaIndex
from engine.parity.seed import SeedData
from engine.parity.sql import sql_write_payload
from engine.parity.stubs import WireMockStubs
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH = REPO_ROOT / "samples" / "synthetic-gmf"
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://exiqo:exiqo@localhost:5432/exiqo_test")

EQUIFAX = "CreditOrigination.creditorigination.EquifaxClient/consumer-credit/v2/scores"
TRANSUNION = "CreditOrigination.creditorigination.TransUnionClient/credit-report/v1/score"


# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def apps() -> dict[str, Application]:
    return {a.id: a for a in scan_folder(SYNTH).applications}


@pytest.fixture(scope="module")
def gmf(apps) -> Application:
    return apps["gmf.credit"]


@pytest.fixture(scope="module")
def db():
    database = ScenarioDB(DSN)
    database.reset(SYNTH / "Database" / "schema.sql", SYNTH / "Database" / "seed.sql")
    yield database
    database.close()


@pytest.fixture(scope="module")
def stubs() -> WireMockStubs:
    return WireMockStubs.from_folder(SYNTH / "Stubs")


@pytest.fixture(scope="module")
def evaluator(gmf, db, stubs) -> Evaluator:
    return Evaluator(gmf, db, stubs)


def run(evaluator: Evaluator, process_id: str, data: dict, **kw) -> ExpectedResult:
    sc = Scenario(id="t", process_id=process_id, name="t", kind=kw.pop("kind", "happy"), input=data, **kw)
    return asyncio.run(evaluator.run(process_id, sc))


def process(app: Application, name: str):
    return next(p for p in app.processes if p.name == name)


def effects(result: ExpectedResult, kind: str, target: str | None = None) -> list:
    return [s for s in result.side_effects if s.kind == kind and (target is None or s.target == target)]


def nguyen_application(application_id: str) -> dict:
    """Applicant 4 of seed.sql (918-74-2290). Their last application is 31 days old, so it is
    not a duplicate; the TransUnion stub answers after 2500 ms, past the 1000 ms timeout."""
    return {"body": {
        "applicationId": application_id, "dealerId": "D2040",
        "applicant": {"firstName": "Robert", "lastName": "Nguyen", "ssn": "918-74-2290",
                      "dateOfBirth": "1968-01-30", "monthlyIncome": "11500.00", "monthlyDebt": "2600.00",
                      "address": {"line1": "29 HARBOR VIEW DR", "city": "TAMPA", "state": "FL",
                                  "postalCode": "33602"}},
        "vehicle": {"vin": "1GYKNDRS0NZ400871", "modelYear": 2022, "make": "CADILLAC", "model": "XT5",
                    "price": "52900.00"},
        "requestedAmount": "45500.00", "requestedTermMonths": 60,
    }}


def decision_request(score: int, income: str | None = "6200.00", debt: str = "1450.00") -> dict:
    params = {"applicationId": "CA-2026-000777", "ssn": "912-55-7781", "dealerId": "D10021",
              "vin": "1G1ZD5ST5LF104221", "creditScore": score, "singleBureau": False,
              "monthlyDebt": debt, "requestedAmount": "31500.00", "requestedTermMonths": 84}
    if income is not None:
        params["monthlyIncome"] = income
    return {"parameters": params}


# --- evaluator ---------------------------------------------------------------------------------


def test_intake_happy_path_expected(gmf, db, stubs):
    # Committed run (isolate=False) so the audit row can be read back from the database.
    evaluator = Evaluator(gmf, db, stubs, isolate=False)
    try:
        result = run(evaluator, "creditorigination.CreditApplicationIntake", nguyen_application("CA-2026-000950"))
        rows = db.execute("select event_type, ssn from audit_log where application_id = ?", ["CA-2026-000950"])
    finally:  # the committed rows would make this applicant a 30-day duplicate for later tests
        for table in ("audit_log", "bureau_pull", "decision", "credit_application"):
            db.execute(f"delete from {table} where application_id = ?", ["CA-2026-000950"])
    assert result.fault is None
    assert result.output["status"] == 200
    body = result.output["body"]
    assert body["tier"] == "A" and Decimal(body["apr"]) == Decimal("5.49")
    assert body["decision"] == "APPROVED" and body["creditScore"] == "781"
    assert body["singleBureau"] == "true"  # TransUnion timed out (2500 ms > 1000 ms)
    assert "transUnionScore" not in body  # INT-F1: the reply never maps it

    audit = effects(result, "row_written", "audit_log")
    assert len(audit) == 1 and audit[0].payload["ssn"] == "918-74-2290"  # AUD-F1: clear text
    assert audit[0].payload["payload"] == "dealer=D2040;vin=1GYKNDRS0NZ400871;amount=45500.00;term=60"
    assert rows == [{"event_type": "APPLICATION_RECEIVED", "ssn": "918-74-2290"}]

    pulls = effects(result, "row_written", "bureau_pull")
    assert len(pulls) == 1
    assert pulls[0].payload["equifax_score"] == 781 and pulls[0].payload["transunion_score"] is None
    assert pulls[0].payload["single_bureau"] is True
    calls = effects(result, "service_called")
    assert [c.target for c in calls] == [
        "EquifaxClient POST /consumer-credit/v2/scores", "TransUnionClient POST /credit-report/v1/score"]
    assert calls[0].payload["status"] == 200 and calls[1].payload["outcome"] == "timeout"
    assert calls[0].payload["request"] == {"ssn": "918-74-2290", "firstName": "ROBERT",
                                           "lastName": "NGUYEN", "dateOfBirth": "1968-01-30"}
    decision = effects(result, "row_written", "decision")[0].payload
    assert decision["tier"] == "A" and decision["dti"] == "0.2261"  # numeric(8,4) as stored

    # subprocess activities are traced under the calling activity
    ids = [s.activity_id for s in result.trace]
    assert "CallDecisionEngine/AssignTierA" in ids and "CallBureauOrchestrator/PullTransUnion" in ids


def test_intake_faults_are_caught_by_the_process_handlers(evaluator):
    # 912-55-7781 has CA-2026-000106 from 5 days ago: DuplicateApplicationFault -> catch -> 409
    dup = nguyen_application("CA-2026-000951")
    dup["body"]["applicant"]["ssn"] = "912-55-7781"
    result = run(evaluator, "creditorigination.CreditApplicationIntake", dup)
    assert result.fault is None
    assert result.output == {"status": 409, "body": {
        "statusCode": "409", "message": "Application already submitted for this SSN within 30 days"}}
    # MissingIncomeFault thrown in DecisionEngine propagates through the call -> 422
    no_income = nguyen_application("CA-2026-000952")
    del no_income["body"]["applicant"]["monthlyIncome"]
    result = run(evaluator, "creditorigination.CreditApplicationIntake", no_income)
    assert result.output["status"] == 422
    # BureauUnavailableFault (both bureaus 503 for 931-48-6610) -> catchAll -> 503
    down = nguyen_application("CA-2026-000953")
    down["body"]["applicant"]["ssn"] = "931-48-6610"
    result = run(evaluator, "creditorigination.CreditApplicationIntake", down)
    assert result.output["status"] == 503
    assert result.output["body"]["message"] == "Neither Equifax nor TransUnion returned a score"


def test_decision_engine_tiers_and_fault(evaluator):
    pid = "creditorigination.DecisionEngine"
    tiers = {score: run(evaluator, pid, decision_request(score)).output for score in (752, 700, 650, 600)}
    assert [tiers[s].get("tier") for s in (752, 700, 650)] == ["A", "B", "C"]
    assert tiers[600]["decision"] == "DECLINED" and "tier" not in tiers[600]
    assert tiers[650]["approvedTermMonths"] == "72"  # tier C caps 84 months at 72
    assert tiers[752]["approvedTermMonths"] == "84"
    fault = run(evaluator, pid, decision_request(752, income=None))
    assert fault.fault is not None and fault.fault.name == "MissingIncomeFault"
    assert fault.fault.data["message"] == "Monthly income is required to compute DTI"
    assert fault.output == {} and not effects(fault, "row_written")


def test_decision_branch_scenarios_cover_all_tiers(gmf, evaluator):
    proc = process(gmf, "DecisionEngine")
    scenarios = generate_scenarios(proc, gmf.contracts, gmf)
    assert all(s.source == "generated" and s.expected is None for s in scenarios)
    by_kind = {k: [s for s in scenarios if s.kind == k] for k in ("happy", "branch", "fault")}
    assert len(by_kind["happy"]) == 1 and len(by_kind["branch"]) == 4 and len(by_kind["fault"]) == 1
    # the tier A branch has the happy path's input but is kept: de-duplication is per kind
    tier_a = next(s for s in by_kind["branch"] if s.id.endswith("ComputeDTIToAssignTierA"))
    assert tier_a.input == by_kind["happy"][0].input
    happy = by_kind["happy"][0].input["parameters"]
    # seed data reused: applicant 1 and decision 1 of seed.sql; the new application id is fresh
    assert happy["ssn"] == "912-55-7781" and happy["creditScore"] == 752
    assert happy["applicationId"] == "CA-2026-000107"
    scenarios = asyncio.run(_fill(scenarios, evaluator))
    assert len(scenarios) == 6  # every branch scenario really took its link
    outcomes = {s.id: (s.expected.output.get("decision"), s.expected.output.get("tier"),
                       s.expected.fault.name if s.expected.fault else None) for s in scenarios}
    assert outcomes == {
        "DecisionEngine:happy": ("APPROVED", "A", None),
        "DecisionEngine:branch:ComputeDTIToAssignTierA": ("APPROVED", "A", None),
        "DecisionEngine:branch:ComputeDTIToAssignTierB": ("APPROVED", "B", None),
        "DecisionEngine:branch:ComputeDTIToAssignTierC": ("APPROVED", "C", None),
        "DecisionEngine:branch:ComputeDTIToAssignDecline": ("DECLINED", None, None),
        "DecisionEngine:fault:StartToThrowMissingIncome": (None, None, "MissingIncomeFault"),
    }
    # boundary values solved from the conditions (>= 680, >= 620, below every threshold)
    scores = {s.id.rsplit(":", 1)[-1]: s.input["parameters"].get("creditScore") for s in scenarios}
    assert scores["ComputeDTIToAssignTierB"] == 680
    assert scores["ComputeDTIToAssignTierC"] == 620
    assert scores["ComputeDTIToAssignDecline"] == 619
    assert "monthlyIncome" not in by_kind["fault"][0].input["parameters"]


async def _fill(scenarios, evaluator):
    from engine.parity.scenarios import fill_expected

    return await fill_expected(scenarios, evaluator)


def _variant(app: Application, name: str, edit) -> Application:
    """A deep copy of ``app`` with ``edit(process)`` applied to process ``name``."""
    copied = copy.deepcopy(app)
    edit(process(copied, name))
    return copied


def _set_condition(link_id: str, expression: str):
    def edit(proc):
        link = next(t for t in proc.transitions if t.id == link_id)
        link.expression = expression
    return edit


def test_overlapping_conditions_fire_every_true_link(gmf, db, stubs):
    # BW fires every conditional link whose condition holds; make B overlap A (>= 680 only)
    app = _variant(gmf, "DecisionEngine", _set_condition(
        "ComputeDTIToAssignTierB", "number($Start/parameters/creditScore) >= 680"))
    result = run(Evaluator(app, db, stubs), "creditorigination.DecisionEngine", decision_request(752))
    steps = {s.activity_id: s for s in result.trace}
    assert steps["ComputeDTI"].chosen_transition == "ComputeDTIToAssignTierA,ComputeDTIToAssignTierB"
    assert "AssignTierA" in steps and "AssignTierB" in steps and "AssignDecline" not in steps
    # the join after both branches still runs once; ($AssignTierA, $AssignTierB, ...)[1] is A
    assert [s.activity_id for s in result.trace].count("RecordDecision") == 1
    assert result.output["tier"] == "A"
    # a generated scenario for B must make A false too, so it stays below 740
    scenarios = generate_scenarios(process(app, "DecisionEngine"), app.contracts, app)
    b = next(s for s in scenarios if s.id.endswith("branch:ComputeDTIToAssignTierB"))
    assert 680 <= b.input["parameters"]["creditScore"] < 740


def test_condition_uses_effective_boolean_value(gmf, db, stubs):
    # XPath: the non-empty string 'false' is true
    app = _variant(gmf, "DecisionEngine", _set_condition("ComputeDTIToAssignTierA", "'false'"))
    result = run(Evaluator(app, db, stubs), "creditorigination.DecisionEngine", decision_request(600))
    assert "AssignTierA" in [s.activity_id for s in result.trace]
    app = _variant(gmf, "DecisionEngine", _set_condition("ComputeDTIToAssignTierA", "0"))
    result = run(Evaluator(app, db, stubs), "creditorigination.DecisionEngine", decision_request(752))
    assert "AssignTierA" not in [s.activity_id for s in result.trace]


def test_cycle_in_link_graph_is_an_error(gmf, db, stubs):
    from engine.ir.models import Transition
    from engine.parity.evaluator import EvaluationError

    def edit(proc):
        proc.transitions.append(Transition(id="Loop", from_id="RecordApplication", to_id="AssignTierA",
                                           kind="success"))
    app = _variant(gmf, "DecisionEngine", edit)
    with pytest.raises(EvaluationError, match="never resolve"):
        run(Evaluator(app, db, stubs), "creditorigination.DecisionEngine", decision_request(752))


def test_fill_expected_drops_scenarios_that_miss_their_link(gmf, evaluator):
    wrong = Scenario(id="DecisionEngine:branch:ComputeDTIToAssignTierB", process_id="creditorigination.DecisionEngine",
                     name="claims B", kind="branch", input=decision_request(752))
    right = Scenario(id="DecisionEngine:branch:ComputeDTIToAssignTierC", process_id="creditorigination.DecisionEngine",
                     name="C", kind="branch", input=decision_request(650))
    with pytest.warns(UserWarning, match="did not take its link"):
        kept = asyncio.run(_fill([wrong, right], evaluator))
    assert [s.id for s in kept] == ["DecisionEngine:branch:ComputeDTIToAssignTierC"]


def test_bureau_single_bureau_flag_on_timeout(evaluator):
    pid = "creditorigination.BureauOrchestrator"
    req = {"applicationId": "CA-2026-000960", "ssn": "918-74-2290", "firstName": "ROBERT",
           "lastName": "NGUYEN", "dateOfBirth": "1968-01-30"}
    result = run(evaluator, pid, req)
    assert result.fault is None
    assert result.output == {"equifaxScore": "781", "selectedScore": "781", "selectedBureau": "EQUIFAX",
                             "singleBureau": "true", "bureauCount": "1"}
    steps = {s.activity_id: s for s in result.trace}
    assert steps["PullTransUnion"].chosen_transition == "PullTransUnionToTransUnionUnavailable"
    assert steps["PullTransUnion"].output["fault"]["name"] == "HttpCommunicationException"
    # parallel branches run in document order: Equifax first
    order = [s.activity_id for s in result.trace]
    assert order.index("ParseEquifax") < order.index("PullTransUnion")

    both = run(evaluator, pid, {**req, "ssn": "912-55-7781"})
    assert both.output["singleBureau"] == "false" and both.output["selectedScore"] == "752"
    # a scenario stub overrides the WireMock mapping: Equifax 5xx -> TransUnion only
    eq_down = run(evaluator, pid, {**req, "ssn": "912-55-7781"},
                  stub_responses={EQUIFAX: StubResponse(status=503)})
    assert eq_down.output["selectedBureau"] == "TRANSUNION" and eq_down.output["singleBureau"] == "true"
    # neither bureau: BureauUnavailableFault, no bureau_pull row
    none = run(evaluator, pid, {**req, "ssn": "931-48-6610"})
    assert none.fault is not None and none.fault.name == "BureauUnavailableFault"
    assert not effects(none, "row_written")


def test_dealer_funding_publishes_event(evaluator):
    pid = "dealerfunding.DealerFundingRequest"
    result = run(evaluator, pid, {"dealId": "DL-50003", "dealerId": "D3317", "fundingAmount": "52000.00"})
    assert result.fault is None
    [row] = effects(result, "row_written", "funding")
    assert row.payload == {"deal_id": "DL-50003", "dealer_id": "D3317", "amount": "52000.00",
                           "status": "FUNDED", "funded_at": "current_timestamp"}
    [msg] = effects(result, "message_published")
    assert msg.target == "gmf.funding.events"
    assert {k: msg.payload[k] for k in ("dealId", "dealerId", "amount", "status")} == {
        "dealId": "DL-50003", "dealerId": "D3317", "amount": "52000.00", "status": "FUNDED"}
    # DL-50001 is already funded: logged and ignored (no row, no event)
    dup = run(evaluator, pid, {"dealId": "DL-50001", "dealerId": "D10021", "fundingAmount": "31500.00"})
    assert not effects(dup, "row_written") and not effects(dup, "message_published")
    assert "LogDuplicateFunding" in [s.activity_id for s in dup.trace]
    # amount more than 2% off the approved amount: REJECTED event
    off = run(evaluator, pid, {"dealId": "DL-50003", "dealerId": "D3317", "fundingAmount": "60000.00"})
    [rejected] = effects(off, "message_published")
    assert rejected.payload["status"] == "REJECTED" and rejected.payload["reason"] == "AMOUNT_OUTSIDE_TOLERANCE"
    # DFR-F2: suspended D1002 matches active D10021 through LIKE 'D1002%', so it is funded
    like = run(evaluator, pid, {"dealId": "DL-50004", "dealerId": "D1002", "fundingAmount": "45500.00"})
    assert like.fault is None and effects(like, "message_published")[0].payload["status"] == "FUNDED"
    # an unknown dealer: no row -> otherwise -> DealerInactiveFault
    gone = run(evaluator, pid, {"dealId": "DL-50003", "dealerId": "D9999", "fundingAmount": "52000.00"})
    assert gone.fault is not None and gone.fault.name == "DealerInactiveFault"


def _payment(evaluator, db, days_late: int, payment_id: str) -> ExpectedResult:
    [row] = db.execute("select next_due_date from loan_account where account_number = ?", ["GMF-10000006"])
    received = row["next_due_date"] + dt.timedelta(days=days_late)
    return run(evaluator, "loanservicing.PaymentPosting", {
        "paymentId": payment_id, "accountNumber": "GMF-10000006", "amount": "468.20",
        "receivedDate": received.isoformat(), "channel": "ACH"})


def test_payment_posting_late_fee(evaluator, db):
    # The source compares days late with >= 10, so day 10 is charged (policy says > 10: PAY-F1).
    for days, fee, mapper in ((9, "0.00", "NoLateFee"), (10, "25.00", "AssessLateFee"),
                              (11, "25.00", "AssessLateFee")):
        result = _payment(evaluator, db, days, f"PMT-00009{days:02d}")
        [payment] = effects(result, "row_written", "payment")
        assert payment.payload["late_fee_applied"] == fee, days
        assert mapper in [s.activity_id for s in result.trace]
    day10 = effects(_payment(evaluator, db, 10, "PMT-0000910"), "row_written")
    payment, account = day10[0].payload, day10[1].payload
    # allocation: fees (late fee) first, then accrued interest 149.22, then principal
    assert (payment["fee_applied"], payment["interest_applied"], payment["principal_applied"]) == (
        "25.00", "149.22", "293.98")
    assert account == {"fees_due": "0.00", "accrued_interest": "0.00", "principal_balance": "22116.02",
                       "last_payment_date": payment["received_date"],
                       "where.account_number": "GMF-10000006"}


def test_evaluator_runs_isolated_by_default(evaluator, db):
    before = db.execute("select count(*) as n from payment")[0]["n"]
    _payment(evaluator, db, 10, "PMT-0000999")
    assert db.execute("select count(*) as n from payment")[0]["n"] == before


def test_transaction_group_fault_drops_its_rows(gmf, db, stubs):
    # UpdateLoanAccount fails after InsertPayment: the local transaction rolls back
    def edit(proc):
        act = next(a for a in proc.activities if a.id == "UpdateLoanAccount")
        act.config["sql"] = act.config["sql"].replace("set fees_due", "set no_such_column")
    app = _variant(gmf, "PaymentPosting", edit)
    evaluator = Evaluator(app, db, stubs, isolate=False)  # real commit/rollback
    result = _payment(evaluator, db, 3, "PMT-0000940")
    assert result.fault is not None and result.fault.name == "JDBCSQLException"
    assert not effects(result, "row_written", "payment")
    assert db.execute("select count(*) as n from payment where payment_id = ?", ["PMT-0000940"])[0]["n"] == 0
    assert "InsertPayment" in [s.activity_id for s in result.trace]


def test_rest_lookup_and_iterate_and_bw5(evaluator, apps, db, stubs):
    found = run(evaluator, "loanservicing.AccountLookup", {"parameters": {"accountNumber": "GMF-10000003"}})
    assert found.output["status"] == 200 and found.output["body"]["maskedSsn"] == "***-**-9042"
    missing = run(evaluator, "dealerfunding.FundingStatusLookup", {"parameters": {"dealId": "DL-50003"}})
    assert missing.output == {"status": 404, "body": {"statusCode": "404", "message": "No funding for deal DL-50003"}}
    # timer starter + iterate group: one referral per ACTIVE account 30+ days past due
    scan = run(evaluator, "loanservicing.NightlyDelinquencyScan", {})
    sent = effects(scan, "message_published", "gmf.collections.queue")
    assert [m.payload["accountNumber"] for m in sent] == ["GMF-10000005", "GMF-10000004", "GMF-10000007"]
    assert [r.payload["amount_past_due"] for r in effects(scan, "row_written", "delinquency")] == [
        "625.68", "1058.60", "755.10"]
    # BW5: tib:trim + upper-case; no otherwise after the state check (LAS-F1): no response
    legacy = Evaluator(apps["LegacyAddress"], db, stubs)
    ok = run(legacy, "LegacyAddressStandardize",
             {"line1": " 12 main st ", "city": " columbus ", "state": " oh ", "zip": "43215-1234"})
    assert ok.output == {"status": 200, "body": {"line1": "12 MAIN ST", "city": "COLUMBUS", "state": "OH",
                                                 "postalCode": "43215"}}
    bad = run(legacy, "LegacyAddressStandardize", {"line1": "1 a st", "city": "x", "state": "zz", "zip": "1"})
    assert bad.output == {} and bad.fault is None


# --- scenario generation -----------------------------------------------------------------------


def test_parse_condition_atoms():
    [[a, b]] = parse_condition("number($Start/parameters/creditScore) >= 740 and number($ComputeDTI/dti) < 0.35")
    assert (a.measure, a.path, a.op, a.value) == ("num", "$Start/parameters/creditScore", "ge", Decimal(740))
    assert (b.op, b.value) == ("lt", Decimal("0.35"))
    left, right = parse_condition(
        "string-length($Start/parameters/monthlyIncome) = 0 or number($Start/parameters/monthlyIncome) <= 0")
    assert left[0].measure == "len" and right[0].op == "le"
    [[c]] = parse_condition("$LookupDealer/Record[1]/status = 'ACTIVE'")
    assert (c.measure, c.value) == ("str", "ACTIVE") and c.negate().op == "ne"
    [[d]] = parse_condition("exists($x/y)")
    assert d.measure == "exists" and d.negate().value is False


def test_generated_scenarios_cover_estate_processes(gmf, evaluator):
    intake = asyncio.run(generate_and_evaluate(process(gmf, "CreditApplicationIntake"), gmf.contracts, gmf, evaluator))
    ids = {s.id for s in intake}
    assert {"CreditApplicationIntake:branch:CallAuditLogToReplyInvalidSSN",
            "CreditApplicationIntake:branch:CallValidateVINToReplyInvalidVIN",
            "CreditApplicationIntake:fault:CheckDuplicateApplicationToThrowDuplicateApplication"} <= ids
    happy = next(s for s in intake if s.kind == "happy")
    assert happy.expected.output["status"] == 200 and happy.expected.fault is None
    # one timeout and one 5xx per bureau, through the BureauOrchestrator subprocess
    failures = {s.id: s for s in intake if s.kind == "external_failure"}
    assert set(failures) == {f"CreditApplicationIntake:{k}:{b}" for k in ("timeout", "5xx")
                             for b in ("EquifaxClient", "TransUnionClient")}
    timeout = failures["CreditApplicationIntake:timeout:TransUnionClient"]
    assert timeout.stub_responses[TRANSUNION].delay_ms == 1001
    assert timeout.expected.output["body"]["singleBureau"] == "true"
    assert all(s.expected is not None and s.expected.source == "model" for s in intake)

    funding = asyncio.run(generate_and_evaluate(process(gmf, "DealerFundingRequest"), gmf.contracts, gmf, evaluator))
    happy = next(s for s in funding if s.kind == "happy")
    assert happy.input["dealId"] == "DL-50003"  # the seed deal that is contracted and not funded
    assert [m.payload["status"] for m in effects(happy.expected, "message_published")] == ["FUNDED"]
    assert {s.id.split(":", 1)[1] for s in funding if s.kind != "happy"} == {
        "branch:LookupDealerToLookupDeal",
        "branch:CheckExistingFundingToInsertFunding",
        "branch:CheckExistingFundingToLogDuplicateFunding",
        "branch:LookupDealToPublishFundingRejected",
        "fault:LookupDealerToThrowDealerInactive"}


def test_static_happy_path_takes_the_success_links(gmf, apps, evaluator):
    """Without running anything the generator predicts lookups from the seed rows: the
    DealerFunding happy path funds a contracted, unfunded deal (DL-50003, not the already
    funded DL-50001), and the funding link gets its own branch scenario."""
    proc = process(gmf, "DealerFundingRequest")
    assert success_links(proc) == {"LookupDealer": "LookupDealerToLookupDeal",
                                   "LookupDeal": "LookupDealToCheckExistingFunding",
                                   "CheckExistingFunding": "CheckExistingFundingToInsertFunding"}
    scenarios = generate_scenarios(proc, gmf.contracts, gmf)
    by_id = {s.id.split(":", 1)[1]: s for s in scenarios}
    assert by_id["happy"].input["dealId"] == "DL-50003"
    assert by_id["branch:CheckExistingFundingToInsertFunding"].input["dealId"] == "DL-50003"
    assert by_id["branch:CheckExistingFundingToLogDuplicateFunding"].input["dealId"] == "DL-50001"
    kept = asyncio.run(_fill([by_id["happy"], by_id["branch:CheckExistingFundingToInsertFunding"],
                              by_id["branch:CheckExistingFundingToLogDuplicateFunding"]], evaluator))
    assert len(kept) == 3  # each took its link
    assert [m.payload["status"] for m in effects(kept[0].expected, "message_published")] == ["FUNDED"]
    # Intake: applicant 1 is a seeded 30-day duplicate, so the happy path skips to applicant 2;
    # the duplicate fault scenario reuses applicant 1
    intake = {s.id.split(":", 1)[1]: s for s in generate_scenarios(
        process(gmf, "CreditApplicationIntake"), gmf.contracts, gmf)}
    assert intake["happy"].input["body"]["applicant"]["ssn"] == "909-32-1184"
    fault = intake["fault:CheckDuplicateApplicationToThrowDuplicateApplication"]
    assert fault.input["body"]["applicant"]["ssn"] == "912-55-7781"
    # no seed (BW5): the happy input is solved onto the success link (a valid state code)
    legacy = apps["LegacyAddress"]
    happy = generate_scenarios(legacy.processes[0], legacy.contracts, legacy)[0]
    assert happy.kind == "happy" and happy.input["state"] == "AL"


def test_schema_and_seed_helpers(gmf):
    schema = SchemaIndex(gmf.contracts)
    decl = schema.find("CreditApplicationRequest")
    assert decl.ns == "http://xmlns.gmfin.example/credit/origination/application"
    assert decl.child("applicant").child("ssn").ns == decl.ns  # elementFormDefault qualified
    unq = schema.find("DecisionRequest")
    assert unq.child("parameters").ns is None  # Decision.xsd is unqualified
    seed = SeedData.for_app(gmf)
    assert seed.primary_keys["credit_application"] == "application_id"
    assert seed.fresh_value("payment", "payment_id") == "PMT-0000985"
    assert seed.rows["dealer"][1] == {"dealer_id": "D1002", "dealer_name": "PRAIRIE STATE CHEVROLET",
                                      "state_code": "IL", "status": "SUSPENDED", "funding_account": "FA-0001002"}
    from engine.parity.seed import parse_simple_select, run_seed_query

    dup = parse_simple_select("select application_id from gmf.credit_application where ssn = ? "
                              "and submitted_at >= current_timestamp - interval '30 days'")
    assert [r["application_id"] for r in run_seed_query(seed, dup, ["912-55-7781"])] == ["CA-2026-000106"]
    like = parse_simple_select("select dealer_id, status from gmf.dealer where dealer_id like ?")
    assert [r["dealer_id"] for r in run_seed_query(seed, like, ["D1002%"])] == ["D10021", "D1002"]
    assert parse_simple_select("select * from a join b on a.x = b.x where a.y = ?") is None
    verb, table, row, _ = sql_write_payload(
        "update gmf.loan_account set fees_due = ?, status = 'X' where account_number = ?", [1, "A"])
    assert (verb, table, row) == ("update", "loan_account", {"fees_due": 1, "status": "X",
                                                             "where.account_number": "A"})


# --- comparison --------------------------------------------------------------------------------


# The real shape the analysis emits for PAY-F1: the condition on the link out of LoadAccount.
S_GRACE = Finding(id="S_grace", severity="medium", source="static", decision="fix",
                  text="Late fee charged on day 10; policy says after 10 days.",
                  citations=[Citation(activity_id="LoadAccount", kind="condition",
                                      text="days-from-duration(...) >= 10")])
PAYMENT = "loanservicing.PaymentPosting"
DECISION = "creditorigination.DecisionEngine"


def _fixed_payment_posting(gmf: Application) -> Application:
    """The estate with PAY-F1 fixed (``> 10``): what the migrated service does."""
    fixed = copy.deepcopy(gmf)
    proc = process(fixed, "PaymentPosting")
    link = next(t for t in proc.transitions if t.id == "LoadAccountToAssessLateFee")
    assert link.expression.endswith(">= 10")
    link.expression = link.expression[: -len(">= 10")] + "> 10"
    return fixed


def test_compare_intended_change(gmf, db, stubs, evaluator):
    expected = _payment(evaluator, db, 10, "PMT-0000930")
    fixed = Evaluator(_fixed_payment_posting(gmf), db, stubs)
    counterfactual = _payment(fixed, db, 10, "PMT-0000930")  # the model with the fix applied
    actual = counterfactual.model_copy(deep=True)  # the .NET service implements the fix
    lineage = build_lineage(expected, gmf, PAYMENT)
    late = {(e.process_id, e.activity_id) for e in lineage["row_written[payment][0].late_fee_applied"]}
    assert {(PAYMENT, "AssessLateFee"), (PAYMENT, "LoadAccount")} <= late  # data + control
    report = compare(expected, actual, [NoiseRule(path_glob="*.posted_at", reason="clock")], [S_GRACE],
                     lineage, scenario_id="PaymentPosting:day10", counterfactual=counterfactual,
                     finding_process=PAYMENT)
    late = next(d for d in report.side_effects if d.path == "row_written[payment][0].late_fee_applied")
    assert (late.expected, late.actual, late.status, late.finding_id) == ("25.00", "0.00", "intended", "S_grace")
    assert report.unexplained == 0
    changed = {d.path for d in report.side_effects if d.status == "intended"}
    assert changed == {f"row_written[payment][0].{c}" for c in
                       ("fee_applied", "late_fee_applied", "principal_applied")} | {
        "row_written[loan_account][0].principal_balance"}
    compared = [d for d in report.fields + report.side_effects if d.status != "noise"]
    assert report.parity == pytest.approx(sum(d.status == "match" for d in compared) / len(compared))
    # a finding decided keep explains nothing
    kept = S_GRACE.model_copy(update={"decision": "keep"})
    assert compare(expected, actual, [], [kept], lineage, finding_process=PAYMENT).unexplained == 4
    # scoped by process: the same activity id cited for another process explains nothing
    assert compare(expected, actual, [], [S_GRACE], lineage,
                   finding_process=DECISION).unexplained == 4
    # a difference the counterfactual does not produce stays a defect, even inside the lineage
    wrong = actual.model_copy(deep=True)
    wrong.side_effects[0].payload["late_fee_applied"] = "5.00"
    report = compare(expected, wrong, [], [S_GRACE], lineage, counterfactual=counterfactual,
                     finding_process=PAYMENT)
    late = next(d for d in report.side_effects if d.path == "row_written[payment][0].late_fee_applied")
    assert late.status == "defect"


def test_compare_tier_threshold_change_is_intended(gmf, db, stubs, evaluator):
    """A finding on the tier B threshold (a condition, not a binding) explains the tier fields.

    Data lineage alone misses it: AssignTierB's binding reads only $Start, so the decision on
    ComputeDTI's outgoing link is only reachable through control dependence."""
    finding = Finding(id="S_tierB", severity="high", source="ai", decision="fix",
                      text="Tier B starts at 700, not 680.",
                      citations=[Citation(activity_id="ComputeDTI", kind="condition",
                                          text="number($Start/parameters/creditScore) >= 680")])
    expected = run(evaluator, DECISION, decision_request(690))
    fixed = _variant(gmf, "DecisionEngine", _set_condition(
        "ComputeDTIToAssignTierB",
        "number($Start/parameters/creditScore) >= 700 and number($Start/parameters/creditScore) <= 739"))
    counterfactual = run(Evaluator(fixed, db, stubs), DECISION, decision_request(690))
    assert (expected.output.get("tier"), counterfactual.output.get("decision")) == ("B", "DECLINED")
    actual = counterfactual.model_copy(deep=True)
    data_only = build_lineage(expected, gmf, DECISION, control=False)
    before = compare(expected, actual, [], [finding], data_only, counterfactual=counterfactual,
                     finding_process=DECISION)
    assert next(d for d in before.fields if d.path == "output.decision").status == "defect"
    lineage = build_lineage(expected, gmf, DECISION)
    report = compare(expected, actual, [], [finding], lineage, counterfactual=counterfactual,
                     finding_process=DECISION)
    statuses = {d.path: d.status for d in report.fields}
    assert statuses["output.decision"] == "intended" and statuses["output.tier"] == "intended"
    assert statuses["output.apr"] == "intended" and statuses["output.dti"] == "match"
    assert report.unexplained == 0
    # an unrelated difference in a field the fix does not change stays a defect
    actual.output["dti"] = "0.5"
    assert compare(expected, actual, [], [finding], lineage, counterfactual=counterfactual,
                   finding_process=DECISION).unexplained == 1


def test_compare_numeric_double_fields(evaluator):
    expected = run(evaluator, DECISION, decision_request(752))
    # the evaluator marks number() division as XPath double; rows compare at the column scale
    assert expected.numeric_fields == {"output.dti": None, "row_written[decision][0].dti": 4}
    compute = next(s for s in expected.trace if s.activity_id == "ComputeDTI")
    assert compute.numeric_kinds == {"dti": "double"}
    assert expected.output["dti"] == "0.23387096774193547"
    actual = expected.model_copy(deep=True)
    actual.output["dti"] = "0.2338709677419354838709677419"  # .NET decimal division
    actual.side_effects[0].payload["dti"] = "0.23387"  # raw value; quantised to numeric(8,4)
    report = compare(expected, actual, [], [], {})
    statuses = {d.path: d.status for d in report.fields + report.side_effects}
    assert statuses["output.dti"] == "match" and statuses["row_written[decision][0].dti"] == "match"
    assert report.unexplained == 0
    actual.output["dti"] = "0.25"  # a wrong formula
    report = compare(expected, actual, [], [], {})
    assert {d.path: d.status for d in report.fields}["output.dti"] == "defect"
    # explicit rules: tolerance, and never noise
    loose = [NumericRule(path_glob="output.dti", rel_tol=0.1, reason="demo")]
    assert {d.path: d.status for d in compare(expected, actual, [], [], {}, numeric_rules=loose).fields}[
        "output.dti"] == "match"
    assert evaluator.as_stored("decision", {"dti": Decimal("0.23387")}) == {"dti": Decimal("0.2339")}


def test_compare_defect(evaluator):
    expected = run(evaluator, "creditorigination.DecisionEngine", decision_request(752))
    actual = expected.model_copy(deep=True)
    actual.output["tier"] = "B"
    report = compare(expected, actual, [], [S_GRACE], build_lineage(expected, evaluator.app,
                                                                      "creditorigination.DecisionEngine"))
    [tier] = [d for d in report.fields if d.path == "output.tier"]
    assert (tier.status, tier.expected, tier.actual, tier.finding_id) == ("defect", "A", "B", None)
    assert report.unexplained == 1 and report.parity < 1.0
    # numbers compare as decimals: 5.49 == "5.490"; a missing side effect is a defect per field
    actual = expected.model_copy(deep=True)
    actual.output["apr"] = 5.490
    actual.side_effects = [s for s in actual.side_effects if s.target != "credit_application"]
    report = compare(expected, actual, [], [], {})
    assert next(d for d in report.fields if d.path == "output.apr").status == "match"
    missing = [d for d in report.side_effects if d.path.startswith("row_written[credit_application][0]")]
    assert missing and all(d.status == "defect" and d.actual is None for d in missing)


def test_compare_noise(evaluator):
    expected = run(evaluator, "creditorigination.DecisionEngine", decision_request(700))
    actual = expected.model_copy(deep=True)
    for se in actual.side_effects:
        se.payload["decided_at" if se.target == "decision" else "submitted_at"] = "2026-10-10T08:00:00"
    rules = [NoiseRule(path_glob="row_written[*].*_at", reason="database clock")]
    report = compare(expected, actual, rules, [], {})
    noise = [d for d in report.side_effects if d.status == "noise"]
    assert {d.path for d in noise} == {"row_written[decision][0].decided_at",
                                       "row_written[credit_application][0].submitted_at"}
    assert report.parity == 1.0 and report.unexplained == 0
    fields, _ = flatten_result(expected)
    assert fields["output.tier"] == "B"
