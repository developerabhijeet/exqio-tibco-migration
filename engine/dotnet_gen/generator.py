"""Solution generator (design spec 2.5 step 1): the IR and the design record to a .NET 10 solution.

Deterministic: the same IR, design records, rules, findings and scenarios give byte-identical files
and the same ``build_hash``. Code decides structure and every simple path; a step whose body needs
judgement (functions, arithmetic, untranslated conditions, patterns without a code template) is
emitted with the TIBCO expression kept in a comment and a body that throws
``NotImplementedException("agent fills: <activity>")``; such files carry ``origin="agent"`` so the
Code Builder agent (Task 15) knows what to fill.

Layout (``<App>`` = PascalCase application id)::

    <App>.sln, Directory.Build.props, Directory.Packages.props, nuget.config, .editorconfig
    src/<App>.Core            BCL only: contracts, services, state, mappings, conditions,
                              repository interfaces, IMessageBus, typed exceptions
    src/<App>.Infrastructure  Dapper repositories (SQL verbatim), Service Bus and in-memory bus,
                              JMS consumers, timer jobs, typed HTTP clients, DI registration
    src/<App>.Api             Program.cs (Swagger, Auth:Mode, /health, dev token and bus endpoints
                              in Development), controllers per REST starter, fault handler
    tests/<App>.Tests         rules, branch and scenario tests (``tests_gen``), fixtures, TestDatabase

Paths reserved for the infrastructure generator (Task 16) are never written: ``RESERVED_FOR_INFRA``.

Conventions the generated code follows:

* A service runs its process step by step: each taken TIBCO link queues its target, a step runs
  once, conditional links are evaluated by ``<Process>Conditions`` (simple predicates generated,
  others left to the agent), ``otherwise`` runs when no conditional link of the step was taken.
* Repository SQL is the TIBCO statement verbatim in a raw string literal; the JDBC ``?``
  placeholders are converted at generation time to named Npgsql parameters ``@p0, @p1, ...`` bound
  in order (documented in each method's remarks).
* Simple paths map with ``TibValue.Of(source)``, which converts like XSLT ``value-of`` (string value
  first, then the target property type).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import jinja2
from lxml import etree
from pydantic import BaseModel

from engine.analysis.models import Finding, Rule
from engine.dotnet_gen import csharp_contracts, tests_gen
from engine.dotnet_gen.models import DesignRecord, MappingRow
from engine.ir.models import Activity, Application, Contract, Process, Transition
from engine.parity.models import Scenario

TEMPLATES = Path(__file__).resolve().parent / "templates"
RESERVED_FOR_INFRA: tuple[str, ...] = ("Dockerfile", ".dockerignore", "infra/")
DOCKERFILE_PATH = "Dockerfile"  # written by the infrastructure generator (Task 16)
MANIFEST = ".exiqo/generated-files.txt"

# Central package versions (Directory.Packages.props). Restored on a machine with NuGet access or
# from the offline cache produced by `make nuget-cache`.
PACKAGES: tuple[tuple[str, str], ...] = (
    ("Azure.Extensions.AspNetCore.Configuration.Secrets", "1.3.2"),
    ("Azure.Identity", "1.13.2"),
    ("Azure.Messaging.ServiceBus", "7.18.2"),
    ("Dapper", "2.1.66"),
    ("Microsoft.AspNetCore.Authentication.JwtBearer", "10.0.0"),
    ("Microsoft.AspNetCore.Mvc.Testing", "10.0.0"),
    ("Microsoft.Extensions.Http.Resilience", "9.0.0"),
    ("Microsoft.NET.Test.Sdk", "17.12.0"),
    ("Npgsql", "9.0.3"),
    ("Swashbuckle.AspNetCore", "7.2.0"),
    ("Testcontainers.PostgreSql", "4.3.0"),
    ("coverlet.collector", "6.0.4"),
    ("xunit", "2.9.3"),
    ("xunit.runner.visualstudio", "3.0.2"),
)

Origin = Literal["code", "agent"]


class GenFile(BaseModel):
    path: str  # relative to the solution root, POSIX separators
    content: str
    origin: Origin = "code"
    activity_id: str | None = None
    process_id: str | None = None  # the process a per-process file belongs to; None for shared files


class GeneratedSolution(BaseModel):
    root: Path
    name: str
    files: list[GenFile]
    build_hash: str


# =============================================================== C# text helpers
_JINJA = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True, trim_blocks=True,
    lstrip_blocks=True, undefined=jinja2.StrictUndefined, autoescape=False)


def render(template: str, **ctx: Any) -> str:
    return _JINJA.get_template(template).render(**ctx)


pascal = csharp_contracts.pascal

_CS_KEYWORDS = frozenset((  # noqa: SIM905 - one readable string
    "abstract as base bool break byte case catch char checked class const continue decimal default "
    "delegate do double else enum event explicit extern false finally fixed float for foreach goto if "
    "implicit in int interface internal is lock long namespace new null object operator out override "
    "params private protected public readonly ref return sbyte sealed short sizeof stackalloc static "
    "string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual "
    "void volatile while").split())


def ident(name: str, fallback: str = "Step") -> str:
    """PascalCase C# identifier (``get`` -> ``Get``); a leading digit gets ``fallback`` in front."""
    p = pascal(name or "")
    if not p:
        return fallback
    return f"{fallback}{p}" if p[0].isdigit() else p


def camel(name: str) -> str:
    """camelCase C# identifier: ``GMFFundingDB`` -> ``gmfFundingDB``, ``MsgType`` -> ``msgType``."""
    p = ident(name, "Value")
    m = re.match(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]+", p)
    head = m.group(0) if m else p[:1]
    out = head.lower() + p[len(head):]
    return f"@{out}" if out in _CS_KEYWORDS else out


def cs_string(text: str) -> str:
    """A regular C# string literal."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def xml_doc(text: str) -> str:
    """Text safe inside an XML doc comment, on one line."""
    return " ".join(str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").split())


def one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def slug(text: str, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return "_".join(words[:max_words])


def method_name(prefix: str, text: str, limit: int = 80, lower: bool = False) -> str:
    """``prefix_full_words_of_text`` cut at a word boundary so the identifier has at most ``limit`` chars."""
    name = prefix
    for word in re.findall(r"[A-Za-z0-9]+", text.lower() if lower else text):
        cand = f"{name}_{word}"
        if len(cand) > limit:
            break
        name = cand
    return name


def raw_string_literal(text: str, indent: int) -> str:
    """A C# raw string literal holding ``text`` exactly (newlines normalised to LF).

    The literal starts at the opening quotes; content lines and the closing quotes are indented by
    ``indent + 4`` spaces, which the compiler strips, so the value is ``text`` byte for byte.
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    quotes = '"""'
    while quotes in body:
        quotes += '"'
    pad = " " * (indent + 4)
    lines = [(pad + line) if line else "" for line in body.split("\n")]
    return f"{quotes}\n" + "\n".join(lines) + f"\n{pad}{quotes}"


_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\u0080-￿][\w\u0080-￿]*)?\$")


def _skip_quoted(sql: str, i: int, quote: str, backslash: bool) -> int:
    j, n = i + 1, len(sql)
    while j < n:
        c = sql[j]
        if backslash and c == "\\":
            j += 2
            continue
        if c == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def to_named_parameters(sql: str) -> str:
    """Convert JDBC ``?`` placeholders to named Npgsql parameters ``@p0, @p1, ...`` in order.

    Left untouched: quoted literals (including ``E'...'``), quoted identifiers, ``--`` and nested
    ``/* */`` comments, dollar-quoted strings and the jsonb operators ``?|`` and ``?&``. The PgJDBC
    escape ``??`` becomes the single ``?`` operator Npgsql expects. Everything else is kept byte for
    byte, so (without ``??``) replacing ``@pN`` back with ``?`` gives the TIBCO statement.
    """
    out: list[str] = []
    i, n, k = 0, len(sql), 0
    while i < n:
        c = sql[i]
        if c == "'":
            prev = sql[i - 1] if i else ""
            before = sql[i - 2] if i > 1 else ""
            escape = prev in "eE" and not (before.isalnum() or before == "_")
            j = _skip_quoted(sql, i, "'", escape)
        elif c == '"':
            j = _skip_quoted(sql, i, '"', False)
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
        elif sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
        elif c == "$" and (m := _DOLLAR_TAG.match(sql, i)) and (not i or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            close = sql.find(m.group(0), m.end())
            j = n if close == -1 else close + len(m.group(0))
        elif c == "?":
            nxt = sql[i + 1:i + 3]
            if nxt[:1] == "?":  # PgJDBC escape: ?? is a literal ? operator, not a placeholder
                out.append("?")
                i += 2
                continue
            if nxt[:1] in ("|", "&") and nxt[1:2] != nxt[:1]:
                out.append("?" + nxt[0])
                i += 2
                continue
            out.append(f"@p{k}")
            k += 1
            i += 1
            continue
        else:
            out.append(c)
            i += 1
            continue
        out.append(sql[i:j])
        i = j
    return "".join(out)


def placeholder_count(sql: str) -> int:
    return len(re.findall(r"@p\d+", to_named_parameters(sql))) - len(re.findall(r"@p\d+", sql))


def tibco_comment(target: str, expression: str, pad: str) -> list[str]:
    """``// TIBCO: target = expression`` (continuation lines of a multi-line expression indented)."""
    lines = (expression or "").replace("\r\n", "\n").split("\n")
    out = [f"{pad}// TIBCO: {target} = {lines[0].rstrip()}".rstrip()]
    out += [f"{pad}//     {ln.rstrip()}".rstrip() for ln in lines[1:]]
    return out


# ================================================================== type tables
SCALARS = frozenset({"string", "int", "long", "ulong", "decimal", "double", "float", "bool", "DateOnly",
                     "DateTime", "TimeOnly"})
VALUE_TYPES = SCALARS - {"string"}

_SQL_PARAM = {"VARCHAR": "string", "CHAR": "string", "LONGVARCHAR": "string", "NVARCHAR": "string",
              "NCHAR": "string", "CLOB": "string", "NUMERIC": "decimal", "DECIMAL": "decimal",
              "INTEGER": "int", "SMALLINT": "int", "TINYINT": "int", "BIGINT": "long", "DOUBLE": "double",
              "FLOAT": "double", "REAL": "float", "BIT": "bool", "BOOLEAN": "bool", "DATE": "DateTime",
              "TIMESTAMP": "DateTime", "TIME": "TimeOnly"}
_COLUMN_TYPE = {"int2": "int", "int4": "int", "integer": "int", "serial": "int", "smallint": "int",
                "int8": "long", "bigint": "long", "bigserial": "long", "numeric": "decimal",
                "decimal": "decimal", "money": "decimal", "float4": "float", "real": "float",
                "float8": "double", "double precision": "double", "bool": "bool", "boolean": "bool",
                "date": "DateTime", "timestamp": "DateTime", "timestamptz": "DateTime",
                "varchar": "string", "text": "string", "bpchar": "string", "char": "string",
                "uuid": "Guid", "json": "string", "jsonb": "string", "time": "TimeSpan"}
_JDBC_TYPE = {"4": "int", "5": "int", "-6": "int", "-5": "long", "2": "decimal", "3": "decimal",
              "6": "double", "8": "double", "7": "float", "12": "string", "1": "string", "-1": "string",
              "-9": "string", "16": "bool", "-7": "bool", "91": "DateTime", "93": "DateTime"}
_TIME_COLUMN = {"timestamp", "timestamptz", "date", "time", "timetz"}
_REST_PARAM = {"integer": "int", "int": "int", "long": "long", "number": "decimal", "decimal": "decimal",
               "boolean": "bool", "bool": "bool"}


# Field names masked in ToString(), logs and ProblemDetails (normalised: lower case, no _ or -).
SENSITIVE_NAMES = frozenset({"ssn", "socialsecuritynumber", "taxid", "tin", "accountnumber", "acctnumber",
                             "accountno", "cardnumber", "creditcardnumber", "cardno", "pan", "routingnumber",
                             "iban", "bankaccountnumber"})


def is_sensitive_name(name: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", name.lower()) in SENSITIVE_NAMES


# ============================================================== record shapes
@dataclass
class PropShape:
    wire: str  # element name on the wire (``@x`` for attributes)
    cs: str
    cs_type: str  # item type
    required: bool = False
    repeated: bool = False
    child: RecShape | None = None
    enum: bool = False
    location: str = ""  # REST parameters: path, query or header
    nullable: bool | None = None  # None: nullable when not required (contract records)

    @property
    def is_nullable(self) -> bool:
        return self.nullable if self.nullable is not None else not self.required

    @property
    def sensitive(self) -> bool:
        return is_sensitive_name(self.cs)


@dataclass
class RecShape:
    name: str  # C# type as written in the generated file
    namespace: str
    props: list[PropShape] = field(default_factory=list)

    def prop(self, local: str) -> PropShape | None:
        for p in self.props:
            if p.wire == local:
                return p
        for p in self.props:
            if p.wire.lower() == local.lower():
                return p
        return None


def _shape_from_model(model: Any, name: str, namespace: str, memo: dict[str, RecShape]) -> RecShape:
    if name in memo:
        return memo[name]
    rec = model.records[name]
    shape = RecShape(name=name, namespace=namespace)
    memo[name] = shape
    for p in rec.props:
        child = _shape_from_model(model, p.child, namespace, memo) if p.child and p.child in model.records else None
        shape.props.append(PropShape(wire=p.path_part or p.wire_name, cs=p.cs_name, cs_type=p.cs_type,
                                     required=p.required, repeated=p.repeated, child=child,
                                     enum=p.cs_type in model.enums))
    return shape


def _contract_model(contract: Contract, reserved: frozenset[str]) -> Any:
    """The record model ``to_records`` renders (same package, so the private builder is shared)."""
    try:
        return csharp_contracts._model(contract, reserved)
    except (ValueError, KeyError, TypeError, etree.XMLSyntaxError):
        return None


class ContractIndex:
    """Records of the contracts a process uses, by root element path (``/FundingRequest``)."""

    def __init__(self, app_name: str, app: Application, contract_ids: list[str]) -> None:
        self.plans = csharp_contracts.plan_contracts(app_name, app.contracts)
        self.by_id: dict[str, Contract] = {c.id: c for c in app.contracts}
        self.roots: dict[str, RecShape] = {}
        self.namespaces: set[str] = set()
        for cid in contract_ids:
            contract, plan = self.by_id.get(cid), self.plans.get(cid)
            if contract is None or plan is None:
                continue
            model = _contract_model(contract, plan.reserved)
            if model is None:  # an unparsable schema yields no records (reported by Task 13's plan)
                continue
            memo: dict[str, RecShape] = {}
            for rec_name, path in model.paths().items():
                shape = _shape_from_model(model, rec_name, plan.namespace, memo)
                root = path.strip("/")
                if root and "/" not in root:
                    self.roots.setdefault(root, shape)
                    self.roots.setdefault(root.lower(), shape)

    def root(self, element: str) -> RecShape | None:
        return self.roots.get(element) or self.roots.get(element.lower())


# =============================================================== process context
_PATH = re.compile(r"^\$([\w.\-]+)((?:/@?(?:[\w.\-]+:)?[\w.\-]+(?:\[\d+\])?)*)$")
_STEP = re.compile(r"^(@?)(?:[\w.\-]+:)?([\w.\-]+)(?:\[(\d+)\])?$")
_STRING_LIT = re.compile(r"""^("([^"]*)"|'([^']*)')$""")
_NUMBER_LIT = re.compile(r"^-?\d+(\.\d+)?$")
_HREF_ROOT = re.compile(r"#//([^;]+)")
_API_KINDS = ("rest", "http", "soap")
_NUM_SUFFIX = {"int": "", "long": "L", "ulong": "UL", "decimal": "m", "double": "d", "float": "f"}
_AS = {"int": "AsInt", "long": "AsLong", "ulong": "AsULong", "decimal": "AsDecimal", "double": "AsDouble",
       "float": "AsFloat", "bool": "AsBool", "DateTime": "AsDateTime", "DateOnly": "AsDateOnly",
       "TimeOnly": "AsTimeOnly"}
_NUMERIC = frozenset({"int", "long", "ulong", "decimal", "double", "float"})


def _element(index: int) -> str:
    return "FirstOrDefault()" if index == 0 else f"ElementAtOrDefault({index})"


def _steps(path: str) -> list[tuple[str, int | None]] | None:
    out: list[tuple[str, int | None]] = []
    for part in [p for p in path.split("/") if p]:
        m = _STEP.match(part)
        if not m:
            return None
        out.append((m.group(1) + m.group(2), int(m.group(3)) if m.group(3) else None))
    return out


def _local(segment: str) -> str:
    seg = segment.split("[", 1)[0]
    if ":" in seg:
        seg = seg.split(":", 1)[1]
    return seg


@dataclass
class Resolved:
    expr: str
    kind: Literal["scalar", "list", "record"]
    cs_type: str = "string"
    nullable: bool = True


@dataclass
class MapTarget:
    kind: str  # jdbc | error | log | record | values | json
    type_name: str
    shape: RecShape | None
    strip: int  # leading target segments to drop (root element, ActivityInput/Body/Root, ...)
    reason: str = ""


class ProcGen:
    """Everything the generator derives for one process."""

    def __init__(self, sol: SolutionGen, process: Process, design: DesignRecord, rules: list[Rule],
                 findings: list[Finding], scenarios: list[Scenario]) -> None:
        self.sol = sol
        self.p = process
        self.design = design
        self.rules = rules
        self.findings = findings
        self.scenarios = scenarios
        self.app = sol.app_name
        self.name = ident(process.name, "Process")
        self.acts = {a.id: a for a in process.activities}
        self.groups = {g.id: g for g in process.groups}
        self.outs: dict[str, list[Transition]] = defaultdict(list)
        for t in process.transitions:
            self.outs[t.from_id].append(t)
        self.starter = process.starter
        self.starter_act = self.acts.get(process.starter.activity_id) if process.starter else None
        self.kind = process.starter.kind if process.starter else "subprocess"
        self.contracts = ContractIndex(self.app, sol.application, [c.contract_id for c in design.contracts])
        self.finding_decision = {f.id: f.decision for f in findings}
        rows_by_act: dict[str, list[MappingRow]] = defaultdict(list)
        for r in design.mapping:
            rows_by_act[r.activity_id].append(r)
        self.primary: dict[str, MappingRow] = {}
        self.map_row: dict[str, MappingRow] = {}
        for aid, rows in rows_by_act.items():
            for r in rows:
                if r.pattern == "input.mapping" or (r.pattern in ("mapper", "assign") and aid in self.acts):
                    self.map_row.setdefault(aid, r)
                if r.pattern != "input.mapping":
                    self.primary.setdefault(aid, r)
        # state property names, one per activity output kept in the state
        self.state_props: dict[str, str] = {}
        used = {"Input", "LastError", f"{self.name}State"}
        for a in process.activities:
            base = ident(a.name or a.id)
            name, n = base, 2
            while name in used:
                name, n = f"{base}{n}", n + 1
            used.add(name)
            self.state_props[a.id] = name
        self.targets: dict[str, MapTarget] = {}
        self.uses_json_nodes = False
        self.parameters = self._rest_parameters()
        self.body_shape = self._body_shape()
        self.message_shape = self._message_shape()
        self.connections: dict[str, str] = {}  # activity id -> connection name
        for a in process.activities:
            if a.type.startswith("jdbc."):
                self.connections[a.id] = sol.connection_name(a)

    # ------------------------------------------------------------ naming
    def ns(self, suffix: str) -> str:
        return f"{self.app}.{suffix}"

    def type_prefix(self, act: Activity) -> str:
        """Row and input records are prefixed with the process when two processes share a step name."""
        base = ident(act.name or act.id)
        return f"{self.name}{base}" if base in self.sol.shared_step_names else base

    def row_type(self, act: Activity) -> str:
        return f"{self.type_prefix(act)}Row"

    def input_type(self, act: Activity) -> str:
        return f"{self.type_prefix(act)}Input"

    def remarks(self, act_id: str, row: MappingRow | None) -> str:
        rules = ", ".join(row.rule_ids) if row and row.rule_ids else "none"
        parts = []
        for fid in (row.finding_ids if row else []):
            label = {"keep": "kept", "fix": "fixed", "ask": "asked"}.get(self.finding_decision.get(fid) or "", "undecided")
            parts.append(f"{fid} ({label})")
        findings = ", ".join(parts) if parts else "none"
        act = self.acts.get(act_id)
        name = act.name if act else act_id
        line = f" line {act.source_line}" if act and act.source_line else ""
        return f"Rules: {rules}. Findings: {findings}. TIBCO: {name}{line}"

    # ------------------------------------------------------------ starter shapes
    def _rest_parameters(self) -> list[PropShape]:
        if self.kind not in ("rest",) or self.starter_act is None:
            return []
        path = str(self.starter.config.get("path") or "")
        in_path = set(re.findall(r"\{([^}/]+)\}", path))
        card = self.sol.io_cards.get(self.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        params: list[PropShape] = []
        children = req.get("children", []) if isinstance(req, dict) and req.get("name") == "parameters" else []
        for ch in children:
            wire = str(ch.get("name") or "")
            if not wire:
                continue
            loc = ch.get("location") or ("path" if wire in in_path else "query")
            params.append(PropShape(wire=wire, cs=ident(wire, "Param"),
                                    cs_type=_REST_PARAM.get(str(ch.get("type") or "").lower(), "string"),
                                    required=bool(ch.get("required")) or loc == "path", location=str(loc)))
        for name in sorted(in_path - {p.wire for p in params}):
            params.append(PropShape(wire=name, cs=ident(name, "Param"), cs_type="string", required=True,
                                    location="path"))
        return params

    def _body_shape(self) -> RecShape | None:
        if self.kind != "rest" or self.starter_act is None:
            return None
        verb = str(self.starter.config.get("verb") or "POST").upper()
        if verb in ("GET", "DELETE", "HEAD"):
            return None
        card = self.sol.io_cards.get(self.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        name = req.get("name") if isinstance(req, dict) else None
        if name and name != "parameters":
            return self.contracts.root(str(name))
        return None

    def _message_shape(self) -> RecShape | None:
        if self.kind != "jms" or self.starter_act is None:
            return None
        settings = self.starter_act.config.get("settings") or {}
        for ref in settings.get("bodyEditorElement") or [] if isinstance(settings, dict) else []:
            m = _HREF_ROOT.search(str(ref.get("href") if isinstance(ref, dict) else ref))
            if m and (shape := self.contracts.root(m.group(1))):
                return shape
        return None

    def message_root(self) -> str | None:
        settings = (self.starter_act.config.get("settings") or {}) if self.starter_act else {}
        for ref in settings.get("bodyEditorElement") or [] if isinstance(settings, dict) else []:
            m = _HREF_ROOT.search(str(ref.get("href") if isinstance(ref, dict) else ref))
            if m:
                return m.group(1)
        return None

    def message_type(self) -> str:
        return self.message_shape.name if self.message_shape else "string"

    # ------------------------------------------------------------ path resolution
    def _var_matches(self, var: str, act: Activity) -> bool:
        return var in (act.name, act.name.replace(" ", "-"), act.id)

    def resolve(self, expr: str) -> Resolved | None:
        m = _PATH.match((expr or "").strip())
        if not m:
            return None
        var, steps = m.group(1), _steps(m.group(2))
        if steps is None:
            return None
        sn = self.starter_act.name if self.starter_act else None
        if sn and self.kind == "rest" and var == f"{sn}.parameters":
            shape = RecShape(name=f"{self.name}Parameters", namespace="", props=self.parameters)
            return self._walk("state.Input.Parameters", False, shape, steps)
        if sn and self.kind == "rest" and self.body_shape is not None:
            if var == f"{sn}.item":
                return self._walk("state.Input.Body", True, self.body_shape, steps)
            if var == sn and steps and steps[0][0] == "item":
                return self._walk("state.Input.Body", True, self.body_shape, steps[1:])
        if sn and self.kind == "jms" and var == sn and self.message_shape is not None:
            root = self.message_root()
            if len(steps) >= 2 and steps[0][0] == "Body" and root and steps[1][0] == root:
                return self._walk("state.Input.Message", False, self.message_shape, steps[2:])
        for act in self.p.activities:
            if not self._var_matches(var, act):
                continue
            prop = self.state_props[act.id]
            if act.type == "jdbc.query":
                if not steps or steps[0][0] != "Record":
                    return None
                if len(steps) == 1 and steps[0][1] is None:
                    return Resolved(f"state.{prop}", "list", self.row_type(act))
                if len(steps) != 2 or steps[1][1] is not None:
                    return None
                columns = {c: (n, t) for c, n, t in self.columns(act)}
                col = steps[1][0]
                hit = columns.get(col) or next((v for k, v in columns.items() if k.lower() == col.lower()), None)
                if hit is None:
                    return None
                index = (steps[0][1] or 1) - 1
                return Resolved(f"state.{prop}.{_element(index)}?.{hit[0]}", "scalar", hit[1], True)
            if act.type in ("mapper", "assign"):
                target = self.target(act)
                if target.shape is None or target.kind != "record" or self.map_origin(act) != "code":
                    return None
                return self._walk(f"state.{prop}", True, target.shape, steps)
            return None
        return None

    def _walk(self, base: str, nullable: bool, shape: RecShape, steps: list[tuple[str, int | None]]) -> Resolved | None:
        expr, current = base, shape
        kind: Literal["scalar", "list", "record"] = "record"
        cs_type = shape.name
        for name, index in steps:
            if current is None:
                return None
            prop = current.prop(name)
            if prop is None:
                return None
            expr += f"{'?.' if nullable else '.'}{prop.cs}"
            if prop.repeated:
                expr += f".{_element((index or 1) - 1)}"
                nullable = True
            elif index not in (None, 1):
                return None
            else:
                nullable = nullable or prop.is_nullable
            current = prop.child
            kind = "record" if prop.child else "scalar"
            cs_type = prop.cs_type
            if prop.enum:
                cs_type = "enum"
        return Resolved(expr, kind, cs_type, nullable)

    def assign(self, expr: str, target_type: str, target_nullable: bool, field_name: str) -> str | None:
        """C# for ``target = expr`` (a simple path or literal); ``None`` when the agent must write it.

        Same type: a direct assignment. Different types: ``TibValue.Of(source).AsX()``, which converts
        like XSLT ``value-of`` and returns null instead of throwing; a non-nullable target then fails
        with ``TibValue.Missing(field)``, visibly, rather than taking a silent default.
        """
        e = (expr or "").strip()
        m = _STRING_LIT.match(e)
        literal = (m.group(2) if m.group(2) is not None else m.group(3)) if m else (e if _NUMBER_LIT.match(e) else None)
        if literal is not None:
            if target_type == "string":
                return cs_string(literal)
            if target_type in _NUM_SUFFIX and _NUMBER_LIT.match(literal):
                if target_type in ("int", "long", "ulong") and "." in literal:
                    return None
                return literal + _NUM_SUFFIX[target_type]
            if target_type == "bool" and literal in ("true", "false", "1", "0"):
                return "true" if literal in ("true", "1") else "false"
            src, src_type, src_nullable = cs_string(literal), "string", False
        else:
            r = self.resolve(e)
            if r is None or r.kind != "scalar":
                return None
            src, src_type, src_nullable = r.expr, r.cs_type, r.nullable
        missing = f"TibValue.Missing({cs_string(field_name)})"
        if src_type == target_type:
            if target_nullable or not src_nullable:
                return src
            return f"{src} ?? string.Empty" if target_type == "string" else f"{src} ?? throw {missing}"
        if target_type == "string":
            return f"TibValue.Of({src}).AsString()"
        conv = _AS.get(target_type)
        if conv is None:
            return None
        out = f"TibValue.Of({src}).{conv}()"
        return out if target_nullable else f"{out} ?? throw {missing}"

    # ------------------------------------------------------------ jdbc
    def columns(self, act: Activity) -> list[tuple[str, str, str]]:
        """(column, property, C# type) from the column metadata of a JDBC query."""
        settings = act.config.get("settings") if isinstance(act.config.get("settings"), dict) else {}
        meta = settings.get("columnMetadata") or []
        out: list[tuple[str, str, str]] = []
        used: set[str] = set()
        for col in meta if isinstance(meta, list) else []:
            name = str(col.get("columnName") or "")
            if not name:
                continue
            cs_type = _COLUMN_TYPE.get(str(col.get("typeName") or "").lower()) or \
                _JDBC_TYPE.get(str(col.get("dataType") or ""), "string")
            prop = ident(name, "Column")
            base, n = prop, 2
            while prop in used or prop == self.row_type(act):
                prop, n = f"{base}{n}", n + 1
            used.add(prop)
            out.append((name, prop, cs_type))
        return out

    def time_columns(self, act: Activity) -> set[str]:
        settings = act.config.get("settings") if isinstance(act.config.get("settings"), dict) else {}
        out = set()
        for col in settings.get("columnMetadata") or []:
            if str(col.get("typeName") or "").lower() in _TIME_COLUMN or str(col.get("dataType")) in ("91", "92", "93"):
                out.add(str(col.get("columnName")))
        return out

    def sql_params(self, act: Activity) -> list[tuple[str, str, str]]:
        """(parameter name, property, C# type) in placeholder order."""
        sql = str(act.config.get("sql") or "")
        count = placeholder_count(sql)
        declared = act.config.get("parameters") or []
        out: list[tuple[str, str, str]] = []
        used: set[str] = set()
        for i in range(max(count, len(declared))):
            d = declared[i] if i < len(declared) and isinstance(declared[i], dict) else {}
            name = str(d.get("name") or f"param{i + 1}")
            prop = ident(name, "Param")
            base, n = prop, 2
            while prop in used:
                prop, n = f"{base}{n}", n + 1
            used.add(prop)
            out.append((name, prop, _SQL_PARAM.get(str(d.get("type") or "").upper(), "string")))
        return out

    # ------------------------------------------------------------ mapping targets
    def target(self, act: Activity) -> MapTarget:
        if act.id in self.targets:
            return self.targets[act.id]
        t = self._target(act)
        self.targets[act.id] = t
        if t.kind == "json":
            self.uses_json_nodes = True
        return t

    def _target(self, act: Activity) -> MapTarget:
        bindings = act.input_bindings
        segs = [[_local(s) for s in b.target.split("/") if s] for b in bindings]
        roots = {s[0] for s in segs if s}
        if act.type.startswith("jdbc."):
            props = [PropShape(wire=name, cs=prop, cs_type=cs_type, nullable=True)
                     for name, prop, cs_type in self.sql_params(act)]
            return MapTarget("jdbc", self.input_type(act), RecShape(self.input_type(act), self.ns("Core.Data"), props), 1)
        if act.type == "reply" and roots and roots <= {"client4XXError", "server5XXError"}:
            props = [PropShape("statusCode", "StatusCode", "int", nullable=False),
                     PropShape("message", "Message", "string", nullable=True)]
            return MapTarget("error", "ErrorReply", RecShape("ErrorReply", self.ns("Core.Services"), props), 1)
        if act.type == "log":
            props = [PropShape(w, ident(w), "string", nullable=True)
                     for w in ("message", "msgCode", "loggerName", "logLevel")]
            return MapTarget("log", "StepLogEntry", RecShape("StepLogEntry", self.ns("Core.Diagnostics"), props), 1)
        if act.type in ("jms.send", "jms.publish", "jms.reply", "jms.request_reply"):
            bodies = {s[2] for s in segs if len(s) >= 3 and s[0] == "ActivityInput" and s[1] == "Body"}
            shape = self.contracts.root(next(iter(bodies))) if len(bodies) == 1 else None
            if shape is None:
                href = _HREF_ROOT.search(json.dumps(act.config.get("settings") or {}))
                shape = self.contracts.root(href.group(1)) if href else None
            if shape is not None:
                return MapTarget("record", shape.name, shape, 3)
            return MapTarget("json", "JsonObject", None, 0, "message body element not found in the contracts")
        if len(roots) == 1:
            shape = self.contracts.root(next(iter(roots)))
            if shape is not None:
                return MapTarget("record", shape.name, shape, 1)
        if act.type == "throw" and act.config.get("fault_name"):
            shape = self.contracts.root(str(act.config["fault_name"]))
            if shape is not None:
                return MapTarget("record", shape.name, shape, 1)
        return MapTarget("json", "JsonObject", None, 0, "target element not found in the process contracts")

    def map_origin(self, act: Activity) -> Origin:
        """Origin of the mapping method actually generated (the design's, or agent when code cannot)."""
        row = self.map_row.get(act.id)
        if row is None:
            return "code"
        if row.origin == "agent":
            return "agent"
        return "code" if self.initializer(act, "") is not None else "agent"

    def initializer(self, act: Activity, pad: str) -> list[str] | None:
        """``new T { ... }`` lines for a code mapping, or ``None`` when code cannot generate it."""
        t = self.target(act)
        if t.shape is None:
            return None
        items: list[tuple[list[str], Any]] = []
        for b in act.input_bindings:
            segs = [_local(s) for s in b.target.split("/") if s]
            if len(segs) <= t.strip:
                return None
            if t.kind == "record" and t.strip == 3 and segs[:2] != ["ActivityInput", "Body"]:
                return None
            items.append((segs[t.strip:], b))
        return self._init(t.shape, items, pad)

    def _init(self, shape: RecShape, items: list[tuple[list[str], Any]], pad: str) -> list[str] | None:
        groups: dict[str, list[tuple[list[str], Any]]] = defaultdict(list)
        for segs, b in items:
            groups[segs[0]].append((segs[1:], b))
        lines = [f"new {shape.name}", f"{pad}{{"]
        matched: set[str] = set()
        inner = pad + "    "
        for prop in shape.props:
            key = next((k for k in groups if k == prop.wire or k.lower() == prop.wire.lower()), None)
            if key is None:
                if prop.required:
                    default = "default" if (prop.cs_type in VALUE_TYPES or prop.enum) and not prop.repeated else "default!"
                    lines.append(f"{inner}{prop.cs} = {default}, // not mapped in TIBCO")
                continue
            matched.add(key)
            its = groups[key]
            if prop.repeated:
                return None
            if len(its) == 1 and not its[0][0]:
                b = its[0][1]
                if prop.child is not None or prop.enum or prop.cs_type not in SCALARS:
                    return None
                expr = self.assign(b.expression, prop.cs_type, prop.is_nullable, prop.wire)
                if expr is None:
                    return None
                lines += tibco_comment(b.target, b.expression, inner)
                lines.append(f"{inner}{prop.cs} = {expr},")
                continue
            if prop.child is None or any(not segs for segs, _ in its):
                return None
            sub = self._init(prop.child, its, inner)
            if sub is None:
                return None
            lines.append(f"{inner}{prop.cs} = {sub[0]}")
            lines += sub[1:-1]
            lines.append(f"{sub[-1]},")
        if set(groups) - matched:
            return None
        lines.append(f"{pad}}}")
        return lines


# ================================================================ solution context
class SolutionGen:
    def __init__(self, app: Application, process_ids: list[str], designs: dict[str, DesignRecord],
                 io_cards: dict[str, Any]) -> None:
        self.application = app
        first = designs[process_ids[0]]
        self.app_name = first.solution.name
        self.io_cards = io_cards
        procs = {p.id: p for p in app.processes}
        counts: dict[str, int] = defaultdict(int)
        for pid in process_ids:
            for a in procs[pid].activities:
                counts[ident(a.name or a.id)] += 1
        self.shared_step_names = {k for k, v in counts.items() if v > 1}
        self.resources = {r.id: r for r in app.resources}

    def connection_name(self, act: Activity) -> str:
        ref = str(act.config.get("resource_ref") or "")
        res = self.resources.get(ref)
        if res is None:
            hits = [r for r in self.resources.values() if r.id.endswith("." + ref) or r.id.endswith("/" + ref)]
            res = hits[0] if len(hits) == 1 else None
        raw = res.name if res else (ref.rsplit(".", 1)[-1] or "Default")
        return ident(raw, "Resource")


# ===================================================================== C# files
_RECORD_START = re.compile(r"^public sealed record (\w+)$")
_PROPERTY = re.compile(r"^    public (?:required )?\S+ (\w+) \{ get; init; \}$")
TO_STRING = "public override string ToString() => Masking.ToMaskedString(this);"


def _masked(text: str, app: str) -> str:
    """``mask_records`` for a generated file outside the Contracts namespace (adds the using)."""
    masked = mask_records(text)
    if "[Sensitive]" not in masked:
        return masked
    using = f"using {app}.Core.Contracts;"
    lines = masked.split("\n")
    usings = [ln for ln in lines if ln.startswith("using ")]
    rest = lines[len(usings):] if usings else lines
    if not usings:
        rest = ["", *rest]
    merged = sorted({*usings, using}, key=lambda u: (not u.startswith("using System"), u))
    return "\n".join(merged + rest)


def mask_records(text: str) -> str:
    """Mark sensitive properties of rendered records ``[Sensitive]`` and give those records a masking
    ``ToString()``, so an SSN or account number never reaches a log in clear text."""
    lines = text.split("\n")
    out: list[str] = []
    in_record, sensitive, attr_start = False, False, None
    for line in lines:
        if _RECORD_START.match(line):
            in_record, sensitive, attr_start = True, False, None
        elif in_record and line.startswith("    [") and attr_start is None:
            attr_start = len(out)
        elif in_record and (m := _PROPERTY.match(line)):
            if is_sensitive_name(m.group(1)):
                at = attr_start if attr_start is not None else len(out)
                out.insert(at, "    [Sensitive]")
                sensitive = True
            attr_start = None
        elif in_record and line == "}":
            if sensitive:
                out += ["", "    /// <summary>Prints the record with its sensitive fields masked.</summary>", f"    {TO_STRING}"]
            in_record = False
        elif in_record and not line.strip():
            attr_start = None
        out.append(line)
    return "\n".join(out)

def _usings(*names: str) -> str:
    system = sorted({n for n in names if n and (n == "System" or n.startswith("System."))})
    other = sorted({n for n in names if n and not (n == "System" or n.startswith("System."))})
    return "".join(f"using {n};\n" for n in system + other)


def _contract_usings(pg: ProcGen, *shapes: RecShape | None) -> list[str]:
    return [s.namespace for s in shapes if s is not None and s.namespace]


def gen_state(pg: ProcGen) -> str:
    lines: list[str] = []
    usings = ["System.Text.Json.Serialization"] if pg.kind == "rest" else []
    if pg.kind == "jms":
        input_doc = f"the JMS message received on {pg.starter.config.get('destination') or 'the queue'}."
        sig = f"{pg.message_type()} Message, IReadOnlyDictionary<string, string>? Properties = null"
        usings += _contract_usings(pg, pg.message_shape)
    elif pg.kind == "rest":
        input_doc = f"the REST request {str(pg.starter.config.get('verb') or '').upper()} {pg.starter.config.get('path') or ''}."
        sig = f"{pg.name}Parameters Parameters"
        if pg.body_shape is not None:
            sig += f", {pg.body_shape.name}? Body = null"
            usings += _contract_usings(pg, pg.body_shape)
    elif pg.kind in ("http", "soap"):
        input_doc = f"the {pg.kind.upper()} request received by {pg.starter_act.name if pg.starter_act else 'the starter'}."
        sig = "JsonElement? Body = null"
        usings.append("System.Text.Json")
    elif pg.kind == "timer":
        input_doc = "the timer firing."
        sig = "DateTimeOffset FiredAt"
    else:
        input_doc = "the subprocess Start input (shape decided by the builder)."
        sig = "JsonObject? Start = null"
        usings.append("System.Text.Json.Nodes")
    has_jdbc = any(a.type.startswith("jdbc.") for a in pg.p.activities)
    if has_jdbc:
        usings.append(pg.ns("Core.Data"))
    props: list[tuple[str, str, str, str]] = []  # (doc, type, name, init)
    for a in pg.p.activities:
        name = pg.state_props[a.id]
        if a.type == "jdbc.query":
            props.append((f"Rows returned by {a.name} (jdbc.query).", f"IReadOnlyList<{pg.row_type(a)}>", name, " = [];"))
        elif a.type in ("jdbc.update", "jdbc.call"):
            props.append((f"Rows affected by {a.name} ({a.type}).", "int", name, ""))
        elif a.type in ("mapper", "assign"):
            t = pg.target(a)
            if t.kind == "json":
                usings.append("System.Text.Json.Nodes")
            usings += _contract_usings(pg, t.shape)
            props.append((f"Output of {a.name} ({a.type}).", f"{t.type_name}?", name, ""))
    lines.append(_usings(*usings).rstrip("\n"))
    lines.append("")
    lines.append(f"namespace {pg.ns('Core.Services')};")
    lines.append("")
    lines.append(f"/// <summary>What starts {pg.name}: {xml_doc(input_doc)}</summary>")
    lines.append(f"public sealed record {pg.name}Input({sig});")
    if pg.kind == "rest":
        lines.append("")
        lines.append(f"/// <summary>Path, query and header parameters of {xml_doc(pg.p.name)}.</summary>")
        lines.append(f"public sealed record {pg.name}Parameters")
        lines.append("{")
        for i, prm in enumerate(pg.parameters):
            if i:
                lines.append("")
            loc = prm.location or "query"
            lines.append(f"    /// <summary>{xml_doc(loc)} parameter {xml_doc(prm.wire)}.</summary>")
            lines.append(f"    [JsonPropertyName({cs_string(prm.wire)})]")
            req = "required " if prm.required else ""
            nul = "" if prm.required else "?"
            lines.append(f"    public {req}{prm.cs_type}{nul} {prm.cs} {{ get; init; }}")
        lines.append("}")
    lines.append("")
    lines.append(f"/// <summary>Variables of one {xml_doc(pg.p.name)} run: the input and each step's output.</summary>")
    lines.append(f"public sealed class {pg.name}State({pg.name}Input input)")
    lines.append("{")
    lines.append(f"    public {pg.name}Input Input {{ get; }} = input;")
    for doc, typ, name, init in props:
        lines.append("")
        lines.append(f"    /// <summary>{xml_doc(doc)}</summary>")
        lines.append(f"    public {typ} {name} {{ get; set; }}{init}")
    lines.append("")
    lines.append("    /// <summary>The exception that sent the run down an error transition, if any.</summary>")
    lines.append("    public Exception? LastError { get; set; }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_data(pg: ProcGen) -> str:
    lines = [f"namespace {pg.ns('Core.Data')};", ""]
    sigs: list[str] = []
    first = True
    for a in pg.p.activities:
        if not a.type.startswith("jdbc."):
            continue
        if not first:
            lines.append("")
        first = False
        if a.type == "jdbc.query":
            lines.append(f"/// <summary>Row of {xml_doc(a.name)} (jdbc.query): columns from the TIBCO column metadata.</summary>")
            lines.append(f"public sealed record {pg.row_type(a)}")
            lines.append("{")
            for i, (col, prop, cs_type) in enumerate(pg.columns(a)):
                if i:
                    lines.append("")
                lines.append(f"    /// <summary>Column {xml_doc(col)}.</summary>")
                lines.append(f"    public {cs_type}? {prop} {{ get; init; }}")
            lines.append("}")
            lines.append("")
        params = pg.sql_params(a)
        if params:
            lines.append(f"/// <summary>Parameters of {xml_doc(a.name)}, bound in order to the statement's placeholders.</summary>")
            lines.append(f"public sealed record {pg.input_type(a)}")
            lines.append("{")
            for i, (name, prop, cs_type) in enumerate(params):
                if i:
                    lines.append("")
                lines.append(f"    /// <summary>Parameter {xml_doc(name)}, bound as @p{i}.</summary>")
                lines.append(f"    public {cs_type}? {prop} {{ get; init; }}")
            lines.append("}")
        elif lines[-1] == "":
            lines.pop()
        ret = f"IReadOnlyList<{pg.row_type(a)}>" if a.type == "jdbc.query" else "int"
        arg = f"{pg.input_type(a)} input, " if params else ""
        sigs.append(f"    Task<{ret}> {ident(a.name or a.id)}Async({arg}CancellationToken cancellationToken);")
    lines.append("")
    lines.append(f"/// <summary>Database steps of {xml_doc(pg.p.name)}; implemented with Dapper in Infrastructure.</summary>")
    lines.append(f"public interface I{pg.name}Repository")
    lines.append("{")
    for i, s in enumerate(sigs):
        if i:
            lines.append("")
        lines.append(s)
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_repository(pg: ProcGen) -> str:
    conns = sorted({pg.connections[a.id] for a in pg.p.activities if a.type.startswith("jdbc.")})
    ctor = ", ".join(f'[FromKeyedServices("{c}")] NpgsqlDataSource {camel(c)}' for c in conns)
    lines = [_usings("System.Data", "Dapper", pg.ns("Core.Data"), "Microsoft.Extensions.DependencyInjection",
                     "Npgsql").rstrip("\n"), "",
             f"namespace {pg.ns('Infrastructure.Repositories')};", "",
             f"/// <summary>Database steps of {xml_doc(pg.p.name)} on Dapper and Npgsql; every statement is the TIBCO SQL verbatim.</summary>",
             f"public sealed class {pg.name}Repository({ctor}) : I{pg.name}Repository",
             "{"]
    members: list[list[str]] = []
    for a in pg.p.activities:
        if not a.type.startswith("jdbc."):
            continue
        sql = str(a.config.get("sql") or "")
        named = to_named_parameters(sql)
        method = ident(a.name or a.id)
        const = f"{method}Sql"
        params = pg.sql_params(a)
        row = pg.primary.get(a.id)
        timeout = str(a.config.get("timeout") or "").strip()
        max_rows = str(a.config.get("max_rows") or "").strip()
        binding = ", ".join(f"@p{i} = {name}" for i, (name, _, _) in enumerate(params)) or "none"
        member = [f"    private const string {const} = {raw_string_literal(named, 4)};", ""]
        summary = f"TIBCO step {a.name} ({a.type}) on {pg.connections[a.id]}: the SQL statement is kept verbatim."
        member.append(f"    /// <summary>{xml_doc(summary)}</summary>")
        member.append(f"    /// <remarks>{xml_doc(pg.remarks(a.id, row))}")
        member.append(f"    /// <para>TIBCO SQL: <c>{xml_doc(sql)}</c></para>")
        member.append(f"    /// <para>Parameters: the JDBC ? placeholders were converted at generation time to named "
                      f"Npgsql parameters, bound in order: {xml_doc(binding)}.</para>")
        if a.type == "jdbc.query" and max_rows.isdigit() and int(max_rows) > 0:
            member.append(f"    /// <para>TIBCO maxRows {int(max_rows)}: rows past {int(max_rows)} are dropped after the "
                          "query; the SQL stays verbatim, so add a LIMIT if the table can return many rows.</para>")
        member[-1] += "</remarks>"
        ret = f"IReadOnlyList<{pg.row_type(a)}>" if a.type == "jdbc.query" else "int"
        arg = f"{pg.input_type(a)} input, " if params else ""
        member.append(f"    public async Task<{ret}> {method}Async({arg}CancellationToken cancellationToken)")
        member.append("    {")
        if a.type == "jdbc.call":
            member.append(f"        // TIBCO stored procedure call; output parameters are mapped by the builder ({const}).")
            member.append("        await Task.Yield();")
            member.append(f'        throw new NotImplementedException("agent fills: {a.name}");')
            member.append("    }")
            members.append(member)
            continue
        member.append("        var parameters = new DynamicParameters();")
        for i, (_, prop, _) in enumerate(params):
            member.append(f'        parameters.Add("p{i}", input.{prop});')
        tmo = f", commandTimeout: {int(timeout)}" if timeout.isdigit() and int(timeout) > 0 else ""
        member.append(f"        var command = new CommandDefinition({const}, parameters{tmo}, "
                      f"commandType: CommandType.Text, cancellationToken: cancellationToken);")
        member.append(f"        await using var connection = await {camel(pg.connections[a.id])}.OpenConnectionAsync(cancellationToken);")
        if a.type == "jdbc.query":
            member.append(f"        var rows = await connection.QueryAsync<{pg.row_type(a)}>(command);")
            if max_rows.isdigit() and int(max_rows) > 0:
                member.append(f"        return [.. rows.Take({int(max_rows)})];")
            else:
                member.append("        return [.. rows];")
        else:
            member.append("        return await connection.ExecuteAsync(command);")
        member.append("    }")
        members.append(member)
    for i, m in enumerate(members):
        if i:
            lines.append("")
        lines += m
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_mappings(pg: ProcGen) -> tuple[str, Origin]:
    usings = [pg.ns("Core.Services"), pg.ns("Core.Tibco")]
    methods: list[list[str]] = []
    origin: Origin = "code"
    for a in pg.p.activities:
        row = pg.map_row.get(a.id)
        if row is None:
            continue
        t = pg.target(a)
        if t.kind == "jdbc":
            usings.append(pg.ns("Core.Data"))
        elif t.kind == "log":
            usings.append(pg.ns("Core.Diagnostics"))
        elif t.kind == "json":
            usings.append("System.Text.Json.Nodes")
        elif t.shape is not None and t.shape.namespace:
            usings.append(t.shape.namespace)
        m = [f"    /// <summary>{xml_doc(f'Mapping of {a.name} ({a.type}): the TIBCO input bindings, each kept as a comment.')}</summary>",
             f"    /// <remarks>{xml_doc(pg.remarks(a.id, row))}</remarks>",
             f"    public static {t.type_name} {row.method}({pg.name}State state)",
             "    {"]
        body = pg.initializer(a, "        ") if row.origin == "code" else None
        if body is None:
            origin = "agent"
            for b in a.input_bindings:
                m += tibco_comment(b.target, b.expression, "        ")
            if row.origin == "code":
                why = t.reason or "a path the generator cannot resolve"
                m.append(f"        // Not generated by code: {why}.")
            m.append(f'        throw new NotImplementedException("agent fills: {a.name}");')
        else:
            m.append(f"        return {body[0]}")
            m += body[1:-1]
            m.append(f"{body[-1]};")
        m.append("    }")
        methods.append(m)
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Core.Mappings')};", "",
             (f"/// <summary>Input mappings of {xml_doc(pg.p.name)}: simple paths generated by code, "
              "functions and arithmetic filled by the Code Builder.</summary>"),
             f"public static class {pg.name}Mappings", "{"]
    for i, m in enumerate(methods):
        if i:
            lines.append("")
        lines += m
    lines.append("}")
    return "\n".join(lines) + "\n", origin


# ----------------------------------------------------------------- conditions
_COUNT = re.compile(r"^count\((\$[^()]+)\)\s*(=|!=|<=|>=|<|>)\s*(\d+)$")
_CMP = re.compile(r"""^(\$[\w.\-/:@\[\]]+)\s*(=|!=|<=|>=|<|>)\s*("[^"]*"|'[^']*'|-?\d+(?:\.\d+)?|\$[\w.\-/:@\[\]]+)$""")
_OPS = {"=": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


def _split_top(expr: str, word: str) -> list[str]:
    parts, depth, quote, start, i = [], 0, "", 0, 0
    token = f" {word} "
    while i < len(expr):
        c = expr[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and expr.startswith(token, i):
            parts.append(expr[start:i])
            i += len(token)
            start = i
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def translate_condition(pg: ProcGen, expr: str) -> str | None:
    e = one_line(expr)
    if not e:
        return None
    ors = _split_top(e, "or")
    if len(ors) > 1:
        parts = [translate_condition(pg, p) for p in ors]
        return None if any(p is None for p in parts) else " || ".join(f"({p})" for p in parts)
    ands = _split_top(e, "and")
    if len(ands) > 1:
        parts = [translate_condition(pg, p) for p in ands]
        return None if any(p is None for p in parts) else " && ".join(f"({p})" for p in parts)
    if e.startswith("(") and e.endswith(")") and _balanced(e[1:-1]):
        return translate_condition(pg, e[1:-1])
    if e.startswith("not(") and e.endswith(")") and _balanced(e[4:-1]):
        inner = translate_condition(pg, e[4:-1])
        return None if inner is None else f"!({inner})"
    if e in ("true()", "false()"):
        return e[:-2]
    m = _COUNT.match(e)
    if m:
        r = pg.resolve(m.group(1))
        if r is None or r.kind != "list":
            return None
        return f"{r.expr}.Count {_OPS[m.group(2)]} {m.group(3)}"
    m = _CMP.match(e)
    if m:
        left = pg.resolve(m.group(1))
        if left is None or left.kind != "scalar":
            return None
        op, rhs = m.group(2), m.group(3)
        as_text = left.expr if left.cs_type == "string" else f"TibValue.Of({left.expr}).AsString()"
        if rhs[0] in "'\"":
            if op not in ("=", "!="):
                return None
            literal = rhs[1:-1]
            if literal == "" and left.cs_type == "string":
                return f"{'' if op == '=' else '!'}string.IsNullOrEmpty({left.expr})"
            return f"{as_text} {_OPS[op]} {cs_string(literal)}"
        if rhs.startswith("$"):
            right = pg.resolve(rhs)
            if right is None or right.kind != "scalar" or op not in ("=", "!="):
                return None
            return f"TibValue.Of({left.expr}).AsString() {_OPS[op]} TibValue.Of({right.expr}).AsString()"
        fractional = "." in rhs
        if left.cs_type in ("decimal", "double", "float") or (left.cs_type in _NUMERIC and not fractional):
            suffix = {"decimal": "m", "float": "f"}.get(left.cs_type, "") if fractional else ""
            return f"{left.expr} {_OPS[op]} {rhs}{suffix}"
        return f"TibValue.Of({left.expr}).AsDecimal() {_OPS[op]} {rhs}m"
    r = pg.resolve(e)
    if r is not None and r.kind == "scalar":
        return f"!string.IsNullOrEmpty({r.expr})" if r.cs_type == "string" else f"!TibValue.Of({r.expr}).IsEmpty"
    if r is not None and r.kind == "list":
        return f"{r.expr}.Count > 0"
    return None


def _balanced(text: str) -> bool:
    depth = 0
    for c in text:
        depth += c == "("
        depth -= c == ")"
        if depth < 0:
            return False
    return depth == 0


def condition_methods(pg: ProcGen) -> dict[str, str]:
    """Transition id -> method name for every conditional transition."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for t in pg.p.transitions:
        if t.kind != "conditional":
            continue
        name = ident(t.id, "When")
        base, n = name, 2
        while name in used:
            name, n = f"{base}{n}", n + 1
        used.add(name)
        out[t.id] = name
    return out


def gen_conditions(pg: ProcGen) -> tuple[str, Origin]:
    names = condition_methods(pg)
    origin: Origin = "code"
    usings = [pg.ns("Core.Services"), pg.ns("Core.Tibco")]
    methods: list[list[str]] = []
    for t in pg.p.transitions:
        if t.kind != "conditional":
            continue
        src = pg.acts.get(t.from_id)
        dst = pg.acts.get(t.to_id)
        line = f" line {t.source_line}" if t.source_line else ""
        m = [f"    /// <summary>{xml_doc(f'Link {t.id}: {src.name if src else t.from_id} to {dst.name if dst else t.to_id}.')}</summary>",
             f"    /// <remarks>{xml_doc(f'TIBCO condition{line}: {t.expression}')}</remarks>",
             f"    public static bool {names[t.id]}({pg.name}State state)",
             "    {"]
        expr_lines = (t.expression or "").replace("\r\n", "\n").split("\n")
        m.append(f"        // TIBCO condition: {expr_lines[0].rstrip()}".rstrip())
        m += [f"        //     {ln.rstrip()}".rstrip() for ln in expr_lines[1:]]
        cs = translate_condition(pg, t.expression or "")
        if cs is None:
            origin = "agent"
            m.append(f'        throw new NotImplementedException("agent fills: transition {t.id}");')
        else:
            m.append(f"        return {cs};")
        m.append("    }")
        methods.append(m)
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Core.Rules')};", "",
             f"/// <summary>Conditions of the {xml_doc(pg.p.name)} links, from the TIBCO XPath expressions.</summary>",
             f"public static class {pg.name}Conditions", "{"]
    for i, m in enumerate(methods):
        if i:
            lines.append("")
        lines += m
    lines.append("}")
    return "\n".join(lines) + "\n", origin


# ------------------------------------------------------------------- service
_GENERIC = {"java.invoke", "xslt.transform", "json.parse", "json.render", "xml.parse", "xml.render",
            "shared.get", "shared.set", "file.read", "file.write", "file.list", "ftp.get", "ftp.put",
            "mail.send", "mail.receive", "cmd.exec"}
_INVOKE = {"http.invoke", "rest.invoke", "soap.invoke"}


@dataclass
class ServiceDeps:
    repository: bool = False
    bus: bool = False
    log: bool = False


@dataclass
class Step:
    lines: list[str]  # statements, unindented
    awaits: bool = False
    terminal: str = ""  # "" | "throw" | "return"
    origin: Origin = "code"
    sets_result: bool = False


RETURN = "@@RETURN@@"  # replaced by the method's final return statement


def _exception_class(pg: ProcGen, act: Activity) -> str:
    row = pg.primary.get(act.id)
    if row and row.pattern == "throw":
        return row.class_name
    fault = ident(str(act.config.get("fault_name") or act.name), "Fault")
    return fault if fault.endswith("Exception") else f"{fault}Exception"


def _step_header(pg: ProcGen, node_id: str) -> str:
    act = pg.acts.get(node_id)
    row = pg.primary.get(node_id)
    if act is None:
        g = pg.groups[node_id]
        return f"// TIBCO: group {g.id} ({g.kind}) over {', '.join(g.member_ids)}."
    line = f" line {act.source_line}" if act.source_line else ""
    lineage = pg.remarks(node_id, row).split(". TIBCO:")[0]
    return f"// TIBCO: {act.name} ({act.type}){line}. {lineage}."


def _step(pg: ProcGen, node_id: str, deps: ServiceDeps, final: bool) -> Step:
    """The statements of one step; ``final`` (no outgoing link) lets a reply return directly."""
    act = pg.acts.get(node_id)
    if act is None:
        g = pg.groups[node_id]
        what = f"group {g.id} ({g.kind}) over {', '.join(g.member_ids)}"
        return Step([f"AgentFill.Required({cs_string(what)});"], origin="agent")
    t = act.type
    prop = pg.state_props[act.id]
    mapping = pg.map_row.get(act.id)
    mcall = f"{pg.name}Mappings.{mapping.method}(state)" if mapping else None
    method = ident(act.name or act.id)
    if pg.starter and act.id == pg.starter.activity_id:
        return Step([f"// Starter: the {'request' if pg.kind in _API_KINDS else 'input'} is in state.Input."])
    if t.startswith("jdbc."):
        deps.repository = True
        params = pg.sql_params(act)
        arg = (f"{mcall}, " if mcall else f"new {pg.input_type(act)}(), ") if params else ""
        return Step([f"state.{prop} = await repository.{method}Async({arg}cancellationToken);"], awaits=True)
    if t in ("jms.send", "jms.publish"):
        dest = str(act.config.get("destination") or (act.config.get("settings") or {}).get("destination") or "")
        if mcall is None or not dest:
            return Step([f'AgentFill.Required({cs_string(f"{act.name} ({t})")});'], origin="agent")
        deps.bus = True
        return Step([f"await bus.PublishAsync({cs_string(dest)}, {mcall}, cancellationToken: cancellationToken);"],
                    awaits=True)
    if t == "throw":
        return Step([f"throw new {_exception_class(pg, act)}({mcall or 'null'});"], terminal="throw")
    if t in ("reply", "end"):
        if t == "reply" and pg.kind in _API_KINDS and mcall and pg.target(act).kind == "error":
            value = f"ProcessResult.FromError({mcall})"
        elif mcall:
            value = f"new ProcessResult(200, {mcall})"
        else:
            value = "new ProcessResult(200, null)" if t == "reply" else "ProcessResult.Completed"
        if final:
            return Step([f"return {value};"], terminal="return")
        return Step([f"result ??= {value};"], sets_result=True)
    if t == "log":
        if mcall is None:
            return Step(["// TIBCO log step without input."])
        deps.log = True
        return Step([f"log.Write({cs_string(pg.p.name)}, {cs_string(act.name)}, {mcall});"])
    if t in ("mapper", "assign"):
        if mcall is None:
            return Step([f"// TIBCO {t} without bindings."])
        return Step([f"state.{prop} = {mcall};"])
    if t in ("noop", "catch", "confirm"):
        return Step([f"// TIBCO {t}: no code; kept for lineage."])
    if t == "call.process":
        target = next((c.target_process for c in pg.p.calls if c.activity_id == act.id), "")
        what = f"{act.name} (call {target or 'subprocess'})"
        return Step([f"AgentFill.Required({cs_string(what)});"], origin="agent")
    row = pg.primary.get(act.id)
    where = f": {row.class_name}.{row.method}" if row else ""
    return Step([f"AgentFill.Required({cs_string(f'{act.name} ({t}){where}')});"], origin="agent")


def _indent(lines: list[str], n: int = 4) -> list[str]:
    pad = " " * n
    return [f"{pad}{ln}" if ln else "" for ln in lines]


def _flatten(blocks: list[list[str]]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if not b:
            continue
        if out:
            out.append("")
        out += b
    return out


class _Structured:
    """Straight-line C# for processes whose links form sequences and if/else regions.

    Gives up (``build`` returns ``None``) on parallel links, several conditional links on one step,
    error links, joins that are not the merge of an if/else, or links crossing a group boundary;
    the queue form with ``ProcessFlow`` handles those.
    """

    EXIT = "\0exit"

    def __init__(self, pg: ProcGen, deps: ServiceDeps, conds: dict[str, str]) -> None:
        self.pg, self.deps, self.conds = pg, deps, conds
        members = {m for g in pg.p.groups for m in g.member_ids}
        handled = {aid for h in pg.p.fault_handlers for aid in h.activity_ids}
        self.main = [n for n in [*pg.acts, *pg.groups] if n not in members and n not in handled]
        main = set(self.main)
        self.out: dict[str, list[Transition]] = {n: [] for n in self.main}
        self.preds: dict[str, list[str]] = {n: [] for n in self.main}
        self.ok = True
        for t in pg.p.transitions:
            a, b = t.from_id in main, t.to_id in main
            if a and b:
                self.out[t.from_id].append(t)
                self.preds[t.to_id].append(t.from_id)
                if t.kind == "error":
                    self.ok = False
            elif a != b:
                self.ok = False
        self.emitted: set[str] = set()
        self.allowed: set[str] = set()
        self.steps: list[Step] = []
        self.ipdom = self._ipdoms() if self.ok else {}

    def _ipdoms(self) -> dict[str, str | None]:
        succ = {n: sorted({t.to_id for t in self.out[n]}) or [self.EXIT] for n in self.main}
        everything = set(self.main) | {self.EXIT}
        pdom: dict[str, set[str]] = {n: set(everything) for n in self.main}
        pdom[self.EXIT] = {self.EXIT}
        changed = True
        while changed:
            changed = False
            for n in self.main:
                new = {n} | set.intersection(*(pdom[s] for s in succ[n]))
                if new != pdom[n]:
                    pdom[n], changed = new, True
        out: dict[str, str | None] = {}
        for n in self.main:
            strict = pdom[n] - {n}
            d = next((x for x in sorted(strict) if pdom[x] == strict), None)
            out[n] = None if d in (None, self.EXIT) else d
        return out

    def build(self) -> tuple[list[str], bool] | None:
        if not self.ok:
            return None
        pg = self.pg
        if pg.starter and pg.starter.activity_id in self.out:
            starts = [pg.starter.activity_id]
        else:
            starts = [n for n in self.main if not self.preds[n]]
        if len(starts) != 1:
            return None
        r = self.region(starts[0], None)
        if r is None or set(self.main) - self.emitted:
            return None
        return _flatten(r[0]), r[1]

    def region(self, node: str | None, stop: str | None) -> tuple[list[list[str]], bool] | None:
        pg = self.pg
        blocks: list[list[str]] = []
        while node is not None and node != stop:
            if node in self.emitted or (len(self.preds[node]) > 1 and node not in self.allowed):
                return None
            self.emitted.add(node)
            outs = self.out[node]
            st = _step(pg, node, self.deps, final=not outs)
            self.steps.append(st)
            block = [_step_header(pg, node), *st.lines]
            if st.terminal:
                if outs:
                    return None
                blocks.append(block)
                return blocks, True
            succ = [t for t in outs if t.kind == "success"]
            cond = [t for t in outs if t.kind == "conditional"]
            other = [t for t in outs if t.kind == "otherwise"]
            if (succ and (cond or other)) or len(succ) > 1 or len(cond) > 1 or len(other) > 1:
                return None
            blocks.append(block)
            if succ:
                node = succ[0].to_id
                continue
            if not cond:
                node = other[0].to_id if other else None
                continue
            c, o = cond[0], (other[0] if other else None)
            merge = self.ipdom.get(node)
            then_r = self.region(c.to_id, merge) if c.to_id != merge else ([], False)
            else_r = (self.region(o.to_id, merge) if o.to_id != merge else ([], False)) if o else ([], False)
            if then_r is None or else_r is None:
                return None
            call = f"{pg.name}Conditions.{self.conds[c.id]}(state)"
            c_comment = f"// Link {c.id}: {one_line(c.expression or '')}"
            o_comment = [f"// Link {o.id}: otherwise"] if o else \
                ["// No otherwise link: when the condition is false the process ends here."]
            then_lines, else_lines = _flatten(then_r[0]), _flatten(else_r[0])
            if merge is not None:
                if any(p not in self.emitted for p in self.preds[merge]):
                    return None
                self.allowed.add(merge)
                if then_lines and else_lines:
                    blocks.append([c_comment, f"if ({call})", "{", *_indent(then_lines), "}", "else", "{",
                                   *_indent(o_comment + else_lines), "}"])
                elif then_lines:
                    blocks.append([c_comment, f"if ({call})", "{", *_indent(then_lines), "}"])
                elif else_lines:
                    blocks.append([c_comment, f"if (!{call})", "{", *_indent(o_comment + else_lines), "}"])
                node = merge
                continue
            # no merge: both branches end the process; the shorter one becomes a guard clause
            if len(else_lines) <= len(then_lines):
                guard = o_comment + else_lines + ([] if else_r[1] else [RETURN])
                blocks.append([c_comment, f"if (!{call})", "{", *_indent(guard), "}"])
                return blocks + then_r[0], then_r[1]
            guard = then_lines + ([] if then_r[1] else [RETURN])
            blocks.append([c_comment, f"if ({call})", "{", *_indent(guard), "}"])
            rest = [list(b) for b in else_r[0]]
            rest[0] = o_comment + rest[0]
            return blocks + rest, else_r[1]
        return blocks, False


def _queue_body(pg: ProcGen, deps: ServiceDeps, conds: dict[str, str]) -> tuple[list[str], list[Step]]:
    """The general form: a ``ProcessFlow`` runs each step once all its live incoming links are decided."""
    steps: list[Step] = []
    handled = {aid for h in pg.p.fault_handlers for aid in h.activity_ids}
    targets = {t.to_id for t in pg.p.transitions}
    if pg.starter and pg.starter.activity_id in pg.acts:
        starts = [pg.starter.activity_id]
    else:
        members = {m for g in pg.p.groups for m in g.member_ids}
        starts = [a.id for a in pg.p.activities if a.id not in targets and a.id not in handled
                  and a.id not in members] or [a.id for a in pg.p.activities[:1]]
    lines = ["var flow = new ProcessFlow(Links);"]
    lines += [f"flow.Start({cs_string(s)});" for s in starts]
    lines += ["while (flow.TryNext(out var step))", "{", "    switch (step)", "    {"]
    cases: list[list[str]] = []
    for node_id in [*pg.acts, *pg.groups]:
        st = _step(pg, node_id, deps, final=False)
        steps.append(st)
        outs = pg.outs.get(node_id, [])
        succ = [t.id for t in outs if t.kind == "success"]
        cond = [t for t in outs if t.kind == "conditional"]
        other = [t.id for t in outs if t.kind == "otherwise"]
        errors = [t.id for t in outs if t.kind == "error"]
        header = [_step_header(pg, node_id)]
        if node_id in handled:
            header.append("// Runs in a TIBCO fault handler (see the catch blocks in ExecuteAsync).")
        body: list[str] = []
        if errors and not st.terminal:
            err = ", ".join(cs_string(e) for e in errors)
            body += ["try", "{", *_indent(st.lines), "}",
                     "catch (Exception ex) when (ex is not OperationCanceledException)", "{",
                     "    state.LastError = ex;", f"    flow.Complete({cs_string(node_id)}, [{err}]);",
                     "    break;", "}", ""]
        else:
            body += st.lines
        if st.terminal:
            cases.append(header + [f"case {cs_string(node_id)}:"] + _indent(body))
            continue
        if not cond:
            taken = ", ".join(cs_string(x) for x in succ + other)
            body.append(f"flow.Complete({cs_string(node_id)}, [{taken}]);")
        else:
            var = f"taken{ident(node_id)}"
            init = ", ".join(cs_string(x) for x in succ)
            body.append(f"var {var} = new List<string> {{ {init} }};" if init else f"var {var} = new List<string>();")
            for t in cond:
                body += [f"// Link {t.id}: {one_line(t.expression or '')}",
                         f"if ({pg.name}Conditions.{conds[t.id]}(state))", "{", f"    {var}.Add({cs_string(t.id)});", "}"]
            if other:
                body += [f"if ({var}.Count == {len(succ)})", "{"]
                body += [f"    {var}.Add({cs_string(o)}); // otherwise" for o in other]
                body.append("}")
            body.append(f"flow.Complete({cs_string(node_id)}, {var});")
        body.append("break;")
        cases.append(header + [f"case {cs_string(node_id)}:"] + _indent(body))
    switch: list[str] = []
    for i, c in enumerate(cases):
        if i:
            switch.append("")
        switch += c
    switch += ["", "default:", '    throw new InvalidOperationException($"Unknown step {step}");']
    lines += _indent(switch, 8) + ["    }", "}", "", RETURN]
    return lines, steps


def _links_field(pg: ProcGen) -> list[str]:
    out = ["    /// <summary>The TIBCO links (id, from, to): a step runs once every live incoming link is decided.</summary>",
           "    private static readonly ProcessLink[] Links =", "    ["]
    out += [f"        new({cs_string(t.id)}, {cs_string(t.from_id)}, {cs_string(t.to_id)})," for t in pg.p.transitions]
    out += ["    ];", ""]
    return out


def gen_service(pg: ProcGen) -> tuple[str, Origin]:
    deps = ServiceDeps()
    conds = condition_methods(pg)
    structured = _Structured(pg, deps, conds)
    built = structured.build()
    if built is not None:
        body, terminated = built
        steps = structured.steps
        if not terminated:
            body = [*body, "", RETURN] if body else [RETURN]
        queue = False
    else:
        deps = ServiceDeps()
        body, steps = _queue_body(pg, deps, conds)
        queue = True
    awaits = any(s.awaits for s in steps)
    uses_result = any(s.sets_result for s in steps)
    origin: Origin = "agent" if any(s.origin == "agent" for s in steps) or pg.p.fault_handlers else "code"
    final = "result ?? ProcessResult.Completed" if uses_result else "ProcessResult.Completed"
    out_body: list[str] = []
    for ln in body:
        if ln.strip() == RETURN:
            ln = ln.replace(RETURN, f"return {final};")
        if not awaits:
            m = re.match(r"^(\s*)return (.+);$", ln)
            if m and not ln.strip().startswith("return Task."):
                ln = f"{m.group(1)}return Task.FromResult({m.group(2)});"
        out_body.append(ln)
    prologue = []
    if not awaits:
        prologue.append("cancellationToken.ThrowIfCancellationRequested();")
    if uses_result:
        prologue.append("ProcessResult? result = null;")

    ctor: list[str] = []
    text_all = "\n".join(out_body)
    usings = [pg.ns("Core.Rules") if conds and "Conditions." in text_all else "",
              pg.ns("Core.Mappings") if "Mappings." in text_all else "",
              pg.ns("Core.Tibco") if ("AgentFill." in text_all or queue or pg.p.fault_handlers) else ""]
    if deps.repository:
        ctor.append(f"I{pg.name}Repository repository")
        usings.append(pg.ns("Core.Data"))
    if deps.bus:
        ctor.append("IMessageBus bus")
        usings.append(pg.ns("Core.Messaging"))
    if deps.log:
        ctor.append("IStepLogger log")
        usings.append(pg.ns("Core.Diagnostics"))
    if any(a.type == "throw" for a in pg.p.activities) or pg.p.fault_handlers:
        usings.append(pg.ns("Core.Exceptions"))
    sig_ctor = f"({', '.join(ctor)})" if ctor else ""
    form = ("Steps follow the TIBCO links through a ProcessFlow: a step runs once every live incoming link "
            "is decided (joins wait for all live predecessors; a link not taken kills its path), conditional links "
            "come from the generated conditions and an otherwise link is taken when no conditional link of its "
            "step was.") if queue else \
        ("The TIBCO links form sequences and if/else regions, so the process reads as straight-line code; "
         "each if is a conditional link and its otherwise link, with the link ids in comments.")
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Core.Services')};", "",
             f"/// <summary>The TIBCO process {xml_doc(pg.p.name)} ({xml_doc(pg.p.id)}) as a .NET service.</summary>",
             f"/// <remarks>{xml_doc(form)}</remarks>",
             f"public sealed class {pg.name}Service{sig_ctor}", "{"]
    if queue:
        lines += _links_field(pg)
    mod = "async " if awaits else ""
    if pg.p.fault_handlers:
        lines += [f"    public async Task<ProcessResult> ExecuteAsync({pg.name}Input input, CancellationToken cancellationToken = default)",
                  "    {",
                  "        try",
                  "        {",
                  f"            return await RunStepsAsync(new {pg.name}State(input), cancellationToken);",
                  "        }"]
        for h in pg.p.fault_handlers:
            names = ", ".join(pg.acts[a].name if a in pg.acts else a for a in h.activity_ids)
            if h.fault_name:
                lines.append(f"        catch (ProcessFaultException fault) when (fault.FaultName == {cs_string(h.fault_name)})")
            else:
                lines.append("        catch (Exception ex) when (ex is not OperationCanceledException)")
            what = f"fault handler {h.fault_name or 'catch all'}: {names}"
            lines += ["        {", f"            // TIBCO fault handler {h.id}: {names}",
                      f"            AgentFill.Required({cs_string(what)});", "            throw;", "        }"]
        lines += ["    }", "",
                  f"    private {mod}Task<ProcessResult> RunStepsAsync({pg.name}State state, CancellationToken cancellationToken)",
                  "    {"]
    else:
        lines += [f"    public {mod}Task<ProcessResult> ExecuteAsync({pg.name}Input input, CancellationToken cancellationToken = default)",
                  "    {", f"        var state = new {pg.name}State(input);"]
    lines += _indent(prologue, 8)
    if lines[-1].strip() != "{":
        lines.append("")
    lines += _indent(out_body, 8)
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n", origin


# --------------------------------------------------------------- exceptions
def gen_exception(pg: ProcGen, act: Activity) -> str:
    cls = _exception_class(pg, act)
    fault = str(act.config.get("fault_name") or act.name)
    t = pg.target(act) if act.input_bindings else None
    shape = t.shape if t and t.kind == "record" else pg.contracts.root(fault)
    ftype = shape.name if shape else "JsonObject"
    usings = [shape.namespace] if shape else ["System.Text.Json.Nodes"]
    row = pg.primary.get(act.id)
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Core.Exceptions')};", "",
             f"/// <summary>TIBCO fault {xml_doc(fault)}, thrown by {xml_doc(act.name)} in {xml_doc(pg.p.name)}.</summary>",
             f"/// <remarks>{xml_doc(pg.remarks(act.id, row))}</remarks>",
             f"public sealed class {cls}({ftype}? fault)",
             f"    : ProcessFaultException({cs_string(fault)}, fault, {cs_string(f'TIBCO fault {fault}')})",
             "{",
             "    /// <summary>The fault record built by the throw mapping.</summary>",
             f"    public {ftype}? Fault {{ get; }} = fault;",
             "}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- controllers
def gen_controller(pg: ProcGen) -> str:
    cfg = pg.starter.config
    verb = str(cfg.get("verb") or "POST").upper()
    path = str(cfg.get("path") or cfg.get("resource_path") or f"/{pg.p.name}")
    attr = {"GET": "HttpGet", "POST": "HttpPost", "PUT": "HttpPut", "DELETE": "HttpDelete",
            "PATCH": "HttpPatch", "HEAD": "HttpHead"}.get(verb, "HttpPost")
    usings = ["Microsoft.AspNetCore.Authorization", "Microsoft.AspNetCore.Mvc", pg.ns("Core.Services")]
    args: list[str] = []
    assigns: list[str] = []
    for prm in pg.parameters:
        loc = prm.location or "query"
        name = camel(prm.wire)
        src = {"path": "FromRoute", "header": "FromHeader"}.get(loc, "FromQuery")
        attr_args = "" if name == prm.wire and src == "FromRoute" else f"(Name = {cs_string(prm.wire)})"
        nul = "" if prm.required else "?"
        args.append(f"[{src}{attr_args}] {prm.cs_type}{nul} {name}")
        assigns.append(f"{prm.cs} = {name}")
    body_arg = ""
    if pg.kind == "rest" and pg.body_shape is not None:
        usings.append(pg.body_shape.namespace)
        args.append(f"[FromBody] {pg.body_shape.name} body")
        body_arg = ", body"
    elif pg.kind in ("http", "soap"):
        usings.append("System.Text.Json")
        args.append("[FromBody] JsonElement body")
        body_arg = "body"
    args.append("CancellationToken cancellationToken")
    if pg.kind == "rest":
        init = ", ".join(assigns)
        input_expr = f"new {pg.name}Input(new {pg.name}Parameters {{ {init} }}{body_arg})" if init else \
            f"new {pg.name}Input(new {pg.name}Parameters(){body_arg})"
    else:
        input_expr = f"new {pg.name}Input({body_arg})"
    produces: list[str] = []
    for a in pg.p.activities:
        if a.type != "reply":
            continue
        t = pg.target(a)
        if t.kind == "error":
            code = next((b.expression.strip() for b in a.input_bindings if b.target.endswith("/statusCode")
                         and b.expression.strip().isdigit()), None)
            if code:
                produces.append(f"[ProducesResponseType(typeof(ErrorReply), {code})]")
        elif t.shape is not None:
            usings.append(t.shape.namespace)
            produces.append(f"[ProducesResponseType(typeof({t.shape.name}), StatusCodes.Status200OK)]")
    produces = list(dict.fromkeys(produces))
    operation = cfg.get("operation") or pg.starter.activity_id
    row = pg.primary.get(pg.starter.activity_id)
    method = row.method if row and row.pattern in ("rest.receive", "http.receive") else f"{ident(pg.starter_act.name if pg.starter_act else 'Execute')}Async"
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Api.Controllers')};", "",
             f"/// <summary>{xml_doc(f'{pg.kind.upper()} starter of {pg.p.name}: {verb} {path}.')}</summary>",
             "[ApiController]", "[Authorize]",
             f"public sealed class {pg.name}Controller({pg.name}Service service) : ControllerBase", "{",
             f"    /// <summary>{xml_doc(f'{verb} {path} (TIBCO operation {operation}).')}</summary>",
             f"    /// <remarks>{xml_doc(pg.remarks(pg.starter.activity_id, row))}</remarks>",
             f"    [{attr}({cs_string(path)})]"]
    lines += [f"    {p}" for p in produces]
    lines += [f"    public async Task<IActionResult> {method}({', '.join(args)})", "    {",
              f"        var result = await service.ExecuteAsync({input_expr}, cancellationToken);",
              "        return StatusCode(result.StatusCode, result.Body);", "    }", "}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ consumers
_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")


_ACK_MODES = {"1": "auto (acknowledged on receipt; a failed job was not redelivered)",
              "2": "client (acknowledged by the process; unacknowledged messages were redelivered)",
              "3": "dups-ok (lazy acknowledge)", "4": "no acknowledge",
              "22": "explicit client", "23": "explicit client dups-ok", "24": "transacted"}


def gen_consumer(pg: ProcGen) -> tuple[str, Origin]:
    cfg = pg.starter.config
    settings = pg.starter_act.config.get("settings") if pg.starter_act and isinstance(pg.starter_act.config.get("settings"), dict) else {}
    dest = str(cfg.get("destination") or "")
    style = str(cfg.get("messaging_style") or "").lower()
    topic = style == "topic"
    entity = f"{dest}/{pg.p.name}" if topic else dest
    selector = str(cfg.get("selector") or "").strip()
    ack = str(settings.get("acknowledgeMode") or "1")
    row = pg.primary.get(pg.starter.activity_id)
    origin: Origin = "code"
    usings = ["System.Text.Json", pg.ns("Core.Exceptions"), pg.ns("Core.Messaging"), pg.ns("Core.Services"),
              "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging"]
    usings += _contract_usings(pg, pg.message_shape)
    mtype = pg.message_type()
    accepts: list[str] = []
    if selector:
        atoms = re.split(r"(?i)\s+and\s+", selector)
        parsed = [_SELECTOR_ATOM.match(a) for a in atoms]
        if all(parsed):
            conds = []
            for m in parsed:
                var = camel(m.group(1))
                conds.append(f"message.Properties.TryGetValue({cs_string(m.group(1))}, out var {var}) && {var} == {cs_string(m.group(2))}")
            accepts += ["    /// <summary>The TIBCO selector, checked on every message (also on topics, behind the subscription rule).</summary>",
                        "    public static bool Accepts(BusMessage message)", "    {",
                        "        ArgumentNullException.ThrowIfNull(message);",
                        f"        return {' && '.join(conds)};", "    }"]
        else:
            origin = "agent"
            usings.append(pg.ns("Core.Tibco"))
            accepts += ["    /// <summary>The TIBCO selector; the code builder translates it (SelectorFilter is the verbatim form).</summary>",
                        "    public static bool Accepts(BusMessage message)", "    {",
                        f"        AgentFill.Required({cs_string(f'JMS selector of {pg.starter_act.name}: {selector}')});",
                        "        return message is not null;", "    }"]
    else:
        accepts.append("    public static bool Accepts(BusMessage message) => message is not null;")
    filter_doc = ("Service Bus SQL filter for the topic subscription rule (the TIBCO selector; JMS selectors "
                  "and Service Bus SQL filters share the SQL-92 subset used here). Empty when there is no selector."
                  if topic else
                  "The TIBCO selector as a Service Bus SQL filter; a queue has no subscription rule, so a message "
                  "that does not match is dead-lettered with reason SelectorMismatch. Empty when there is no selector.")
    mismatch = ("A message the subscription rule let through that still fails the selector is dead-lettered."
                if topic else
                "TIBCO left non-matching messages on the queue for other receivers; a Service Bus queue cannot, "
                "so they are dead-lettered for review instead of being completed and lost.")
    settle = (f"TIBCO acknowledge mode: {_ACK_MODES.get(ack, ack)}. Here a message is settled after the process "
              "ran: completed on success, dead-lettered on a business fault (reason = fault name), a selector "
              "mismatch or an unreadable body, and abandoned for redelivery only on a transient error.")
    if mtype == "string":
        read = ["        var body = message.Body;"]
    else:
        read = ["        " + mtype + "? body;",
                "        try",
                "        {",
                f"            body = JsonSerializer.Deserialize<{mtype}>(message.Body, BusJson.Options);",
                "        }",
                "        catch (JsonException ex)",
                "        {",
                '            logger.LogWarning(ex, "Unreadable message on {Entity}; dead-lettered", Entity);',
                '            return BusOutcome.DeadLetter("InvalidBody", ex.Message);',
                "        }",
                "",
                "        if (body is null)",
                "        {",
                '            return BusOutcome.DeadLetter("InvalidBody", "empty message body");',
                "        }"]
    name = pg.starter_act.name if pg.starter_act else pg.starter.activity_id
    body_note = (f"The TIBCO message body was {cfg.get('message_type') or 'XML text'}; on the bus it is JSON "
                 "of the contract record.")
    remarks = "".join([xml_doc(pg.remarks(pg.starter.activity_id, row)), f"<para>{xml_doc(settle)}</para>",
                       f"<para>{xml_doc(mismatch)}</para>", f"<para>{xml_doc(body_note)}</para>"])
    lines = [_usings(*usings).rstrip("\n"), "", f"namespace {pg.ns('Infrastructure.Messaging')};", "",
             f"/// <summary>{xml_doc(f'JMS starter {name} of {pg.p.name}: consumes {entity} through IMessageBus.')}</summary>",
             f"/// <remarks>{remarks}</remarks>",
             f"public sealed class {pg.name}Consumer(IMessageBus bus, {pg.name}Service service, ILogger<{pg.name}Consumer> logger) : IHostedService",
             "{",
             "    private IAsyncDisposable? _subscription;",
             "",
             f"    public const string Entity = {cs_string(entity)};",
             "",
             f"    /// <summary>{xml_doc(filter_doc)}</summary>",
             f"    public const string SelectorFilter = {cs_string(selector)};",
             ""]
    lines += accepts
    lines += ["",
              "    public async Task StartAsync(CancellationToken cancellationToken)",
              "    {",
              "        _subscription = await bus.SubscribeAsync(Entity, OnMessageAsync, cancellationToken);",
              "    }",
              "",
              "    public async Task StopAsync(CancellationToken cancellationToken)",
              "    {",
              "        if (_subscription is not null)",
              "        {",
              "            await _subscription.DisposeAsync();",
              "            _subscription = null;",
              "        }",
              "    }",
              "",
              "    /// <summary>Runs the process for one message (the scenario tests call this directly).</summary>",
              f"    public Task HandleAsync({mtype} message, CancellationToken cancellationToken)",
              "    {",
              f"        return service.ExecuteAsync(new {pg.name}Input(message), cancellationToken);",
              "    }",
              "",
              "    private async Task<BusOutcome> OnMessageAsync(BusMessage message, CancellationToken cancellationToken)",
              "    {",
              "        if (!Accepts(message))",
              "        {",
              '            logger.LogWarning("Message on {Entity} does not match the selector {Selector}; dead-lettered", Entity, SelectorFilter);',
              '            return BusOutcome.DeadLetter("SelectorMismatch", SelectorFilter);',
              "        }",
              ""]
    lines += read
    lines += ["",
              "        try",
              "        {",
              "            await HandleAsync(body, cancellationToken);",
              "            return BusOutcome.Completed;",
              "        }",
              "        catch (ProcessFaultException fault)",
              "        {",
              "            // A business fault is not retried: redelivery would fault again.",
              '            logger.LogWarning(fault, "Business fault {Fault} on {Entity}; dead-lettered", fault.FaultName, Entity);',
              "            return BusOutcome.DeadLetter(fault.FaultName, fault.Message);",
              "        }",
              "",
              "        // Any other exception is transient: the bus abandons the message and redelivers it.",
              "    }",
              "}"]
    return "\n".join(lines) + "\n", origin


def gen_job(pg: ProcGen) -> str:
    cfg = pg.starter.config
    row = pg.primary.get(pg.starter.activity_id)
    key = f"Jobs:{pg.name}"
    starter = pg.starter_act.name if pg.starter_act else pg.starter.activity_id
    summary = (f"Timer starter {starter} of {pg.p.name}: runs every {key}:Interval, first at {key}:Start "
               f"(TIBCO: {cfg.get('interval') or cfg.get('start') or 'schedule'}). A failed run is logged and the "
               "next tick runs as scheduled; no exception leaves ExecuteAsync.")
    lines = [_usings("System.Globalization", pg.ns("Core.Services"), "Microsoft.Extensions.Configuration",
                     "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging").rstrip("\n"), "",
             f"namespace {pg.ns('Infrastructure.Jobs')};", "",
             f"/// <summary>{xml_doc(summary)}</summary>",
             f"/// <remarks>{xml_doc(pg.remarks(pg.starter.activity_id, row))}</remarks>",
             f"public sealed class {pg.name}Job({pg.name}Service service, IConfiguration configuration, TimeProvider timeProvider, ILogger<{pg.name}Job> logger) : BackgroundService",
             "{",
             "    /// <summary>One run, as the timer fires it (the scenario tests call this directly).</summary>",
             "    public Task RunOnceAsync(CancellationToken cancellationToken)",
             "    {",
             f"        return service.ExecuteAsync(new {pg.name}Input(timeProvider.GetUtcNow()), cancellationToken);",
             "    }",
             "",
             "    protected override async Task ExecuteAsync(CancellationToken stoppingToken)",
             "    {",
             '        if (!configuration.GetValue("Jobs:Enabled", true))',
             "        {",
             "            return;",
             "        }",
             "",
             "        try",
             "        {",
             f'            if (!TimeSpan.TryParse(configuration["{key}:Interval"], CultureInfo.InvariantCulture, out var interval) || interval <= TimeSpan.Zero)',
             "            {",
             "                interval = TimeSpan.FromDays(1);",
             f'                logger.LogWarning("{key}:Interval is missing or invalid; using {{Interval}}", interval);',
             "            }",
             "",
             f'            if (TimeOnly.TryParse(configuration["{key}:Start"], CultureInfo.InvariantCulture, out var at))',
             "            {",
             "                var now = timeProvider.GetLocalNow();",
             "                var first = new DateTimeOffset(now.Date + at.ToTimeSpan(), now.Offset);",
             "                if (first <= now)",
             "                {",
             "                    first = first.AddDays(1);",
             "                }",
             "",
             "                await Task.Delay(first - now, timeProvider, stoppingToken);",
             "                await RunTickAsync(stoppingToken);",
             "            }",
             "",
             "            using var timer = new PeriodicTimer(interval, timeProvider);",
             "            while (await timer.WaitForNextTickAsync(stoppingToken))",
             "            {",
             "                await RunTickAsync(stoppingToken);",
             "            }",
             "        }",
             "        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)",
             "        {",
             "            // host shutdown",
             "        }",
             "        catch (Exception ex)",
             "        {",
             f'            logger.LogError(ex, "{pg.name} timer stopped");',
             "        }",
             "    }",
             "",
             "    private async Task RunTickAsync(CancellationToken stoppingToken)",
             "    {",
             "        try",
             "        {",
             "            await RunOnceAsync(stoppingToken);",
             "        }",
             "        catch (Exception ex) when (ex is not OperationCanceledException)",
             "        {",
             f'            logger.LogError(ex, "{pg.name} run failed; the next run follows the schedule");',
             "        }",
             "    }",
             "}"]
    return "\n".join(lines) + "\n"


def _interval(cfg: dict[str, Any]) -> str:
    unit = str(cfg.get("interval_unit") or "").lower()
    try:
        n = int(str(cfg.get("time_interval") or "1"))
    except ValueError:
        n = 1
    seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}.get(unit.rstrip("s"), 86400) * n
    days, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{days}.{h:02d}:{m:02d}:{s:02d}"


# ------------------------------------------------------------- agent classes
def gen_agent_class(app: str, project_ns: str, folder: str, class_name: str,
                    members: list[tuple[str, str, str]]) -> str:
    """A class whose methods the builder fills: (method, activity, remarks)."""
    ns = f"{project_ns}.{folder}" if folder else project_ns
    lines = [f"namespace {ns};", "",
             f"/// <summary>{xml_doc(f'{class_name}: TIBCO steps the Code Builder fills (no code template).')}</summary>",
             f"public sealed class {class_name}", "{"]
    for i, (method, act_name, remarks) in enumerate(members):
        if i:
            lines.append("")
        lines += [f"    /// <summary>{xml_doc(f'TIBCO step {act_name}.')}</summary>",
                  f"    /// <remarks>{xml_doc(remarks)}</remarks>",
                  f"    public Task<object?> {method}(CancellationToken cancellationToken)", "    {",
                  "        cancellationToken.ThrowIfCancellationRequested();",
                  f'        throw new NotImplementedException("agent fills: {act_name}");', "    }"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_client(app: str, class_name: str, members: list[tuple[str, str, str]]) -> str:
    lines = [_usings("System.Text.Json.Nodes").rstrip("\n"), "", f"namespace {app}.Infrastructure.Clients;", "",
             f"/// <summary>{xml_doc(f'Typed HTTP client {class_name}: timeout, retry and circuit breaker from the TIBCO HTTP client resource (registered in DI).')}</summary>",
             f"public sealed class {class_name}(HttpClient http)", "{",
             "    /// <summary>The configured HttpClient (base address and resilience from DI).</summary>",
             "    public HttpClient Http { get; } = http;"]
    for method, act_name, remarks in members:
        lines += ["", f"    /// <summary>{xml_doc(f'TIBCO step {act_name}: the request and response mapping are filled by the Code Builder.')}</summary>",
                  f"    /// <remarks>{xml_doc(remarks)}</remarks>",
                  f"    public Task<JsonObject?> {method}(JsonObject request, CancellationToken cancellationToken)", "    {",
                  "        ArgumentNullException.ThrowIfNull(request);",
                  "        cancellationToken.ThrowIfCancellationRequested();",
                  f'        throw new NotImplementedException("agent fills: {act_name}");', "    }"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def gen_soap_stub(pg: ProcGen) -> str:
    lines = [f"namespace {pg.ns('Api.Soap')};", "",
             f"/// <summary>{xml_doc(f'SOAP starter of {pg.p.name}: CoreWCF service contract filled by the Code Builder (design decision).')}</summary>",
             f"public sealed class {pg.name}SoapService", "{",
             "    public Task<object?> ExecuteAsync(CancellationToken cancellationToken)", "    {",
             "        cancellationToken.ThrowIfCancellationRequested();",
             f'        throw new NotImplementedException("agent fills: SOAP starter {pg.p.name}");', "    }", "}"]
    return "\n".join(lines) + "\n"


def gen_subprocess_stub(app: str, name: str, target: str) -> str:
    lines = [_usings("System.Text.Json.Nodes").rstrip("\n"), "", f"namespace {app}.Core.Services;", "",
             f"/// <summary>{xml_doc(f'Subprocess {target}: outside the generated selection; generate it with the caller or fill it here.')}</summary>",
             f"public sealed class {name}Service", "{",
             "    public Task<JsonObject?> ExecuteAsync(JsonObject? start, CancellationToken cancellationToken)", "    {",
             "        cancellationToken.ThrowIfCancellationRequested();",
             f'        throw new NotImplementedException("agent fills: subprocess {target}");', "    }", "}"]
    return "\n".join(lines) + "\n"


# ================================================================== appsettings
def _set(doc: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(":")
    for part in parts[:-1]:
        nxt = doc.setdefault(part, {})
        if not isinstance(nxt, dict):
            nxt = doc[part] = {}
        doc = nxt
    doc[parts[-1]] = value


def _substvar_value(app: Application, module: str, name: str) -> tuple[str, str] | None:
    best: tuple[int, str, str] | None = None
    for sv in app.substvars:
        if sv.name.startswith("//") and "//" in sv.name[2:]:
            mod, rest = sv.name[2:].split("//", 1)
            prec = 1
        else:
            parts = PurePosixPath(sv.source_path.replace("\\", "/")).parts
            mod = parts[parts.index("META-INF") - 1] if "META-INF" in parts and parts.index("META-INF") > 0 else ""
            rest, prec = sv.name, 0
        if rest != name or (mod and module and mod != module):
            continue
        if best is None or prec > best[0]:
            best = (prec, sv.value, sv.type)
    return (best[1], best[2]) if best else None


def _typed(value: str, typ: str) -> Any:
    if typ.lower() in ("integer", "int", "long") and re.fullmatch(r"-?\d+", value or ""):
        return int(value)
    if typ.lower() == "boolean" and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _npgsql_from_jdbc(url: str, user: str) -> str:
    m = re.match(r"jdbc:postgresql://([^:/]+)(?::(\d+))?/([^?;]+)", url or "")
    host, port, db = (m.group(1), m.group(2) or "5432", m.group(3)) if m else ("localhost", "5432", "postgres")
    return f"Host={host};Port={port};Database={db};Username={user or 'postgres'};Password=change-me-local"


def _references(pg: ProcGen, name: str) -> bool:
    """True when a process step reads the substitution variable (``%%NAME%%``, a global variable path...)."""
    text = json.dumps([[a.config, [b.expression for b in a.input_bindings]] for a in pg.p.activities],
                      sort_keys=True, default=str)
    return re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", text) is not None


def _not_carried_reason(sol: SolutionGen, pg: ProcGen, substvar: str, connections: set[str]) -> str:
    resources = list(sol.application.resources)
    if substvar.startswith("resource:"):
        rname = substvar[len("resource:"):].rpartition(".")[0]
        res = next((r for r in resources if r.name == rname), None)
        if res is not None and res.kind == "jms":
            return "JMS connection setting: replaced by Azure Service Bus (Messaging:*)"
        if res is not None and res.kind == "jdbc":
            return f"JDBC setting: replaced by ConnectionStrings:{ident(rname, 'Resource')}"
        return "resource setting not read by the generated code"
    bound_to = [(res, field_name) for res in resources
                for field_name, var in ((res.settings.get("substitution_bindings") or {}) if isinstance(res.settings, dict) else {}).items()
                if var == substvar]
    used = [(res, f) for res, f in bound_to if res.kind != "jdbc" or ident(res.name, "Resource") in connections]
    for res, field_name in used or bound_to:
        if res.kind == "jms":
            return "JMS connection setting: replaced by Azure Service Bus (Messaging:*)"
        if res.kind == "jdbc":
            db = ident(res.name, "Resource")
            return f"JDBC setting: replaced by ConnectionStrings:{db}" if db in connections else \
                "JDBC setting of a resource the selected processes do not use"
        if res.kind == "http" and res.settings.get("role") == "server" and field_name in ("port", "host"):
            return "HTTP listener address: set by the host (ASPNETCORE_URLS, container ingress)"
        if res.kind == "http":
            return f"HTTP client setting: carried as Clients:{ident(res.name)}:BaseAddress"
    return "not read by the generated code"


def gen_appsettings(sol: SolutionGen, pgs: list[ProcGen], clients: dict[str, dict[str, Any]]
                    ) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, str]]]:
    """(appsettings.json, appsettings.Development.json, Key Vault secrets (name, key), substvars not carried).

    Production holds non-secret keys only, plus placeholders for KeyVault:Uri and the Service Bus
    namespace; secrets arrive from Key Vault (``--`` for ``:``). Development holds local values.
    """
    app = sol.application
    prod: dict[str, Any] = {
        "Logging": {"LogLevel": {"Default": "Information", "Microsoft.AspNetCore": "Warning"}},
        "AllowedHosts": "*",
        "Auth": {"Mode": "Production", "EntraId": {"Instance": "https://login.microsoftonline.com/",
                                                   "TenantId": "<entra-tenant-id>",
                                                   "Audience": "api://<app-registration-client-id>"}},
        "Swagger": {"Enabled": False},
        "Messaging": {"Mode": "ServiceBus",
                      "ServiceBus": {"FullyQualifiedNamespace": "<service-bus-namespace>.servicebus.windows.net"}},
        "KeyVault": {"Uri": "https://<key-vault-name>.vault.azure.net/"},
        "Jobs": {"Enabled": True},
    }
    seed = hashlib.sha256(f"exiqo-dev:{app.id}".encode()).hexdigest()
    dev: dict[str, Any] = {
        "Logging": {"LogLevel": {"Default": "Information", "Microsoft.AspNetCore": "Information"}},
        "Auth": {"Mode": "Development", "DevToken": {"Issuer": "exiqo-dev",
                                                     "Audience": f"{sol.app_name.lower()}-api",
                                                     "SigningKey": f"development-only-{seed}"}},
        "Swagger": {"Enabled": True},
        "Messaging": {"Mode": "InMemory"},
        "KeyVault": {"Uri": ""},
        "ConnectionStrings": {},
        "Jobs": {"Enabled": False},
    }
    connections = {pg.connections[x] for pg in pgs for x in pg.connections}
    resources = {r.name: r for r in app.resources}
    secrets: dict[str, str] = {}
    not_carried: dict[str, str] = {}
    for pg in pgs:
        for row in pg.design.config:
            if row.key.startswith("ConnectionStrings:"):
                db = row.key.split(":", 1)[1]
                if db not in connections:
                    continue
                res = resources.get(row.substvar[len("resource:"):].rpartition(".")[0])
                settings = res.settings if res else {}
                secrets[row.key.replace(":", "--")] = row.key
                _set(dev, row.key, _npgsql_from_jdbc(str(settings.get("jdbc_url") or ""),
                                                     str(settings.get("username") or "")))
                continue
            if row.substvar.startswith("resource:") or not _references(pg, row.substvar):
                not_carried.setdefault(row.substvar, _not_carried_reason(sol, pg, row.substvar, connections))
                continue
            found = _substvar_value(app, pg.p.module, row.substvar)
            value, typ = found if found else ("", "String")
            local = _typed(value, typ) if value else "change-me-local"
            if row.secret:
                secrets[row.key.replace(":", "--")] = row.key
                _set(dev, row.key, local)
            else:
                _set(prod, row.key, local)
        if pg.kind == "timer":
            _set(prod, f"Jobs:{pg.name}:Interval", _interval(pg.starter.config))
            if pg.starter.config.get("start"):
                _set(prod, f"Jobs:{pg.name}:Start", str(pg.starter.config.get("start")))
    for name, info in sorted(clients.items()):
        _set(prod, f"Clients:{name}:BaseAddress", info["base"])
    if not dev["ConnectionStrings"]:
        del dev["ConnectionStrings"]
    return (json.dumps(prod, indent=2, ensure_ascii=False) + "\n", json.dumps(dev, indent=2, ensure_ascii=False) + "\n",
            sorted(secrets.items()), sorted(not_carried.items()))


# ===================================================================== driver
def solution_layout(app_name: str) -> dict[str, Any]:
    """Paths of a generated solution, the layout contract with the infrastructure generator (Dockerfile,
    pipelines): projects live under ``src/<App>.X/`` and ``tests/<App>.Tests/``."""
    projects: dict[str, dict[str, str]] = {}
    for kind, suffix, base in (("api", "Api", "src"), ("core", "Core", "src"),
                               ("infrastructure", "Infrastructure", "src"), ("tests", "Tests", "tests")):
        name = f"{app_name}.{suffix}"
        projects[kind] = {"name": name, "folder": f"{base}/{name}", "csproj": f"{base}/{name}/{name}.csproj"}
    return {"solution": f"{app_name}.sln", "projects": projects, "api_project": projects["api"]["csproj"],
            "api_assembly": f"{app_name}.Api", "api_dll": f"{app_name}.Api.dll",
            "test_project": projects["tests"]["csproj"]}



def _as_map(value: Any, process_ids: list[str], process_of: Any) -> dict[str, list[Any]]:
    """Per-process lists from a dict keyed by process id, or from a flat list (assigned by citations)."""
    if isinstance(value, dict):
        return {pid: list(value.get(pid) or []) for pid in process_ids}
    out: dict[str, list[Any]] = {pid: [] for pid in process_ids}
    for item in value or []:
        for pid in process_of(item):
            if pid in out:
                out[pid].append(item)
    return out


def _designs(app: Application, process_ids: list[str], design: Any, rules: dict[str, list[Rule]],
             findings: dict[str, list[Finding]]) -> tuple[dict[str, DesignRecord], dict[str, Any]]:
    from engine.domains import graph as graph_mod
    from engine.dotnet_gen.design import build_design_skeleton

    given: dict[str, DesignRecord] = {}
    if isinstance(design, DesignRecord):
        given[design.process_id] = design
    elif isinstance(design, dict):
        given.update(design)
    elif design:
        given.update({d.process_id: d for d in design})
    procs = {p.id: p for p in app.processes}
    graph = graph_mod.build_graph([app])
    cards: dict[str, Any] = {}
    out: dict[str, DesignRecord] = {}
    for pid in process_ids:
        card = graph_mod.io_card(procs[pid], app, graph)
        cards[pid] = card.model_dump(mode="json") if hasattr(card, "model_dump") else card
        out[pid] = given.get(pid) or build_design_skeleton(procs[pid], app, rules.get(pid, []),
                                                           findings.get(pid, []), card)
    return out, cards


def generate_solution(app: Application, process_ids: list[str], design: DesignRecord | dict[str, DesignRecord] | list[DesignRecord],
                      rules: dict[str, list[Rule]] | list[Rule], findings: dict[str, list[Finding]] | list[Finding],
                      scenarios: list[Scenario], out_dir: Path) -> GeneratedSolution:
    """Generate the solution for ``process_ids`` of ``app`` into ``out_dir`` (the solution root).

    ``design`` is the design record of each process (a dict keyed by process id, a list, or one
    record); a missing record is built with ``build_design_skeleton``. ``rules`` and ``findings``
    are keyed by process id or flat lists (assigned to the processes their citations name).
    Files from an earlier generation that are no longer produced are removed (``.exiqo`` manifest);
    the infrastructure paths in ``RESERVED_FOR_INFRA`` are never touched.
    """
    if not process_ids:
        raise ValueError("process_ids is empty")
    procs = {p.id: p for p in app.processes}
    missing = [pid for pid in process_ids if pid not in procs]
    if missing:
        raise KeyError(f"unknown process ids: {missing}")

    def cited(item: Any) -> list[str]:
        acts = {c.activity_id for c in getattr(item, "citations", [])}
        return [pid for pid in process_ids if acts & {a.id for a in procs[pid].activities}]

    rules_map = _as_map(rules, process_ids, cited)
    findings_map = _as_map(findings, process_ids, cited)
    designs, cards = _designs(app, process_ids, design, rules_map, findings_map)
    sol = SolutionGen(app, process_ids, designs, cards)
    a = sol.app_name
    pgs = [ProcGen(sol, procs[pid], designs[pid], rules_map[pid], findings_map[pid],
                   [s for s in scenarios if s.process_id == pid]) for pid in process_ids]

    files: dict[str, GenFile] = {}

    def add(path: str, content: str, origin: Origin = "code", activity_id: str | None = None,
            process_id: str | None = None) -> None:
        if any(path == r or path.startswith(r) for r in RESERVED_FOR_INFRA):
            raise ValueError(f"{path} is reserved for the infrastructure generator")
        if path in files:  # shared file (an exception or stub two processes need): first writer wins
            update: dict[str, Any] = {}
            if origin == "agent" and files[path].origin == "code":
                update["origin"] = "agent"
            if files[path].process_id != process_id:
                update["process_id"] = None  # needed by several processes: shared
                update["activity_id"] = None
            if update:
                files[path] = files[path].model_copy(update=update)
            return
        files[path] = GenFile(path=path, content=content, origin=origin, activity_id=activity_id,
                              process_id=process_id)

    layout = solution_layout(a)
    folders = {k: v["folder"] for k, v in layout["projects"].items()}
    core, infra, api, tests = folders["core"], folders["infrastructure"], folders["api"], folders["tests"]
    projects = [(v["name"], v["csproj"]) for v in layout["projects"].values()]
    sln_projects = [{"name": n, "win_path": p.replace("/", "\\"),
                     "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"exiqo:{app.id}:{n}")).upper()}
                    for n, p in projects]
    add(f"{a}.sln", render("sln.j2", projects=sln_projects))
    add("Directory.Build.props", render("Directory.Build.props.j2"))
    add("Directory.Packages.props", render("Directory.Packages.props.j2", packages=PACKAGES))
    add("nuget.config", render("nuget.config.j2"))
    add(".editorconfig", render("editorconfig.j2"))
    add(".gitignore", render("gitignore.j2"))

    # ---- Core
    add(f"{core}/{a}.Core.csproj", render("Core.csproj.j2", app=a))
    add(f"{core}/Contracts/TibcoPathAttribute.cs", render("TibcoPathAttribute.cs.j2", app=a))
    add(f"{core}/Tibco/TibValue.cs", render("TibValue.cs.j2", app=a))
    add(f"{core}/Tibco/AgentFill.cs", render("AgentFill.cs.j2", app=a))
    add(f"{core}/Tibco/ProcessFlow.cs", render("ProcessFlow.cs.j2", app=a))
    add(f"{core}/Contracts/Masking.cs", render("Masking.cs.j2", app=a, names=sorted(SENSITIVE_NAMES)))
    add(f"{core}/Messaging/IMessageBus.cs", render("IMessageBus.cs.j2", app=a))
    add(f"{core}/Services/ProcessResult.cs", render("ProcessResult.cs.j2", app=a))
    add(f"{core}/Exceptions/ProcessFaultException.cs", render("ProcessFaultException.cs.j2", app=a))
    add(f"{core}/Diagnostics/IStepLogger.cs", render("IStepLogger.cs.j2", app=a))
    contract_ids = sorted({cm.contract_id for pg in pgs for cm in pg.design.contracts})
    plans = csharp_contracts.plan_contracts(a, app.contracts)
    by_id = {c.id: c for c in app.contracts}
    for cid in contract_ids:
        plan = plans[cid]
        add(f"{core}/{plan.file}", mask_records(csharp_contracts.to_records(by_id[cid], plan.namespace, plan.reserved)))

    selected_names = {procs[pid].name for pid in process_ids}
    repositories, services, consumers, jobs = [], [], [], []
    connections: set[str] = set()
    generic: dict[tuple[str, str, str], list[tuple[str, str, str]]] = defaultdict(list)
    client_members: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    clients: dict[str, dict[str, Any]] = {}
    owners: dict[str, set[str]] = defaultdict(set)  # aggregated class -> processes contributing to it

    def owner(key: str) -> str | None:
        return next(iter(owners[key])) if len(owners[key]) == 1 else None
    resilience = {r.resource_id: r for pg in pgs for r in pg.design.resilience}
    for pg in pgs:
        n = pg.name
        add(f"{core}/Services/{n}State.cs", _masked(gen_state(pg), a), process_id=pg.p.id)
        service, service_origin = gen_service(pg)
        add(f"{core}/Services/{n}Service.cs", service, service_origin, process_id=pg.p.id)
        services.append(f"{n}Service")
        if pg.map_row:
            text, origin = gen_mappings(pg)
            add(f"{core}/Mappings/{n}Mappings.cs", text, origin, process_id=pg.p.id)
        if any(t.kind == "conditional" for t in pg.p.transitions):
            text, origin = gen_conditions(pg)
            add(f"{core}/Rules/{n}Conditions.cs", text, origin, process_id=pg.p.id)
        if any(act.type.startswith("jdbc.") for act in pg.p.activities):
            add(f"{core}/Data/{n}Data.cs", _masked(gen_data(pg), a), process_id=pg.p.id)
            add(f"{infra}/Repositories/{n}Repository.cs", gen_repository(pg),
                "agent" if any(act.type == "jdbc.call" for act in pg.p.activities) else "code", process_id=pg.p.id)
            repositories.append((f"I{n}Repository", f"{n}Repository"))
            connections |= {pg.connections[x] for x in pg.connections}
        for act in pg.p.activities:
            row = pg.primary.get(act.id)
            if act.type == "throw":
                add(f"{core}/Exceptions/{_exception_class(pg, act)}.cs", gen_exception(pg, act), "code", act.id, process_id=pg.p.id)
            elif act.type == "call.process" and row:
                target = next((c.target_process for c in pg.p.calls if c.activity_id == act.id), "")
                tname = procs[target].name if target in procs else (target.rsplit(".", 1)[-1] or act.name)
                if tname not in selected_names:
                    add(f"{core}/{row.file}", gen_subprocess_stub(a, ident(tname, "Process"), target or act.name),
                        "agent", act.id, process_id=pg.p.id)
            elif act.type in _INVOKE and row:
                client_members[row.class_name].append((row.method, act.name, pg.remarks(act.id, row)))
                owners[f"client:{row.class_name}"].add(pg.p.id)
                ref = str(act.config.get("resource_ref") or "")
                res = sol.resources.get(ref)
                settings = res.settings if res else {}
                host = _substvar_value(app, pg.p.module, str((settings.get("substitution_bindings") or {}).get("host") or ""))
                port = settings.get("port")
                base = f"http://{host[0] if host else 'localhost'}:{port}/" if port else f"http://{host[0] if host else 'localhost'}/"
                rmap = resilience.get(res.id) if res else None
                lines: list[str] = []
                if rmap and rmap.timeout_ms:
                    lines.append(f"options.AttemptTimeout.Timeout = TimeSpan.FromMilliseconds({rmap.timeout_ms});")
                if rmap and rmap.breaker:
                    if rmap.breaker.get("failure_rate") is not None:
                        lines.append(f"options.CircuitBreaker.FailureRatio = {int(rmap.breaker['failure_rate']) / 100:.2f};")
                    if rmap.breaker.get("window") is not None:
                        lines.append(f"options.CircuitBreaker.MinimumThroughput = {max(2, int(rmap.breaker['window']))};")
                    if rmap.breaker.get("break_ms") is not None:
                        lines.append(f"options.CircuitBreaker.BreakDuration = TimeSpan.FromMilliseconds({int(rmap.breaker['break_ms'])});")
                clients[row.class_name] = {"name": row.class_name, "base_key": f"Clients:{row.class_name}:BaseAddress",
                                           "base": base, "resilience": lines}
            elif act.type in _GENERIC and row:
                proj = "core" if row.project.endswith(".Core") else "infrastructure"
                generic[(proj, row.file, row.class_name)].append(
                    (ident(row.method.split("(")[0], "Run"), act.name, pg.remarks(act.id, row)))
                owners[f"generic:{proj}:{row.file}"].add(pg.p.id)
        if pg.kind == "rest" or pg.kind == "http":
            add(f"{api}/Controllers/{n}Controller.cs", gen_controller(pg), "code", pg.starter.activity_id, process_id=pg.p.id)
        elif pg.kind == "soap":
            add(f"{api}/Soap/{n}SoapService.cs", gen_soap_stub(pg), "agent", pg.starter.activity_id, process_id=pg.p.id)
        elif pg.kind == "jms":
            text, origin = gen_consumer(pg)
            add(f"{infra}/Messaging/{n}Consumer.cs", text, origin, pg.starter.activity_id, process_id=pg.p.id)
            consumers.append(f"{n}Consumer")
        elif pg.kind == "timer":
            add(f"{infra}/Jobs/{n}Job.cs", gen_job(pg), "code", pg.starter.activity_id, process_id=pg.p.id)
            jobs.append(f"{n}Job")
    for (proj, file, cls), members in sorted(generic.items()):
        seen: dict[str, int] = {}
        uniq = []
        for method, act_name, remarks in members:
            seen[method] = seen.get(method, 0) + 1
            uniq.append((method if seen[method] == 1 else f"{method}{seen[method]}", act_name, remarks))
        folder = str(PurePosixPath(file).parent).replace("/", ".")
        folder = "" if folder == "." else folder
        root_ns = f"{a}.Core" if proj == "core" else f"{a}.Infrastructure"
        base = core if proj == "core" else infra
        add(f"{base}/{file}", gen_agent_class(a, root_ns, folder, cls, uniq), "agent",
            process_id=owner(f"generic:{proj}:{file}"))
    for cls, members in sorted(client_members.items()):
        add(f"{infra}/Clients/{cls}.cs", gen_client(a, cls, members), "agent", process_id=owner(f"client:{cls}"))

    # ---- Infrastructure
    infra_packages = ["Azure.Identity", "Azure.Messaging.ServiceBus", "Dapper", "Npgsql"]
    if any(c["resilience"] for c in clients.values()):
        infra_packages.append("Microsoft.Extensions.Http.Resilience")
    add(f"{infra}/{a}.Infrastructure.csproj", render("Infrastructure.csproj.j2", app=a, packages=infra_packages))
    add(f"{infra}/Messaging/InMemoryMessageBus.cs", render("InMemoryMessageBus.cs.j2", app=a))
    add(f"{infra}/Messaging/ServiceBusMessageBus.cs", render("ServiceBusMessageBus.cs.j2", app=a))
    add(f"{infra}/Diagnostics/LoggerStepLogger.cs", render("LoggerStepLogger.cs.j2", app=a))
    di_usings = ["Azure.Identity", "Azure.Messaging.ServiceBus", "Dapper", f"{a}.Core.Diagnostics", f"{a}.Core.Messaging",
                 f"{a}.Core.Services", f"{a}.Infrastructure.Diagnostics", f"{a}.Infrastructure.Messaging",
                 "Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
                 "Microsoft.Extensions.DependencyInjection.Extensions", "Npgsql"]
    if repositories:
        di_usings += [f"{a}.Core.Data", f"{a}.Infrastructure.Repositories"]
    if jobs:
        di_usings.append(f"{a}.Infrastructure.Jobs")
    if clients:
        di_usings.append(f"{a}.Infrastructure.Clients")
    system = sorted(u for u in set(di_usings) if u.startswith("System"))
    other = sorted(u for u in set(di_usings) if not u.startswith("System"))
    add(f"{infra}/InfrastructureServiceCollectionExtensions.cs",
        render("DependencyInjection.cs.j2", app=a, usings=system + other, connections=sorted(connections),
               repositories=repositories, services=services, consumers=consumers, jobs=jobs,
               clients=[clients[k] for k in sorted(clients)]))

    # ---- Api
    add(f"{api}/{a}.Api.csproj", render("Api.csproj.j2", app=a, app_lower=a.lower()))

    add(f"{api}/Auth/DevToken.cs", render("DevToken.cs.j2", app=a))
    add(f"{api}/Errors/FaultExceptionHandler.cs", render("FaultExceptionHandler.cs.j2", app=a))
    prod, dev, secrets, not_carried = gen_appsettings(sol, pgs, clients)
    add(f"{api}/Program.cs", render("Program.cs.j2", app=a, app_id=app.id, title=f"{a} API", secrets=secrets,
                                    not_carried=not_carried))
    add(f"{api}/appsettings.json", prod)
    add(f"{api}/appsettings.Development.json", dev)

    # ---- Tests (written before the code bodies)
    add(f"{tests}/{a}.Tests.csproj", render("Tests.csproj.j2", app=a))
    sql_texts: dict[str, str] = {}
    estate = _estate_root(app)
    for rel in app.sql_files:
        path = (estate / rel) if estate else None
        if path and path.is_file():
            sql_texts[PurePosixPath(rel).name] = path.read_text(encoding="utf-8")
    for name, text in sql_texts.items():
        add(f"{tests}/Database/{name}", text)
    schemas = sorted({m.lower() for t in sql_texts.values()
                      for m in re.findall(r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_]\w*)\.", t)})
    for f in tests_gen.generate_test_support(a, schemas, list(sql_texts), sorted(connections)):
        add(f.path, f.content, f.origin)
    for pg in pgs:
        ctx = tests_gen.TestContext.from_proc(pg, sql_texts, schemas)
        for f in tests_gen.generate_tests(pg.p, pg.rules, pg.scenarios, pg.design, ctx):
            add(f.path, f.content, f.origin, f.activity_id, process_id=pg.p.id)

    ordered = [files[k] for k in sorted(files)]
    digest = hashlib.sha256()
    for f in ordered:
        digest.update(f.path.encode("utf-8") + b"\0" + f.content.encode("utf-8") + b"\0")
    solution = GeneratedSolution(root=Path(out_dir).resolve(), name=a, files=ordered, build_hash=digest.hexdigest())
    write_solution(solution)
    return solution


def _estate_root(app: Application) -> Path | None:
    from engine.parity.seed import estate_root
    return estate_root(app)


def write_solution(solution: GeneratedSolution) -> None:
    """Write every file; remove files a previous generation wrote that are no longer generated."""
    root = solution.root
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST
    new = {f.path for f in solution.files}
    if manifest.is_file():
        for old in manifest.read_text(encoding="utf-8").splitlines():
            old = old.strip()
            if not old or old in new or old.startswith("/") or ".." in PurePosixPath(old).parts:
                continue
            if any(old == r or old.startswith(r) for r in RESERVED_FOR_INFRA):
                continue
            stale = root / old
            if stale.is_file():
                stale.unlink()
    for f in solution.files:
        target = root / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8", newline="\n")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(sorted(new)) + "\n", encoding="utf-8")


# ======================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    """``python -m engine.dotnet_gen.generator <estate> --app <id> --out <dir> [--process id ...]``.

    Generates without rules or scenarios (static findings undecided); used by ``make nuget-cache``.
    """
    from engine.analysis.static_rules import run_static_rules
    from engine.tibco_parser.scanner import scan_folder

    ap = argparse.ArgumentParser(prog="python -m engine.dotnet_gen.generator")
    ap.add_argument("estate", type=Path)
    ap.add_argument("--app", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--process", action="append", default=[])
    args = ap.parse_args(argv)
    apps = {x.id: x for x in scan_folder(args.estate).applications}
    app = apps[args.app]
    pids = args.process or [p.id for p in app.processes]
    findings = {p.id: run_static_rules(p, app.contracts, app.substvars) for p in app.processes if p.id in pids}
    sol = generate_solution(app, pids, {}, {}, findings, [], args.out)
    print(f"{sol.name}: {len(sol.files)} files in {sol.root} (build hash {sol.build_hash[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
