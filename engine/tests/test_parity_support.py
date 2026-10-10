"""Building blocks of the parity evaluator: models, XPath, XSLT, stubs, scenario database."""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
from lxml import etree

from engine.parity import xslt as xslt_mod
from engine.parity.db import ScenarioDB, qmark_to_format
from engine.parity.models import (
    ExpectedResult,
    FieldDiff,
    ParityReport,
    Scenario,
    SeedRow,
    StubResponse,
    dict_to_xml,
    xml_to_dict,
)
from engine.parity.stubs import StubRegistry
from engine.parity.xpath import XPathContext
from engine.parity.xslt import bw5_binding_stylesheet, rewrite_tib_calls, transform

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH = REPO_ROOT / "samples" / "synthetic-gmf"
DECISION_BWP = SYNTH / "CreditOrigination/Processes/creditorigination/DecisionEngine.bwp"
DEFAULT_DSN = "postgresql://exiqo:exiqo@localhost:5432/exiqo_test"

DECISION_NS = "http://xmlns.gmfin.example/credit/origination/decision"


def _binding(activity: str) -> str:
    """The tibex:inputBinding XSLT of one activity in DecisionEngine.bwp."""
    tree = etree.parse(str(DECISION_BWP))
    for act in tree.iter("{http://www.tibco.com/bpel/2007/extensions}activityExtension"):
        if act.get("name") == activity:
            for b in act.iter("{http://www.tibco.com/bpel/2007/extensions}inputBinding"):
                return b.get("expression")
    raise AssertionError(f"no inputBinding for {activity}")


def _transition_conditions() -> list[str]:
    tree = etree.parse(str(DECISION_BWP))
    bpws = "{http://docs.oasis-open.org/wsbpel/2.0/process/executable}"
    return [c.text for c in tree.iter(f"{bpws}transitionCondition")]


def _tier_links() -> list[tuple[str, str]]:
    """(link name, condition) of the ComputeDTI outgoing transitions, document order."""
    tree = etree.parse(str(DECISION_BWP))
    bpws = "{http://docs.oasis-open.org/wsbpel/2.0/process/executable}"
    return [
        (src.get("linkName"), src.find(f"{bpws}transitionCondition").text)
        for src in tree.iter(f"{bpws}source")
        if src.get("linkName", "").startswith("ComputeDTIToAssign")
    ]


LEGACY = SYNTH / "LegacyAddress" / "LegacyAddressStandardize.process"
PD = "{http://xmlns.tibco.com/bw/process/2003}"


def _legacy_bindings(activity: str) -> etree._Element:
    for act in etree.parse(str(LEGACY)).iter(f"{PD}activity"):
        if act.get("name") == activity:
            return act.find(f"{PD}inputBindings")
    raise AssertionError(activity)


def _start(score: int = 750, debt: str = "1500", income: str = "5000") -> etree._Element:
    return dict_to_xml(
        {
            "parameters": {
                "applicationId": "APP-1",
                "creditScore": score,
                "singleBureau": False,
                "monthlyDebt": debt,
                "monthlyIncome": income,
                "requestedAmount": "20000.00",
                "requestedTermMonths": 60,
            }
        },
        "DecisionRequest",
    )


# --- models -------------------------------------------------------------------


def test_dict_xml_roundtrip():
    src = {"a": {"b": "1", "c": ["x", "y"]}, "flag": True, "n": 5}
    el = dict_to_xml(src, "Root")
    assert el.tag == "Root"
    assert [c.text for c in el.find("a").findall("c")] == ["x", "y"]
    assert el.find("flag").text == "true"
    back = xml_to_dict(el)
    assert back == {"a": {"b": "1", "c": ["x", "y"]}, "flag": "true", "n": "5"}
    # a known repeating element stays a list even when it occurs once
    one = dict_to_xml({"row": [{"id": 1}]}, "R")
    assert xml_to_dict(one, lists={"row"}) == {"row": [{"id": "1"}]}


def test_xml_to_dict_strips_namespaces():
    el = etree.fromstring('<t:R xmlns:t="urn:t"><t:a>1</t:a><b/></t:R>')
    assert xml_to_dict(el) == {"a": "1", "b": ""}


def test_scenario_models():
    sc = Scenario(
        id="s1",
        process_id="p",
        name="happy",
        kind="happy",
        input={"x": 1},
        stub_responses={"bureau/pull": StubResponse(status=200, body={"score": 700})},
        seed_state=[SeedRow(table="dealer", row={"dealer_id": "D1"})],
    )
    assert sc.source == "generated" and sc.expected is None
    assert StubResponse(status=500).delay_ms == 0
    exp = ExpectedResult(output={"tier": "A"}, source="model")
    assert exp.side_effects == [] and exp.fault is None and exp.trace == []
    d = FieldDiff(path="tier", expected="A", actual="B", status="defect")
    assert d.finding_id is None
    rep = ParityReport(scenario_id="s1", fields=[d], side_effects=[], parity=0.0, unexplained=1)
    assert rep.unexplained == 1
    with pytest.raises(ValueError):
        Scenario(id="s", process_id="p", name="n", kind="bogus", input={})


# --- xpath --------------------------------------------------------------------


def test_xpath_variable_path():
    ctx = XPathContext({"Start": _start()}, {})
    assert ctx.evaluate("$Start/parameters/applicationId") == "APP-1"
    assert ctx.evaluate("string-length($Start/parameters/applicationId) > 0") is True
    assert ctx.evaluate("count($Start/parameters/*)") == 7
    assert ctx.evaluate("exists($Start/parameters/missing)") is False
    assert ctx.evaluate("$Start/parameters/creditScore") == "750"
    # a variable that holds a bare value is also supported
    assert XPathContext({"n": 3}, {}).evaluate("$n + 1") == 4


def test_xpath_dti_arithmetic():
    # the actual expression from the DecisionEngine ComputeDTI input binding
    sheet = etree.fromstring(_binding("ComputeDTI").split("?>", 1)[1])
    select = sheet.xpath("//*[local-name()='value-of']/@select")[0]
    assert select == (
        "number($Start/parameters/monthlyDebt) div number($Start/parameters/monthlyIncome)"
    )
    ctx = XPathContext({"Start": _start(debt="1500", income="5000")}, {})
    assert ctx.evaluate(select) == pytest.approx(0.3)


@pytest.mark.parametrize(
    ("score", "dti", "tier"),
    [(750, "0.30", "A"), (700, "0.30", "B"), (650, "0.40", "C"), (600, "0.30", "decline")],
)
def test_xpath_transition_conditions_of_decision_engine(score, dti, tier):
    # BW rule: the first true conditional transition wins, else the ##otherwise## one
    ctx = XPathContext(
        {"Start": _start(score), "ComputeDTI": dict_to_xml({"dti": dti}, "DtiResult")}, {}
    )
    chosen = "decline"
    for link, cond in _tier_links():
        if cond != "##otherwise##" and ctx.evaluate(cond) is True:
            chosen = link.removeprefix("ComputeDTIToAssignTier")
            break
    assert chosen == tier


def test_xpath_namespaced_paths_and_xsd_constructors():
    pay = etree.fromstring(
        '<Body xmlns:tns="urn:pay"><tns:Payment><tns:amount>12.50</tns:amount>'
        "<tns:receivedDate>2026-03-05</tns:receivedDate></tns:Payment></Body>"
    )
    ctx = XPathContext({"ReceivePayment": pay}, {}, namespaces={"tns": "urn:pay"})
    assert ctx.evaluate("xsd:decimal($ReceivePayment/tns:Payment/tns:amount) * 2") == 25
    d = ctx.evaluate("xsd:date($ReceivePayment/tns:Payment/tns:receivedDate) + xs:dayTimeDuration('P10D')")
    assert str(d) == "2026-03-15"


def test_xpath_bw_functions():
    ctx = XPathContext({}, {"bureau.url": "http://bureau.local", "late.days": "10"})
    assert ctx.evaluate("bw:getModuleProperty('bureau.url')") == "http://bureau.local"
    assert ctx.evaluate("xsd:integer(bw:getModuleProperty('late.days')) >= 10") is True
    with pytest.raises(KeyError):
        ctx.evaluate("bw:getModuleProperty('nope')")
    assert ctx.evaluate("tib:trim('  ab  ')") == "ab"
    el = etree.fromstring("<r><a> x </a></r>")
    assert XPathContext({"v": el}, {}).evaluate("tib:trim($v/a)") == "x"
    assert ctx.evaluate("upper-case(tib:trim(' ab '))") == "AB"


def test_xpath_tib_datetime_functions():
    ctx = XPathContext({}, {})
    parsed = ctx.evaluate("tib:parse-dateTime('yyyy-MM-dd HH:mm:ss', '2026-03-05 14:30:00')")
    assert str(parsed) == "2026-03-05T14:30:00"
    assert (
        ctx.evaluate(
            "tib:format-dateTime('dd/MM/yyyy HH:mm', xs:dateTime('2026-03-05T14:30:00'))"
        )
        == "05/03/2026 14:30"
    )
    # the clock is injectable so the evaluator can freeze time for reproducible results
    frozen = XPathContext({}, {}, now="2026-01-02T03:04:05")
    assert str(frozen.evaluate("tib:current-dateTime()")) == "2026-01-02T03:04:05"
    assert frozen.evaluate("tib:timestamp()") == "2026-01-02T03:04:05"
    assert str(frozen.evaluate("current-dateTime()")).startswith("2026-01-02T03:04:05")


def test_xpath_render_xml():
    el = etree.fromstring("<r><a>1</a></r>")
    out = XPathContext({"n": el}, {}).evaluate("tib:render-xml($n)")
    assert out == "<r><a>1</a></r>"


# --- xslt ---------------------------------------------------------------------


def test_xslt_binding_transform():
    ctx = XPathContext(
        {"Start": _start(750), "ComputeDTI": dict_to_xml({"dti": "0.3"}, "DtiResult")}, {}
    )
    out = transform(_binding("AssignTierA"), ctx)
    assert etree.QName(out).localname == "TierAssignment"
    assert out.find("tier").text == "A"
    assert out.find("decision").text == "APPROVED"
    assert out.find("apr").text == "5.49"
    assert out.find("approvedAmount").text == "20000.00"


def test_xslt_tier_c_caps_term_at_72_months():
    start = _start(650)
    start.find("parameters/requestedTermMonths").text = "84"
    ctx = XPathContext({"Start": start, "ComputeDTI": dict_to_xml({"dti": "0.4"}, "DtiResult")}, {})
    out = transform(_binding("AssignTierC"), ctx)
    assert out.find("tier").text == "C"
    assert out.find("approvedTermMonths").text == "72"
    start.find("parameters/requestedTermMonths").text = "48"
    assert transform(_binding("AssignTierC"), ctx).find("approvedTermMonths").text == "48"


def test_xslt_decline_has_no_tier():
    ctx = XPathContext(
        {"Start": _start(600), "ComputeDTI": dict_to_xml({"dti": "0.5"}, "DtiResult")}, {}
    )
    out = transform(_binding("AssignDecline"), ctx)
    assert out.find("tier") is None
    assert out.find("decision").text == "DECLINED"


def _http_receiver(**params: str) -> etree._Element:
    return dict_to_xml({"ProcessStarterOutput": {"parameters": params}}, "HTTP-Receiver")


def test_xslt_legacy_bw5_binding_with_tib_trim():
    sheet = bw5_binding_stylesheet(_legacy_bindings("Standardize Address"))
    ctx = XPathContext(
        {
            "HTTP-Receiver": _http_receiver(
                line1="  12 main st ", line2="   ", city=" columbus ", state=" oh ",
                zip=" 43215-1234 ",
            )
        },
        {},
    )
    out = transform(sheet, ctx)
    assert etree.QName(out).localname == "StandardizedAddress"
    got = {etree.QName(c).localname: c.text for c in out}
    assert got == {"line1": "12 MAIN ST", "city": "COLUMBUS", "state": "OH", "postalCode": "43215"}
    ctx.variables["HTTP-Receiver"] = _http_receiver(
        line1="1 a st", line2=" apt 4 ", city="x", state="y", zip="12345")
    out = transform(sheet, ctx)
    assert out.find("{*}line2").text == "APT 4"


def test_xslt_legacy_bw5_binding_with_tib_render_xml():
    std = transform(
        bw5_binding_stylesheet(_legacy_bindings("Standardize Address")),
        XPathContext({"HTTP-Receiver": _http_receiver(
            line1="1 a st", line2="", city="c", state="s", zip="99999")}, {}),
    )
    wrapper = etree.Element("Standardize-Address")
    wrapper.append(std)
    out = transform(
        bw5_binding_stylesheet(_legacy_bindings("Send HTTP Response")),
        XPathContext({"Standardize-Address": wrapper}, {}),
    )
    body = out.find("asciiContent").text
    assert "StandardizedAddress" in body and ">1 A ST<" in body
    assert out.find("Headers/StatusLine").text == "HTTP/1.1 200 OK"


def test_xslt_legacy_binding_lxml_fallback_extensions(monkeypatch):
    monkeypatch.setattr(xslt_mod, "_saxon_available", lambda: False)
    sheet = bw5_binding_stylesheet(_legacy_bindings("Send HTTP Response"))
    pfx = _legacy_bindings("Send HTTP Response").nsmap["pfx"]
    wrapper = etree.fromstring(
        f'<Standardize-Address><p:StandardizedAddress xmlns:p="{pfx}"><v> x </v>'
        "</p:StandardizedAddress></Standardize-Address>"
    )
    with pytest.warns(UserWarning, match="xslt downgraded"):
        out = transform(sheet, XPathContext({"Standardize-Address": wrapper}, {}))
    assert "<v> x </v>" in out.find("asciiContent").text


def test_rewrite_tib_calls():
    assert rewrite_tib_calls("upper-case(tib:trim($a/b))") == (
        "upper-case(replace(replace(string($a/b), '^\\s+', ''), '\\s+$', ''))"
    )
    assert rewrite_tib_calls("tib:render-xml($x, true())") == "serialize($x)"
    # nested, quoted parentheses, untouched foreign prefixes
    assert rewrite_tib_calls("tib:trim(tib:trim('a)'))").count("replace(") == 4
    assert rewrite_tib_calls("other:trim($a)") == "other:trim($a)"


def test_xslt_dti_binding():
    ctx = XPathContext({"Start": _start(debt="1500", income="5000")}, {})
    out = transform(_binding("ComputeDTI"), ctx)
    assert etree.QName(out).namespace == DECISION_NS
    assert float(out.find("dti").text) == pytest.approx(0.3)


def test_xslt_unbound_param_is_empty_sequence():
    # RecordDecision declares AssignTierA..AssignDecline; only the branch that ran is bound
    tier = transform(
        _binding("AssignTierA"),
        XPathContext(
            {"Start": _start(750), "ComputeDTI": dict_to_xml({"dti": "0.3"}, "DtiResult")}, {}
        ),
    )
    ctx = XPathContext(
        {
            "Start": _start(750),
            "ComputeDTI": dict_to_xml({"dti": "0.3"}, "DtiResult"),
            "AssignTierA": tier,
        },
        {},
    )
    out = transform(_binding("RecordDecision"), ctx)
    assert out.find("tier").text == "A"
    assert out.find("applicationId").text == "APP-1"
    assert out.find("dti").text == "0.3"
    assert out.find("singleBureau").text == "false"


def test_xslt_uses_saxon_for_xslt2():
    ctx = XPathContext({"Start": _start()}, {})
    sheet = (
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="2.0">'
        '<xsl:param name="Start"/><xsl:template match="/"><r>'
        '<xsl:value-of select="upper-case($Start/parameters/applicationId)"/>'
        "</r></xsl:template></xsl:stylesheet>"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = transform(sheet, ctx)
    assert out.text == "APP-1"


def test_xslt_lxml_fallback_warns(monkeypatch):
    monkeypatch.setattr(xslt_mod, "_saxon_available", lambda: False)
    ctx = XPathContext({"Start": _start(debt="1500", income="5000")}, {})
    with pytest.warns(UserWarning, match="xslt downgraded to 1.0"):
        out = transform(_binding("ComputeDTI"), ctx)
    assert float(out.find("dti").text) == pytest.approx(0.3)


# --- stubs --------------------------------------------------------------------


def test_stub_registry_returns_registered_response():
    reg = StubRegistry()
    reg.register("equifax", "/score", StubResponse(status=200, body={"score": 712}))
    resp = reg.call("equifax", "/score", {"ssn": "9"})
    assert resp.status == 200 and resp.body == {"score": 712}
    assert reg.calls[0].resource_id == "equifax" and reg.calls[0].body == {"ssn": "9"}
    with pytest.raises(LookupError):
        reg.call("equifax", "/other", {})


def test_stub_timeout():
    reg = StubRegistry(timeout_ms=5000)
    reg.register("transunion", "/score", StubResponse(status=200, body={}, delay_ms=60000))
    with pytest.raises(TimeoutError):
        reg.call("transunion", "/score", {})
    assert reg.calls[0].outcome == "timeout"
    # a per-call timeout longer than the delay lets the response through
    assert reg.call("transunion", "/score", {}, timeout_ms=120000).status == 200


def test_stub_from_scenario_keys_and_globs():
    reg = StubRegistry.from_stub_responses(
        {
            "equifax/score": StubResponse(status=200, body={"score": 1}),
            "vinapi": StubResponse(status=404),
        }
    )
    assert reg.call("equifax", "/score", None).body == {"score": 1}
    assert reg.call("vinapi", "/anything/else", None).status == 404


# --- scenario database --------------------------------------------------------


def _dsn() -> str:
    # Default to the local test database; an unreachable database is a failure, not a skip.
    return os.environ.get("TEST_DATABASE_URL") or DEFAULT_DSN


def test_qmark_to_format():
    assert qmark_to_format("select * from t where a = ? and b = ?") == (
        "select * from t where a = %s and b = %s"
    )
    assert qmark_to_format("select '?' , 5 % 2, ?") == "select '?' , 5 %% 2, %s"


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("select 1 /* why? 5%2 */ + ?", "select 1 /* why? 5%%2 */ + %s"),
        ("select /* a /* nested ? */ still ? */ ?", "select /* a /* nested ? */ still ? */ %s"),
        ("select 1 -- ? 100%\n, ?", "select 1 -- ? 100%%\n, %s"),
        ("select $$a ? 'b$$, ?", "select $$a ? 'b$$, %s"),
        ("select $q$it's ? $$ here$q$, ?", "select $q$it's ? $$ here$q$, %s"),
        ("select E'it\\'s ?', ?", "select E'it\\'s ?', %s"),
        ("select 'it''s ?', ?", "select 'it''s ?', %s"),
        ("select \"a?b\", ?", "select \"a?b\", %s"),
        ("select doc ?| array['a'] from t where x = ?", "select doc ?| array['a'] from t where x = %s"),
        ("select doc ?& array['a'] and y = ?", "select doc ?& array['a'] and y = %s"),
        ("select ?||'%'", "select %s||'%%'"),
        ("select $1, ?", "select $1, %s"),
        ("select coalesce(a,0)::text || 'e' , ?", "select coalesce(a,0)::text || 'e' , %s"),
    ],
)
def test_qmark_to_format_skips_literals_comments_and_operators(statement, expected):
    assert qmark_to_format(statement) == expected


def test_reset_refuses_non_test_database_without_override():
    db = ScenarioDB("postgresql://exiqo:exiqo@localhost:5432/exiqo")
    with pytest.raises(ValueError, match="refusing to reset"):
        db.reset("select 1", "select 1")
    # the guard fires before any connection is made
    assert db._conn is None


def test_scenario_db_reset_and_transaction_rollback():
    db = ScenarioDB(_dsn())
    try:
        db.reset(
            SYNTH / "Database" / "schema.sql",
            SYNTH / "Database" / "seed.sql",
            [
                SeedRow(
                    table="dealer",
                    row={
                        "dealer_id": "DX1",
                        "dealer_name": "EXTRA MOTORS",
                        "state_code": "TX",
                        "status": "ACTIVE",
                        "funding_account": "FA-1",
                    },
                )
            ],
        )
        assert db.execute("select count(*) as n from gmf.dealer")[0]["n"] == 6
        rows = db.execute("select dealer_name from gmf.dealer where dealer_id = ?", ["DX1"])
        assert rows == [{"dealer_name": "EXTRA MOTORS"}]

        n = db.execute("update gmf.dealer set status = ? where dealer_id = ?", ["SUSPENDED", "DX1"])
        assert n == 1

        with pytest.raises(RuntimeError, match="boom"), db.transaction():
            db.execute("delete from gmf.dealer where dealer_id = ?", ["DX1"])
            assert db.execute("select count(*) as n from gmf.dealer")[0]["n"] == 5
            raise RuntimeError("boom")
        assert db.execute("select count(*) as n from gmf.dealer")[0]["n"] == 6

        with db.transaction():
            db.execute("delete from gmf.dealer where dealer_id = ?", ["DX1"])
        assert db.execute("select count(*) as n from gmf.dealer")[0]["n"] == 5

        # literal % is safe with and without parameters
        assert db.execute("select 7 % 4 as m")[0]["m"] == 3
        assert db.execute("select 7 % 4 as m, ? as p", [1])[0]["m"] == 3

        # reset is repeatable and restores the seed
        db.reset(SYNTH / "Database" / "schema.sql", SYNTH / "Database" / "seed.sql", [])
        assert db.execute("select count(*) as n from gmf.dealer")[0]["n"] == 5
    finally:
        db.close()


def test_scenario_db_seed_rows_accept_json_and_qualified_tables():
    db = ScenarioDB(_dsn())
    try:
        db.reset("create table gmf.t (id int primary key, doc jsonb);", "", [
            SeedRow(table="t", row={"id": 1, "doc": {"a": [1, 2]}}),
            SeedRow(table="gmf.t", row={"id": 2, "doc": None}),
        ])
        assert db.execute("select doc from gmf.t where id = ?", [1]) == [{"doc": {"a": [1, 2]}}]
        assert db.execute("select count(*) as n from t")[0]["n"] == 2
    finally:
        db.close()
