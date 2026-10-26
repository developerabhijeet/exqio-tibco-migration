"""The language-neutral service plan, the expression AST and SQL placeholder styles (target-stacks Task 4).

``build_plan`` is what the Java, Go and Python generators print: steps in the design's walk order,
the flow the .NET generator would structure (``Seq``/``If``) or queue (``Queue``), and the agent
flags the .NET generator would leave to the Code Builder.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path as FsPath

import pytest

from engine.dotnet_gen import generator
from engine.dotnet_gen.design import _walk_order, is_simple_expression
from engine.dotnet_gen.generator import to_named_parameters
from engine.ir.models import Activity, InputBinding, Process, Starter, Transition
from engine.ir.models import FaultHandler as IrFaultHandler
from engine.ir.models import Group as IrGroup
from engine.stacks.expr import And, Call, Compare, Count, Lit, Not, Or, Path, parse_simple
from engine.stacks.plan import If, Loop, Queue, Run, Seq, Try, build_plan
from engine.stacks.sql import _rewrite, convert_placeholders
from engine.tests.conftest import DFR, FSL, PROCESS_IDS


@pytest.fixture(scope="module")
def designs(gmf_designs):
    return gmf_designs


@pytest.fixture(scope="module")
def solution(tmp_path_factory, gmf_app, gmf_designs, gmf_rules, gmf_findings):
    out = tmp_path_factory.mktemp("plan") / "GmfCredit"
    return generator.generate_solution(gmf_app, PROCESS_IDS, gmf_designs, gmf_rules, gmf_findings, [], out)


# ------------------------------------------------------------------- brief tests
def test_plan_step_order_matches_walk_order(gmf_app, gmf_processes, designs):
    plan = build_plan(gmf_processes[DFR], gmf_app, designs[DFR])
    assert [s.activity_id for s in plan.steps] == [a for a, _ in _walk_order(gmf_processes[DFR]) if a in {x.id for x in gmf_processes[DFR].activities}]


def test_agent_flags_match_dotnet_origins(gmf_app, gmf_processes, designs, solution):
    plan = build_plan(gmf_processes[DFR], gmf_app, designs[DFR])
    dotnet_agent = {f.activity_id for f in solution.files if f.origin == "agent" and f.activity_id}
    assert {s.activity_id for s in plan.steps if s.agent} == dotnet_agent & {s.activity_id for s in plan.steps}


def test_parse_simple_comparisons_and_refusals():
    assert parse_simple('$get/root/status = "ACTIVE"') == Compare("==", Path("get", (("root", None), ("status", None)), False), Lit("ACTIVE"))
    assert parse_simple("count($x/item) > 0") == Compare(">", Count(Path("x", (("item", None),), False)), Lit(0))
    assert parse_simple("tib:parse-date('yyyy', $d)") is None


@pytest.mark.parametrize("style,expected", [("jdbc", "select ? , '?' -- ?\n"), ("dollar", "select $1 , '?' -- ?\n"), ("pyformat", "select %s , '?' -- ?\n")])
def test_placeholder_styles_skip_literals_and_comments(style, expected):
    assert convert_placeholders("select ? , '?' -- ?\n", style) == expected


def test_pyformat_escapes_literal_percent():
    assert convert_placeholders("select '%' || ?", "pyformat") == "select '%%' || %s"


# ------------------------------------------------------------------ expressions
def test_parse_simple_paths_literals_and_logic():
    lookup = Path("LookupDealer", (("Record", 1), ("status", None)), False)
    assert parse_simple("$LookupDealer/Record[1]/status = 'ACTIVE'") == Compare("==", lookup, Lit("ACTIVE"))
    # namespace prefixes are dropped (paths resolve by local name); an attribute is the last step
    assert parse_simple("$get.parameters/tns:dealId") == Path("get.parameters", (("dealId", None),), False)
    assert parse_simple("$m/Body/@id") == Path("m", (("Body", None), ("id", None)), True)
    assert parse_simple("$m/@id/x") is None
    assert parse_simple("$a/x = 1 and ($a/y != 'n' or not($a/z))") == And((
        Compare("==", Path("a", (("x", None),), False), Lit(1)),
        Or((Compare("!=", Path("a", (("y", None),), False), Lit("n")), Not(Path("a", (("z", None),), False))))))
    assert parse_simple("$a/amt >= 2.50") == Compare(">=", Path("a", (("amt", None),), False), Lit(Decimal("2.50")))
    assert parse_simple("-3") == Lit(-3) and parse_simple("true()") == Lit(True) and parse_simple("false()") == Lit(False)
    assert parse_simple('concat("No funding for deal ", $get.parameters/tns:dealId)') == Call(
        "concat", (Lit("No funding for deal "), Path("get.parameters", (("dealId", None),), False)))
    assert parse_simple("count($q/Record) = 0") == Compare("==", Count(Path("q", (("Record", None),), False)), Lit(0))


@pytest.mark.parametrize("expr", [
    "", "   ", "abs($a/x)", "$a/x + 1", "number($a/x) * 0.02", "substring($a/x)", "concat($a/x)",
    "$a/x = ", "($a/x", "count($a/x, $b)", "tib:render-xml($a)", "$a[1", "'unterminated",
])
def test_parse_simple_refuses_what_code_cannot_write(expr):
    assert parse_simple(expr) is None


def test_parse_simple_accepts_every_expression_the_design_calls_simple(gmf_apps):
    """``is_simple_expression`` (the design's prediction) and the AST agree on the whole sample estate."""
    seen = 0
    for app in gmf_apps.values():
        for p in app.processes:
            for a in p.activities:
                for b in a.input_bindings:
                    if is_simple_expression(b.expression):
                        seen += 1
                        assert parse_simple(b.expression) is not None, b.expression
    assert seen > 50


# -------------------------------------------------------------------------- sql
_TRICKY = [
    "select ? from t where a = '?' and b = \"?\" -- ?\n and c = ?",
    "select ?, E'it\\'s ?', $$ ? $$, $tag$ ? $tag$ /* ? /* ? */ ? */ from t where j ?| array['a'] and k ?& b",
    "select ?? from t where x = ?",
    "update t set pct = '5%' where id = ? and note like '%?%'",
]


@pytest.mark.parametrize("sql", _TRICKY)
def test_named_at_delegates_and_the_scanner_agrees_with_it(sql):
    assert convert_placeholders(sql, "named_at") == to_named_parameters(sql)
    assert _rewrite(sql, lambda k: f"@p{k}", lambda s: s) == to_named_parameters(sql)


def test_dollar_and_pyformat_on_tricky_sql():
    sql = _TRICKY[1]
    dollar = convert_placeholders(sql, "dollar")
    assert dollar.startswith("select $1, E'it\\'s ?', $$ ? $$, $tag$ ? $tag$ /* ? /* ? */ ? */")
    assert "?| array" in dollar and "?& b" in dollar
    assert convert_placeholders(_TRICKY[2], "dollar") == "select ? from t where x = $1"
    assert convert_placeholders(_TRICKY[3], "pyformat") == "update t set pct = '5%%' where id = %s and note like '%%?%%'"
    assert convert_placeholders(_TRICKY[2], "jdbc") == _TRICKY[2]
    with pytest.raises(ValueError):
        convert_placeholders("select ?", "qmark")  # type: ignore[arg-type]


def test_every_gmf_statement_converts_with_the_same_placeholder_count(gmf_apps):
    for app in gmf_apps.values():
        for p in app.processes:
            for a in p.activities:
                sql = str(a.config.get("sql") or "")
                if not sql:
                    continue
                n = len(re.findall(r"@p\d+", to_named_parameters(sql))) - len(re.findall(r"@p\d+", sql))
                assert len(re.findall(r"\$\d+", convert_placeholders(sql, "dollar"))) - len(re.findall(r"\$\d+", sql)) == n
                assert convert_placeholders(sql, "pyformat").count("%s") - sql.replace("%", "%%").count("%s") == n


# ------------------------------------------------------------------------- plan
def test_dfr_flow_is_structured_with_conditions_and_the_agent_condition(gmf_app, gmf_processes, designs):
    plan = build_plan(gmf_processes[DFR], gmf_app, designs[DFR])
    assert plan.starter.kind == "jms" and plan.starter.destination == "gmf.dealer.funding.request"
    assert plan.starter.selector == "MsgType = 'FUNDING_REQUEST'" and plan.starter.route is None
    assert isinstance(plan.flow, Seq)
    assert plan.flow.items[:2] == (Run("ReceiveFundingRequest"), Run("LookupDealer"))
    first = plan.flow.items[2]
    assert isinstance(first, If) and first.link_id == "LookupDealerToLookupDeal"
    assert first.cond == Compare("==", Path("LookupDealer", (("Record", 1), ("status", None)), False), Lit("ACTIVE"))
    assert first.otherwise == Seq((Run("ThrowDealerInactive"),)) and first.otherwise_id == "LookupDealerToThrowDealerInactive"
    amount = first.then.items[1]
    assert isinstance(amount, If) and amount.cond is None and amount.tibco.startswith("abs(")  # left to the agent
    runs = _runs(plan.flow)
    assert sorted(runs) == sorted(s.activity_id for s in plan.steps) and len(runs) == len(set(runs))
    by_id = {s.activity_id: s for s in plan.steps}
    assert by_id["LookupDealer"].sql and "?" in by_id["LookupDealer"].sql
    assert by_id["ThrowDealerInactive"].throws == "DealerInactiveFault"
    assert by_id["PublishFundingEvent"].call_target == "gmf.funding.events"
    assert by_id["LookupDealer"].inputs[0].target == "jdbcQueryActivityInput/dealerId"
    assert by_id["LookupDealer"].inputs[0].source is not None
    assert by_id["LookupDealer"].row.pattern == "jdbc.query" and by_id["LookupDealer"].pattern == "jdbc.query"
    assert plan.messaging == designs[DFR].messaging and plan.config == designs[DFR].config
    assert set(plan.contracts.records) >= {"FundingRequest", "DealerInactiveFault"}


def test_fsl_rest_starter_and_reply_branches(gmf_app, gmf_processes, designs):
    plan = build_plan(gmf_processes[FSL], gmf_app, designs[FSL])
    assert (plan.starter.kind, plan.starter.verb, plan.starter.route) == ("rest", "GET", "/funding/{dealId}")
    cond = plan.flow.items[2]
    assert isinstance(cond, If) and cond.cond == Compare(">", Count(Path("QueryFundingStatus", (("Record", None),), False)), Lit(0))
    assert cond.then == Seq((Run("getOut"),)) and cond.otherwise == Seq((Run("ReplyNotFound"),))
    assert not any(s.agent for s in plan.steps)


def test_plan_is_deterministic(gmf_app, gmf_processes, designs):
    assert build_plan(gmf_processes[DFR], gmf_app, designs[DFR]) == build_plan(gmf_processes[DFR], gmf_app, designs[DFR])


def _runs(node) -> list[str]:
    if isinstance(node, Run):
        return [node.step_id]
    if isinstance(node, Seq):
        return [r for i in node.items for r in _runs(i)]
    if isinstance(node, If):
        return _runs(node.then) + _runs(node.otherwise)
    if isinstance(node, Loop):
        return _runs(node.body)
    if isinstance(node, Queue):
        return [r for n in node.nodes for r in _runs(n)]
    if isinstance(node, Try):
        out = _runs(node.body)
        for c in node.catch.values():
            out += _runs(c)
        return out + (_runs(node.catch_all) if node.catch_all else [])
    raise AssertionError(node)


def _act(aid: str, typ: str, *bindings: tuple[str, str], **config) -> Activity:
    return Activity(id=aid, name=aid, type=typ, native_type=typ, config=config,
                    input_bindings=[InputBinding(target=t, expression=e, language="xpath") for t, e in bindings])


def _process(acts, transitions, groups=(), handlers=(), starter="Start", kind="rest", **cfg) -> Process:
    return Process(id="m.P", application="a", module="m", name="P", dialect="bw6", source_path="p.bwp",
                   starter=Starter(activity_id=starter, kind=kind, config=cfg) if starter else None,
                   activities=list(acts), transitions=[Transition(id=t[0], from_id=t[1], to_id=t[2], kind=t[3],
                                                                 expression=t[4] if len(t) > 4 else None)
                                                      for t in transitions],
                   groups=list(groups), fault_handlers=list(handlers))


def _plan(gmf_app, process):
    from engine.domains.models import IOCard
    from engine.dotnet_gen.design import build_design_skeleton
    design = build_design_skeleton(process, gmf_app, [], [], IOCard(process_id=process.id))
    return build_plan(process, gmf_app, design)


def test_parallel_links_give_a_queue_with_link_conditions(gmf_app):
    p = _process([_act("Start", "rest.receive"), _act("A", "log", ("ActivityInput/message", "'a'")),
                  _act("B", "log", ("ActivityInput/message", "'b'")), _act("End", "reply")],
                 [("s-a", "Start", "A", "success"), ("s-b", "Start", "B", "success"),
                  ("a-e", "A", "End", "conditional", "$Start/x = 'y'"), ("b-e", "B", "End", "error")], verb="post")
    plan = _plan(gmf_app, p)
    assert isinstance(plan.flow, Queue) and plan.flow.starts == ("Start",)
    assert [n.step_id for n in plan.flow.nodes] == ["Start", "A", "B", "End"]
    link = {lk.id: lk for lk in plan.flow.links}
    assert link["a-e"].cond == Compare("==", Path("Start", (("x", None),), False), Lit("y")) and link["b-e"].kind == "error"
    assert plan.starter.verb == "POST" and plan.starter.route == "/P"


def test_groups_become_loops_and_fault_handlers_a_try(gmf_app):
    group = IrGroup(id="EachLine", kind="iterate", member_ids=["L1", "L2"], config={"over": "$Start/line"})
    handler = IrFaultHandler(id="h1", fault_name="Boom", activity_ids=["Recover"])
    catch_all = IrFaultHandler(id="h2", activity_ids=["LogIt"])
    p = _process([_act("Start", "rest.receive"), _act("L1", "log", ("ActivityInput/message", "$Start/a")),
                  _act("L2", "assign", ("x", "tib:format('x')")), _act("Recover", "log"), _act("LogIt", "log"),
                  _act("Odd", "java.invoke"), _act("End", "reply")],
                 [("s-g", "Start", "EachLine", "success"), ("l1-l2", "L1", "L2", "success"),
                  ("g-o", "EachLine", "Odd", "success"), ("o-e", "Odd", "End", "success")],
                 groups=[group], handlers=[handler, catch_all])
    for a in p.activities:
        if a.id in ("L1", "L2"):
            a.group_id = "EachLine"
    plan = _plan(gmf_app, p)
    assert isinstance(plan.flow, Try)
    body = plan.flow.body
    assert isinstance(body, Seq) and body.items[0] == Run("Start")
    loop = body.items[1]
    assert loop == Loop("EachLine", Path("Start", (("line", None),), False), Seq((Run("L1"), Run("L2"))), kind="iterate")
    assert plan.flow.catch == {"Boom": Seq((Run("Recover"),))} and plan.flow.catch_all == Seq((Run("LogIt"),))
    agent = {s.activity_id for s in plan.steps if s.agent}
    assert agent == {"L2", "Odd"}  # an untranslatable mapping and a step with no code pattern
    l2 = next(s for s in plan.steps if s.activity_id == "L2")
    assert l2.inputs[0].source is None and l2.inputs[0].tibco == "tib:format('x')"


def test_soap_and_unparsed_selector_starters_are_agent(gmf_app):
    soap = _plan(gmf_app, _process([_act("Start", "soap.receive"), _act("End", "reply")],
                                   [("s-e", "Start", "End", "success")], kind="soap", operation="Fund"))
    assert soap.starter.route == "Fund" and next(s for s in soap.steps if s.activity_id == "Start").agent
    jms = _plan(gmf_app, _process([_act("Start", "jms.receive"), _act("End", "end")],
                                  [("s-e", "Start", "End", "success")], kind="jms", destination="q",
                                  selector="Kind LIKE 'A%'"))
    assert next(s for s in jms.steps if s.activity_id == "Start").agent
    timer = _plan(gmf_app, _process([_act("Start", "timer.start"), _act("End", "end")],
                                    [("s-e", "Start", "End", "success")], kind="timer", time_interval="5",
                                    interval_unit="Minute"))
    assert timer.starter.interval_ms == 300_000 and not any(s.agent for s in timer.steps)


def test_plan_needs_a_row_for_every_activity(gmf_app, gmf_processes, designs):
    bad = designs[DFR].model_copy(update={"mapping": [r for r in designs[DFR].mapping if r.activity_id != "LookupDeal"]})
    with pytest.raises(KeyError, match="LookupDeal"):
        build_plan(gmf_processes[DFR], gmf_app, bad)


def test_shared_modules_are_stack_neutral():
    for name in ("expr.py", "plan.py", "sql.py"):
        text = (FsPath(__file__).resolve().parents[1] / "stacks" / name).read_text(encoding="utf-8")
        assert "—" not in text
        assert not re.search(r"\bC#|\.cs\b|Async\b|NotImplementedException|TibValue", text), name


def test_every_sample_process_plans_and_its_flow_runs_each_activity_once(gmf_apps):
    from engine.domains import graph as graph_mod
    from engine.dotnet_gen.design import build_design_skeleton

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    forms: set[str] = set()
    for app in gmf_apps.values():
        for p in app.processes:
            plan = build_plan(p, app, build_design_skeleton(p, app, [], [], graph_mod.io_card(p, app, graph)))
            forms.add(type(plan.flow).__name__)
            runs = _runs(plan.flow)
            assert sorted(runs) == sorted(a.id for a in p.activities), p.id
            assert [s.activity_id for s in plan.steps] == [n for n, _ in _walk_order(p) if n in {a.id for a in p.activities}]
    assert {"Seq", "Queue", "Try"} <= forms
