"""Canonical activity catalogue (design spec section 4.2).

Every BW5 and BW6 native activity type maps to exactly one canonical type.
Unknown native types resolve to None; the caller reports them and the gate for
that process stays blocked until a mapping is added here.

Notes on ambiguity that only a parser can resolve (from bindings, not names):
  * BPEL ``receive`` defaults to ``rest.receive`` and BPEL ``invoke`` to
    ``call.process``; the parser refines both from the partner link binding.
  * ``jms.publish`` is reached natively only from BW5 ``JMSTopicPublishActivity``.
    A BW6 ``bw.jms.send`` aimed at a topic stays ``jms.send`` unless the parser
    says otherwise.
"""
import re
from typing import Dict, FrozenSet, List, Optional, Tuple

CANONICAL_TYPES: FrozenSet[str] = frozenset(
    {
        "rest.receive", "http.receive", "soap.receive", "jms.receive", "timer.start",
        "jdbc.query", "jdbc.update", "jdbc.call",
        "http.invoke", "rest.invoke", "soap.invoke",
        "jms.send", "jms.publish",
        "call.process", "mapper", "assign", "throw", "catch", "reply", "end", "log",
        "java.invoke", "xslt.transform", "file.read", "file.write",
        "group.loop", "group.iterate", "group.transaction", "group.critical",
        "flow.parallel", "wait", "checkpoint", "confirm",
    }
)

# BW6 native types (as in tibex:activityType-style attributes and binding namespaces).
_BW6: Dict[str, List[str]] = {
    "rest.receive": [
        "bw.restjson.JsonReceive", "bw.restjson.RestService", "bw.restjson.RestReceive",
        "bw.restjson.receive", "bw.rest.RestService", "bw.rest.RestReceive",
    ],
    "rest.invoke": [
        "bw.restjson.RestReference", "bw.restjson.RestInvoke", "bw.restjson.invoke",
        "bw.rest.RestReference", "bw.rest.RestInvoke",
    ],
    "http.receive": ["bw.http.receive", "bw.http.HTTPReceiver"],
    "http.invoke": ["bw.http.sendhttprequest", "bw.http.SendHTTPRequest"],
    "soap.receive": ["bw.soap.receive", "bw.soap.SoapService", "bw.soap.service"],
    "soap.invoke": ["bw.soap.invoke", "bw.soap.SoapReference", "bw.soap.reference", "bw.soap.sendsoaprequest"],
    "jms.receive": ["bw.jms.receive", "bw.jms.getmessage"],
    "jms.send": ["bw.jms.send", "bw.jms.sendmessage"],
    "timer.start": ["bw.generalactivities.timer"],
    "jdbc.query": ["bw.jdbc.JDBCQuery", "bw.jdbc.query"],
    "jdbc.update": ["bw.jdbc.update", "bw.jdbc.JDBCUpdate"],
    "jdbc.call": ["bw.jdbc.callprocedure", "bw.jdbc.JDBCCallProcedure"],
    "call.process": ["bw.internal.callprocess", "bw.generalactivities.callprocess"],
    "mapper": ["bw.generalactivities.mapper"],
    "assign": ["bw.generalactivities.assign"],
    "throw": ["bw.generalactivities.throw", "bw.generalactivities.rethrow"],
    "catch": ["bw.generalactivities.catch"],
    "end": ["bw.generalactivities.end"],
    "log": ["bw.generalactivities.log"],
    "wait": ["bw.generalactivities.wait"],
    "java.invoke": ["bw.java.JavaInvoke"],
    "xslt.transform": ["bw.xml.transformxml"],
    "file.read": ["bw.file.read"],
    "file.write": ["bw.file.write"],
    "checkpoint": ["bw.internal.checkpoint"],
    "confirm": ["bw.internal.confirm"],
}

# BW6 / BPEL structural element names. Bare names and the bpel:/bpws:/tibex: prefixed forms.
_BPEL: Dict[str, str] = {
    "receive": "rest.receive",  # default; parser refines from the binding
    "invoke": "call.process",  # default; parser refines from the binding
    "reply": "reply",
    "throw": "throw",
    "rethrow": "throw",
    "assign": "assign",
    "empty": "end",
    "exit": "end",
    "wait": "wait",
    "catch": "catch",
    "catchAll": "catch",
    "forEach": "group.iterate",
    "repeatUntil": "group.loop",
    "while": "group.loop",
    "flow": "flow.parallel",
}
_BPEL_PREFIXES: Tuple[str, ...] = ("bpel", "bpws")
_TIBEX: Dict[str, str] = {
    "transaction": "group.transaction",
    "criticalSection": "group.critical",
    "critical": "group.critical",
    "checkpoint": "checkpoint",
    "confirm": "confirm",
}

# BW5 plugin and process-engine types.
_BW5: Dict[str, List[str]] = {
    "http.receive": ["com.tibco.plugin.http.HTTPEventSource"],
    "soap.receive": ["com.tibco.plugin.soap.SOAPEventSource"],
    "jms.receive": [
        "com.tibco.plugin.jms.JMSQueueEventSource", "com.tibco.plugin.jms.JMSTopicEventSource",
        "com.tibco.plugin.jms.JMSQueueGetMessageActivity",
    ],
    "timer.start": ["com.tibco.plugin.timer.TimerEventSource"],
    "jdbc.query": ["com.tibco.plugin.jdbc.JDBCQueryActivity"],
    "jdbc.update": ["com.tibco.plugin.jdbc.JDBCUpdateActivity"],
    "jdbc.call": ["com.tibco.plugin.jdbc.JDBCCallActivity"],
    "http.invoke": ["com.tibco.plugin.http.HTTPSendRequestActivity"],
    "soap.invoke": ["com.tibco.plugin.soap.SOAPSendRequestActivity", "com.tibco.plugin.soap.SOAPSendReceiveActivity"],
    "jms.send": ["com.tibco.plugin.jms.JMSQueueSendActivity"],
    "jms.publish": ["com.tibco.plugin.jms.JMSTopicPublishActivity"],
    "call.process": ["com.tibco.pe.core.CallProcessActivity"],
    "mapper": ["com.tibco.plugin.mapper.MapperActivity"],
    "assign": ["com.tibco.pe.core.AssignActivity"],
    "throw": ["com.tibco.pe.core.GenerateErrorActivity"],
    "catch": ["com.tibco.pe.core.CatchActivity"],
    "reply": ["com.tibco.plugin.http.HTTPResponseActivity", "com.tibco.plugin.soap.SOAPSendReplyActivity"],
    "end": ["pd:end"],
    "log": ["com.tibco.pe.core.WriteToLogActivity"],
    "java.invoke": ["com.tibco.plugin.java.JavaActivity", "com.tibco.plugin.java.JavaMethodActivity"],
    "xslt.transform": ["com.tibco.plugin.xml.XMLTransformActivity"],
    "file.read": ["com.tibco.plugin.file.FileReadActivity"],
    "file.write": ["com.tibco.plugin.file.FileWriteActivity"],
    "group.loop": ["com.tibco.pe.core.LoopGroup"],
    "group.transaction": ["com.tibco.pe.core.TransactionGroup"],
    "group.critical": ["com.tibco.pe.core.CriticalSectionGroup"],
    "wait": ["com.tibco.pe.core.WaitActivity"],
    "checkpoint": ["com.tibco.pe.core.CheckpointActivity"],
}


def _build() -> Dict[str, str]:
    table: Dict[str, str] = {}
    for src in (_BW6, _BW5):
        for canon, natives in src.items():
            for n in natives:
                table[n] = canon
    for name, canon in _BPEL.items():
        table[name] = canon
        for prefix in _BPEL_PREFIXES:
            table[f"{prefix}:{name}"] = canon
    for name, canon in _TIBEX.items():
        table[f"tibex:{name}"] = canon
    # canonical names resolve to themselves
    for c in CANONICAL_TYPES:
        table.setdefault(c, c)
    return table


NATIVE_TO_CANONICAL: Dict[str, str] = _build()

# ---- lookup indexes -------------------------------------------------------

_LOWER: Dict[str, str] = {k.lower(): v for k, v in NATIVE_TO_CANONICAL.items()}
# Qualified keys (with a dot) take part in prefix and suffix matching, longest first.
# Bare BPEL names never do: "something.invoke" must not become call.process.
_QUALIFIED: List[Tuple[str, str]] = sorted(
    ((k.lower(), v) for k, v in NATIVE_TO_CANONICAL.items() if "." in k and ":" not in k),
    key=lambda kv: len(kv[0]),
    reverse=True,
)
# BW5 class simple names are distinctive, so "<anything>.JDBCUpdateActivity" resolves.
_BW5_SIMPLE: Dict[str, str] = {
    k.rsplit(".", 1)[1].lower(): v for k, v in NATIVE_TO_CANONICAL.items() if k.startswith("com.tibco.")
}
_BPEL_LOCAL: Dict[str, str] = {k.lower(): v for k, v in _BPEL.items()}
_PREFIX_BOUNDARY = ".:/#( "
_SPLIT = re.compile(r"[.:/#]")


def canonical(native: str) -> Optional[str]:
    """Map a native BW5/BW6 type string to a canonical type, or None if unknown.

    Order: exact, case-insensitive exact, namespace-prefixed BPEL name, prefix
    match on a known native type, suffix match on a known native type, BW5 class
    simple name.
    """
    if not native or not isinstance(native, str):
        return None
    s = native.strip()
    if not s:
        return None
    hit = NATIVE_TO_CANONICAL.get(s)
    if hit:
        return hit
    low = s.lower()
    hit = _LOWER.get(low)
    if hit:
        return hit
    if ":" in low and "." not in low.split(":", 1)[0]:
        local = low.rsplit(":", 1)[1]
        hit = _BPEL_LOCAL.get(local)
        if hit:
            return hit
    for key, canon in _QUALIFIED:
        if low.startswith(key) and (len(low) == len(key) or low[len(key)] in _PREFIX_BOUNDARY):
            return canon
    for key, canon in _QUALIFIED:
        if low.endswith("." + key):
            return canon
    simple = _SPLIT.split(low)[-1]
    if "." in low:
        hit = _BW5_SIMPLE.get(simple)
        if hit:
            return hit
    return None


# ---- BW5 groups -----------------------------------------------------------

# BW5 groups appear as <pd:group> with a <pd:groupType>. Values are IR Group.kind.
_GROUP_KINDS: Dict[str, str] = {
    "loop": "loop",
    "repeat": "loop",
    "iterate": "iterate",
    "transaction": "transaction",
    "critical": "critical",
    "none": "scope",
}
GROUP_KIND_TO_CANONICAL: Dict[str, str] = {
    "loop": "group.loop",
    "iterate": "group.iterate",
    "transaction": "group.transaction",
    "critical": "group.critical",
    "parallel": "flow.parallel",
}


def group_kind(native: str) -> Optional[str]:
    """Map a BW5 ``pd:groupType`` value (or a group class name) to an IR Group.kind."""
    if not native or not isinstance(native, str):
        return None
    s = native.strip().lower()
    if s in _GROUP_KINDS:
        return _GROUP_KINDS[s]
    canon = canonical(native)
    if canon:
        for kind, c in GROUP_KIND_TO_CANONICAL.items():
            if c == canon:
                return kind
    return None
