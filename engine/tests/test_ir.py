"""Tests for the neutral IR: models, catalogue, complexity and spec hash."""
import copy
import re
from pathlib import Path

import pytest

from engine.ir import catalogue, complexity, spec_hash
from engine.ir.models import (
    Activity,
    Application,
    Complexity,
    Contract,
    FaultHandler,
    Group,
    InputBinding,
    Process,
    ProcessCall,
    Resource,
    Starter,
    SubstVar,
    Transition,
    UnknownElement,
    Variable,
)


# ---------------------------------------------------------------- catalogue

def test_canonical_known():
    assert catalogue.canonical("bw.jdbc.update") == "jdbc.update"
    assert catalogue.canonical("com.tibco.plugin.jdbc.JDBCUpdateActivity") == "jdbc.update"
    assert catalogue.canonical("bw.restjson.JsonReceive") == "rest.receive"
    assert catalogue.canonical("bw.jms.sendmessage") == "jms.send"
    assert catalogue.canonical("bw.jms.getmessage") == "jms.receive"
    assert catalogue.canonical("bw.http.sendhttprequest") == "http.invoke"
    assert catalogue.canonical("bw.generalactivities.timer") == "timer.start"
    assert catalogue.canonical("bw.xml.transformxml") == "xslt.transform"
    assert catalogue.canonical("bw.java.JavaInvoke") == "java.invoke"
    assert catalogue.canonical("com.tibco.pe.core.CallProcessActivity") == "call.process"
    assert catalogue.canonical("com.tibco.plugin.jms.JMSTopicPublishActivity") == "jms.publish"
    assert catalogue.canonical("com.tibco.plugin.jms.JMSTopicEventSource") == "jms.receive"
    assert catalogue.canonical("com.tibco.plugin.soap.SOAPSendReplyActivity") == "reply"
    assert catalogue.canonical("com.tibco.pe.core.CriticalSectionGroup") == "group.critical"


@pytest.mark.parametrize(
    "native,expected",
    [
        ("invoke", "call.process"),
        ("reply", "reply"),
        ("throw", "throw"),
        ("rethrow", "throw"),
        ("assign", "assign"),
        ("empty", "end"),
        ("wait", "wait"),
        ("forEach", "group.iterate"),
        ("repeatUntil", "group.loop"),
        ("while", "group.loop"),
        ("flow", "flow.parallel"),
        ("tibex:transaction", "group.transaction"),
        ("bpel:invoke", "call.process"),
        ("ns0:reply", "reply"),
    ],
)
def test_canonical_bpel(native, expected):
    assert catalogue.canonical(native) == expected


def test_canonical_tolerant_lookup():
    # suffix tolerant: BW5 class name behind an unknown package prefix
    assert catalogue.canonical("x.y.z.JDBCUpdateActivity") == "jdbc.update"
    # prefix tolerant: trailing qualifier after a known native type
    assert catalogue.canonical("bw.jdbc.update:v2") == "jdbc.update"
    # case tolerant
    assert catalogue.canonical("BW.JDBC.Update") == "jdbc.update"
    # surrounding whitespace
    assert catalogue.canonical("  bw.jdbc.update ") == "jdbc.update"
    # boundary respected: sendmessage must not resolve through "send" prefix by accident
    assert catalogue.canonical("bw.jms.sendmessage") == "jms.send"


def test_canonical_unknown_returns_none():
    assert catalogue.canonical("bw.unknown.Thing") is None
    assert catalogue.canonical("com.tibco.plugin.nothing.FooActivity") is None
    assert catalogue.canonical("") is None
    assert catalogue.canonical("something.invoke") is None  # bare BPEL names are not suffix matched


def test_catalogue_consistency():
    assert "rest.receive" in catalogue.CANONICAL_TYPES
    assert len(catalogue.CANONICAL_TYPES) == 49
    assert set(catalogue.NATIVE_TO_CANONICAL.values()) <= catalogue.CANONICAL_TYPES
    # every canonical type is reachable from at least one native type
    assert set(catalogue.NATIVE_TO_CANONICAL.values()) == catalogue.CANONICAL_TYPES
    # canonical names resolve to themselves
    for c in catalogue.CANONICAL_TYPES:
        assert catalogue.canonical(c) == c


def test_group_kind():
    # real BW5 pd:groupType values
    assert catalogue.group_kind("inputLoop") == "iterate"
    assert catalogue.group_kind("repeat") == "loop"
    assert catalogue.group_kind("while") == "loop"
    assert catalogue.group_kind("repeatOnError") == "loop"
    assert catalogue.group_kind("criticalSection") == "critical"
    assert catalogue.group_kind("transactionGroup") == "transaction"
    assert catalogue.group_kind("pick") == "scope"
    assert catalogue.group_kind("none") == "scope"
    # plain words and class names
    assert catalogue.group_kind("loop") == "loop"
    assert catalogue.group_kind("iterate") == "iterate"
    assert catalogue.group_kind("transaction") == "transaction"
    assert catalogue.group_kind("critical") == "critical"
    assert catalogue.group_kind("com.tibco.pe.core.LoopGroup") == "loop"
    assert catalogue.group_kind("com.tibco.pe.core.TransactionGroup") == "transaction"
    assert catalogue.group_kind("pd:group") is None
    assert catalogue.group_kind("bogus") is None


@pytest.mark.parametrize(
    "native,expected",
    [
        ("bw.internal.end", "end"),
        ("bw.internal.accumulateend", "end"),
        ("bw.http.sendHTTPResponse", "reply"),
        ("bw.java.javamethod", "java.invoke"),
        ("bw.file.create", "file.write"),
        ("bw.internal.setEPR", "assign"),
        ("bw.restjson.JsonParser", "json.parse"),
        ("bw.restjson.JsonRender", "json.render"),
        ("bw.xml.parsexml", "xml.parse"),
        ("bw.xml.renderxml", "xml.render"),
        ("bw.mail.receive", "mail.receive"),
        ("bw.mail.sendmail", "mail.send"),
        ("com.tibco.pe.core.GetSharedVariableActivity", "shared.get"),
        ("com.tibco.pe.core.SetSharedVariableActivity", "shared.set"),
        ("com.tibco.plugin.xml.XMLParseActivity", "xml.parse"),
        ("com.tibco.plugin.parse.ParseActivity", "xml.parse"),
        ("com.tibco.plugin.xml.XMLRendererActivity", "xml.render"),
        ("com.tibco.plugin.json.activities.JSONParserActivity", "json.parse"),
        ("com.tibco.plugin.timer.NullActivity", "noop"),
        ("com.tibco.plugin.cmdexec.CmdExecActivity", "cmd.exec"),
        ("com.tibco.plugin.jms.JMSReplyActivity", "jms.reply"),
        ("com.tibco.plugin.jms.JMSQueueRequestReplyActivity", "jms.request_reply"),
        ("com.tibco.plugin.jms.JMSQueueSignalInActivity", "jms.receive"),
        ("com.tibco.plugin.jdbc.JDBCGeneralActivity", "jdbc.call"),
        ("com.tibco.plugin.ftp.FTPGetActivity", "ftp.get"),
        ("com.tibco.plugin.ftp.FTPPutActivity", "ftp.put"),
        ("com.tibco.plugin.file.ListFilesActivity", "file.list"),
        ("com.tibco.plugin.file.FileCreateActivity", "file.write"),
        ("com.tibco.plugin.mail.MailPubActivity", "mail.send"),
        ("com.tibco.pe.core.EngineCommandActivity", "engine.command"),
        ("com.tibco.plugin.json.activities.RestActivity", "rest.invoke"),
        ("com.tibco.plugin.json.rest.server.activities.RestAdapterActivity", "rest.invoke"),
    ],
)
def test_canonical_evidenced_types(native, expected):
    assert catalogue.canonical(native) == expected


def test_deliberately_unmapped_types():
    # Twitter and BusinessEvents plugins stay unmapped; parsers report them, never drop them.
    assert catalogue.canonical("com.tibco.plugin.twitter.activities.TwitterPublishActivity") is None
    assert catalogue.canonical("com.tibco.be.bw.plugin.BESendEvent") is None


# ------------------------------------------- harvested sample types (source of truth)

REPO_ROOT = Path(__file__).resolve().parents[2]
_BW6_TYPE = re.compile(r'activityTypeID="([^"]+)"')
_BW5_TYPE = re.compile(r"<pd:type>\s*(com\.tibco\.[^<\s]+)\s*</pd:type>")  # pd:type also holds line styles such as "solid"

# Types under samples/public that are deliberately NOT mapped, each with a reason.
PUBLIC_UNMAPPED_ALLOWLIST = {
    # Twitter plugin: no business-system equivalent in the .NET target; reported, not dropped.
    "com.tibco.plugin.twitter.activities.TwitterPublishActivity",
    # TIBCO BusinessEvents bridge: out of scope for BW to .NET migration; reported, not dropped.
    "com.tibco.be.bw.plugin.BESendEvent",
    # SOAP URL-access resource fetch (WSDL retrieval): no canonical type, needs an SME mapping decision.
    "com.tibco.plugin.soap.urlaccess.RetrieveResource",
}


def _harvest(root: Path) -> set:
    found = set()
    for path in list(root.glob("**/*.bwp")) + list(root.glob("**/*.process")):
        text = path.read_text(encoding="utf-8", errors="replace")
        found.update(_BW6_TYPE.findall(text))
        found.update(_BW5_TYPE.findall(text))
    return found


def test_harvested_synthetic_types_all_map():
    root = REPO_ROOT / "samples" / "synthetic-gmf"
    found = _harvest(root)
    assert found, "no activity types harvested from samples/synthetic-gmf"
    unmapped = sorted(t for t in found if catalogue.canonical(t) is None)
    assert unmapped == [], f"unmapped synthetic types: {unmapped}"


def test_harvested_public_types_subset_of_allowlist():
    root = REPO_ROOT / "samples" / "public"
    found = _harvest(root)
    assert found, "no activity types harvested from samples/public"
    unmapped = {t for t in found if catalogue.canonical(t) is None}
    print("unmapped public types:", sorted(unmapped))
    assert unmapped <= PUBLIC_UNMAPPED_ALLOWLIST, sorted(unmapped - PUBLIC_UNMAPPED_ALLOWLIST)


# ------------------------------------------------------------------- models

def _act(i, type_="mapper", **kw):
    return Activity(id=f"a{i}", name=f"Act{i}", type=type_, native_type="n", **kw)


def _proc(activities=(), transitions=(), **kw):
    return Process(
        id="p1", application="app", module="mod", package="pkg", name="Proc",
        dialect="bw6", source_path="x.bwp",
        activities=list(activities), transitions=list(transitions), **kw,
    )


def test_models_roundtrip():
    p = _proc(
        activities=[
            Activity(
                id="a1", name="Q", type="jdbc.query", native_type="bw.jdbc.JDBCQuery",
                config={"sql": "select 1"},
                input_bindings=[InputBinding(target="p", expression="$x/y", language="xpath", source_line=3)],
                output_schema="Out", source_line=2, group_id=None,
            )
        ],
        transitions=[Transition(id="t1", from_id="a1", to_id="a2", kind="success", expression=None, source_line=None)],
        groups=[Group(id="g1", kind="loop", member_ids=["a1"], config={})],
        starter=Starter(activity_id="a1", kind="rest", config={}),
        variables=[Variable(name="v", schema=None, scope="process")],
        fault_handlers=[FaultHandler(id="f1", fault_name=None, activity_ids=["a1"])],
        calls=[ProcessCall(activity_id="a1", target_process="other")],
        unknown_elements=[UnknownElement(path="/a", tag="x", reason="r")],
        contracts_used=["c1"],
        complexity=Complexity(score=1.0, band="low", factors={"activities": 1.0}),
    )
    again = Process.model_validate(p.model_dump(mode="json"))
    assert again == p
    assert again.raw_xml_sha256 == ""

    app = Application(
        id="app", name="App", dialect="bw6", modules=["mod"], processes=[p],
        resources=[Resource(id="r", kind="jdbc", name="db", source_path="r.jdbc", settings={}, masked_fields=["password"])],
        substvars=[SubstVar(name="n", value="v", type="String", source_path="s")],
        contracts=[Contract(id="c1", kind="xsd", path="a.xsd", root_elements=["R"], text="<x/>")],
        sql_files=["a.sql"],
    )
    assert Application.model_validate(app.model_dump(mode="json")) == app


def test_models_reject_bad_literals():
    with pytest.raises(Exception):
        Transition(id="t", from_id="a", to_id="b", kind="bogus", expression=None, source_line=None)
    with pytest.raises(Exception):
        Variable(name="v", schema=None, scope="global")


# --------------------------------------------------------------- complexity

def test_complexity_bands():
    small = _proc(
        activities=[_act(1, "rest.receive"), _act(2, "mapper"), _act(3, "reply")],
        transitions=[
            Transition(id="t1", from_id="a1", to_id="a2", kind="success", expression=None, source_line=None),
            Transition(id="t2", from_id="a2", to_id="a3", kind="success", expression=None, source_line=None),
        ],
    )
    c = complexity.score(small)
    assert c.band == "low"
    assert 0 <= c.score <= 3.5
    assert set(c.factors) == {
        "activities", "branches", "mapping_size", "functions", "xslt",
        "java", "subprocess_depth", "group_nesting", "external_systems",
    }

    acts = [_act(i, "mapper") for i in range(1, 25)]
    acts.append(_act(25, "xslt.transform"))
    trs = [
        Transition(id=f"c{i}", from_id=f"a{i}", to_id=f"a{i + 1}", kind="conditional", expression="$x = 1", source_line=None)
        for i in range(1, 9)
    ]
    big = _proc(activities=acts, transitions=trs)
    cb = complexity.score(big)
    assert cb.band == "high"
    assert cb.score >= 7
    assert cb.score <= 10


def test_complexity_medium_and_clamp():
    acts = [_act(i, "mapper") for i in range(1, 21)]
    acts[0] = _act(1, "jdbc.query")
    acts[1] = _act(2, "http.invoke")
    trs = [
        Transition(id=f"c{i}", from_id=f"a{i}", to_id=f"a{i + 1}", kind="conditional", expression="x", source_line=None)
        for i in range(1, 7)
    ]
    mid = complexity.score(_proc(activities=acts, transitions=trs))
    assert mid.band == "medium"
    assert 3.5 <= mid.score < 6.5

    # everything at once is clamped to 10, and the factors still sum above it
    types = ["java.invoke", "xslt.transform", "jdbc.query", "http.invoke", "jms.send", "file.read"]
    acts = [_act(i, types[i % len(types)]) for i in range(1, 60)]
    acts[0] = Activity(
        id="a1", name="M", type="mapper", native_type="n",
        input_bindings=[InputBinding(target="t", expression="concat($a, 'x') " * 150, language="xpath")],
    )
    trs = [
        Transition(id=f"c{i}", from_id=f"a{i}", to_id=f"a{i + 1}", kind="error", expression=None, source_line=None)
        for i in range(1, 40)
    ]
    groups = [Group(id=f"g{i}", kind="scope", member_ids=[f"g{i + 1}"], config={}) for i in range(1, 5)]
    calls = [ProcessCall(activity_id="a1", target_process=f"p{i}") for i in range(5)]
    huge = complexity.score(_proc(activities=acts, transitions=trs, groups=groups, calls=calls))
    assert huge.score == 10
    assert huge.band == "high"
    assert sum(huge.factors.values()) > 10


def test_complexity_detects_signals():
    a = Activity(
        id="a1", name="M", type="mapper", native_type="n",
        input_bindings=[
            InputBinding(target="t", expression="concat(upper-case($a), substring($b, 1, 3))", language="xpath", source_line=None),
        ],
    )
    j = _act(2, "java.invoke")
    jdbc = _act(3, "jdbc.query")
    http = _act(4, "http.invoke")
    g_outer = Group(id="g1", kind="scope", member_ids=["g2"], config={})
    g_inner = Group(id="g2", kind="loop", member_ids=["a1"], config={})
    p = _proc(
        activities=[a, j, jdbc, http],
        groups=[g_outer, g_inner],
        calls=[ProcessCall(activity_id="a1", target_process="x"), ProcessCall(activity_id="a2", target_process="y")],
    )
    c = complexity.score(p)
    assert c.factors["functions"] > 0
    assert c.factors["mapping_size"] > 0
    assert c.factors["java"] > 0
    assert c.factors["external_systems"] > 0
    assert c.factors["group_nesting"] > 0
    assert c.factors["subprocess_depth"] > 0
    assert c.factors["xslt"] == 0
    # an explicit depth from the caller overrides the local proxy
    deeper = complexity.score(p, subprocess_depth=3)
    assert deeper.factors["subprocess_depth"] >= c.factors["subprocess_depth"]


# ---------------------------------------------------------------- spec hash

def test_spec_hash_stable_and_sensitive():
    ir = {"id": "p1", "activities": [{"id": "a1", "z": 1, "a": 2}]}
    rules = [{"id": "r1", "text": "Reject negative amounts"}, {"id": "r2", "text": "Round half up"}]
    decisions = [{"finding": "f1", "decision": "fix"}]

    h1 = spec_hash.spec_hash(ir, rules, decisions)
    h2 = spec_hash.spec_hash(copy.deepcopy(ir), copy.deepcopy(rules), copy.deepcopy(decisions))
    assert h1 == h2
    assert len(h1) == 64 and int(h1, 16) >= 0

    # key order does not matter
    assert spec_hash.spec_hash({"activities": [{"a": 2, "z": 1, "id": "a1"}], "id": "p1"}, rules, decisions) == h1
    # rule and decision list order does not matter
    assert spec_hash.spec_hash(ir, list(reversed(rules)), decisions) == h1
    two = decisions + [{"finding": "f2", "decision": "keep"}]
    assert spec_hash.spec_hash(ir, rules, two) == spec_hash.spec_hash(ir, rules, list(reversed(two)))

    changed = copy.deepcopy(rules)
    changed[0]["text"] = "Reject negative or zero amounts"
    assert spec_hash.spec_hash(ir, changed, decisions) != h1
    assert spec_hash.spec_hash(ir, rules, []) != h1
    assert spec_hash.spec_hash({**ir, "id": "p2"}, rules, decisions) != h1
