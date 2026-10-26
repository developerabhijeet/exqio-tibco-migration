"""Shared pytest configuration for the engine test-suite."""
import asyncio
import os
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if TYPE_CHECKING:
    from engine.analysis.models import Citation, Rule

SKIP_NO_NEO4J = ("NEO4J_TEST_URI is not set: run make test (starts neo4j-test) or point it at a "
                 "Neo4j 5.26")


@pytest.fixture
async def memory_graph():
    """A MemoryGraph on the test Neo4j with the schema and no data; skipped when it is unset."""
    uri = os.environ.get("NEO4J_TEST_URI", "")  # an empty value counts as unset
    if not uri:
        pytest.skip(SKIP_NO_NEO4J)
    from engine.memory.graph import MemoryGraph
    graph = MemoryGraph.connect(uri, os.environ.get("NEO4J_TEST_USER", "neo4j"),
                                os.environ.get("NEO4J_TEST_PASSWORD", "exiqo-test-neo4j"))
    try:
        await graph.ensure_schema()
        await graph.reset()
        yield graph
    finally:
        await graph.close()


@pytest.fixture
def hash_embeddings(monkeypatch):
    """The deterministic hash embedding backend (no model download, fast)."""
    from engine.knowledge import embeddings
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "hash")
    embeddings.reset_backend()
    yield
    embeddings.reset_backend()


class EmbedSpy:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def reset(self) -> None:
        self.texts.clear()


@pytest.fixture
def embed_spy(monkeypatch, hash_embeddings):
    """Records every text passed to ``embeddings.embed`` (still embedding with the hash backend)."""
    from engine.knowledge import embeddings
    spy, real = EmbedSpy(), embeddings.embed

    def embed(texts: list[str]) -> list[list[float]]:
        spy.texts.extend(texts)
        return real(texts)

    monkeypatch.setattr(embeddings, "embed", embed)
    return spy


# ------------------------------------------------------------------ gmf.credit generator inputs
# The synthetic GMF estate with two processes, hand-written rules and filled scenarios: the inputs
# the .NET golden files were generated from (``test_generator.py``) and every stack generates from.
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://exiqo:exiqo@localhost:5432/exiqo_test")
PROCESS_IDS = ["dealerfunding.FundingStatusLookup", "dealerfunding.DealerFundingRequest"]
DFR = "dealerfunding.DealerFundingRequest"
FSL = "dealerfunding.FundingStatusLookup"


def _cit(activity: str, kind: str, text: str) -> "Citation":
    from engine.analysis.models import Citation
    return Citation(activity_id=activity, kind=kind, text=text)


def hand_rules() -> "dict[str, list[Rule]]":
    """Approved rules written by hand so the ids and texts are stable for the golden files."""
    from engine.analysis.models import Rule

    dfr = [
        Rule(id="R1", text="A dealer must be ACTIVE to receive funding.", confidence=0.95,
             status="approved",
             citations=[_cit("LookupDealer", "condition", "$LookupDealer/Record[1]/status = 'ACTIVE'")]),
        Rule(id="R2", text="The funding amount must be within 2% of the approved deal amount.",
             confidence=0.9, status="approved",
             citations=[_cit("LookupDeal", "condition", "number($LookupDeal/Record[1]/approved_amount) * 0.02")]),
        Rule(id="R3", text="A deal is funded at most once; a repeat request is logged as DF-409.",
             confidence=0.85, status="edited",
             citations=[_cit("CheckExistingFunding", "sql", "select funding_id, status"),
                        _cit("InsertFunding", "sql", "insert into gmf.funding")]),
        Rule(id="R4", text="Draft rule that is not approved and gets no test.", confidence=0.4,
             status="draft", citations=[_cit("LookupDeal", "sql", "from gmf.deal")]),
    ]
    fsl = [
        Rule(id="R5", text="Funding status is looked up by deal id.", confidence=0.9, status="approved",
             citations=[_cit("QueryFundingStatus", "sql", "where f.deal_id = ?")]),
        Rule(id="R6", text="An unknown deal returns 404 \"No funding for deal\".", confidence=0.9,
             status="approved",
             citations=[_cit("ReplyNotFound", "expression", 'concat("No funding for deal ", $get.parameters/tns:dealId)')]),
    ]
    return {DFR: dfr, FSL: fsl}


@pytest.fixture(scope="module")
def gmf_apps():
    from engine.tibco_parser.scanner import scan_folder
    apps = {a.id: a for a in scan_folder(GMF).applications}
    # scan_folder records absolute paths, and source_path is inside the spec hash (and so the design
    # hash and every golden that carries one): make it relative to the samples root so a golden does
    # not depend on where the checkout lives.
    for app in apps.values():
        for proc in app.processes:
            proc.source_path = Path(proc.source_path).relative_to(GMF).as_posix()
    return apps


@pytest.fixture(scope="module")
def gmf_app(gmf_apps):
    return gmf_apps["gmf.credit"]


@pytest.fixture(scope="module")
def gmf_processes(gmf_app):
    return {p.id: p for p in gmf_app.processes}


@pytest.fixture(scope="module")
def gmf_findings(gmf_app, gmf_processes):
    from engine.analysis.static_rules import run_static_rules

    app, processes = gmf_app, gmf_processes
    out = {}
    for pid in PROCESS_IDS:
        found = run_static_rules(processes[pid], app.contracts, app.substvars)
        out[pid] = [f.model_copy(update={"decision": "keep"}) for f in found]
    return out


@pytest.fixture(scope="module")
def gmf_rules():
    return hand_rules()


@pytest.fixture(scope="module")
def gmf_designs(gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings):
    from engine.domains import graph as graph_mod
    from engine.dotnet_gen.design import build_design_skeleton

    apps, app, processes, rules, findings = gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings
    graph = graph_mod.build_graph(list(apps.values()))
    return {pid: build_design_skeleton(processes[pid], app, rules[pid], findings[pid],
                                       graph_mod.io_card(processes[pid], app, graph))
            for pid in PROCESS_IDS}


@pytest.fixture(scope="module")
def gmf_scenarios(gmf_app, gmf_processes):
    app, processes = gmf_app, gmf_processes
    from engine.parity.db import ScenarioDB
    from engine.parity.evaluator import Evaluator
    from engine.parity.scenarios import fill_expected, generate_scenarios
    from engine.parity.stubs import WireMockStubs

    db = ScenarioDB(DSN)
    db.reset(GMF / "Database" / "schema.sql", GMF / "Database" / "seed.sql")
    evaluator = Evaluator(app, db, WireMockStubs.from_folder(GMF / "Stubs"))
    out = []
    try:
        for pid in PROCESS_IDS:
            static = generate_scenarios(processes[pid], app.contracts, app)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out += asyncio.run(fill_expected(static, evaluator))
    finally:
        db.close()
    return out




@pytest.fixture(scope="module")
def gmf_inputs(gmf_designs, gmf_rules, gmf_findings, gmf_scenarios):
    """``(process_ids, designs, rules, findings, scenarios)``: the generator inputs after ``app``."""
    return PROCESS_IDS, gmf_designs, gmf_rules, gmf_findings, gmf_scenarios
