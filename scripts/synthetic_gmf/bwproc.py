"""BusinessWorks 6 process (.bwp) builder."""
from __future__ import annotations

import re

from .bwlib import XPATH_LANG, XSLT_LANG, N, Raw, render, xpdl, xslt
from .diagram import diagram_lines

BPWS = "http://docs.oasis-open.org/wsbpel/2.0/process/executable"
ENGINE = "http://www.tibco.com/pe/EngineTypes"
REST_NS = "http://tns.tibco.com/bw/REST"
JDBC_COMMON = "http://www.tibco.com/namespaces/tnt/plugins/jdbc"
LOG_NS = "http://www.tibco.com/pe/WriteToLogActivitySchema"
TIMER_NS = "http://tns.tibco.com/bw/activity/timer/xsd/output"
XSD = "http://www.w3.org/2001/XMLSchema"
PRODUCT = "6.5.0 V63 2018-08-08"

SR_JDBC = "{http://xsd.tns.tibco.com/amf/models/sharedresource/jdbc}JdbcDataSource"
SR_HTTPCLIENT = "{http://xsd.tns.tibco.com/bw/models/sharedresource/httpclient}HttpClientConfiguration"
SR_JMS = "{http://xsd.tns.tibco.com/bw/models/sharedresource/jms}JMSConnectionFactory"

EXT_NAMESPACES = [
    "http://www.eclipse.org/gmf/runtime/1.0.2/notation",
    "http://www.tibco.com/bw/process/info",
    "http://docs.oasis-open.org/ns/opencsa/sca-bpel/200801",
    "http://docs.oasis-open.org/ns/opencsa/sca/200912",
    "http://ns.tibco.com/bw/property",
    "http://www.tibco.com/bpel/2007/extensions",
]
BWEXT_NS = {
    "xmlns:activityconfig": "http://tns.tibco.com/bw/model/activityconfig",
    "xmlns:bwext": "http://tns.tibco.com/bw/model/core/bwext",
    "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

ENGINE_TYPES = f'''
<xs:schema attributeFormDefault="unqualified"
    elementFormDefault="qualified"
    targetNamespace="{ENGINE}"
    xmlns:tns="{ENGINE}" xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:complexType block="extension restriction"
        final="extension restriction" name="ErrorReport">
        <xs:sequence>
            <xs:element form="unqualified" name="StackTrace" type="xs:string"/>
            <xs:element form="unqualified" name="Msg" type="xs:string"/>
            <xs:element form="unqualified" name="FullClass" type="xs:string"/>
            <xs:element form="unqualified" name="Class" type="xs:string"/>
            <xs:element form="unqualified" name="ProcessStack" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="MsgCode" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="Data" type="tns:anydata"/>
        </xs:sequence>
    </xs:complexType>
    <xs:complexType block="extension restriction"
        final="extension restriction" name="FaultDetail">
        <xs:sequence>
            <xs:element form="unqualified" name="ActivityName" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="Data" type="tns:anydata"/>
            <xs:element form="unqualified" name="Msg" type="xs:string"/>
            <xs:element form="unqualified" name="MsgCode" type="xs:string"/>
            <xs:element form="unqualified" name="ProcessStack" type="xs:string"/>
            <xs:element form="unqualified" name="StackTrace" type="xs:string"/>
            <xs:element form="unqualified" name="FullClass" type="xs:string"/>
            <xs:element form="unqualified" name="Class" type="xs:string"/>
        </xs:sequence>
    </xs:complexType>
    <xs:complexType block="extension restriction"
        final="extension restriction" name="ProcessContext">
        <xs:sequence>
            <xs:element form="unqualified" name="JobId" type="xs:string"/>
            <xs:element form="unqualified" name="ApplicationName" type="xs:string"/>
            <xs:element form="unqualified" name="EngineName" type="xs:string"/>
            <xs:element form="unqualified" name="ProcessInstanceId" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="CustomJobId" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="TrackingInfo" type="xs:string"/>
        </xs:sequence>
    </xs:complexType>
    <xs:complexType name="anydata">
        <xs:sequence>
            <xs:any namespace="##any" processContents="lax"/>
        </xs:sequence>
    </xs:complexType>
    <xs:element name="ErrorReport" type="tns:ErrorReport"/>
    <xs:element name="FaultDetail" type="tns:FaultDetail"/>
    <xs:element name="ProcessContext" type="tns:ProcessContext"/>
    <xs:element name="CorrelationValue" type="xs:string"/>
</xs:schema>
'''

SQL_TYPES = {
    "VARCHAR": (12, "xsd:string"),
    "CHAR": (1, "xsd:string"),
    "INTEGER": (4, "xsd:int"),
    "NUMERIC": (2, "xsd:decimal"),
    "DATE": (91, "xsd:date"),
    "TIMESTAMP": (93, "xsd:dateTime"),
    "BOOLEAN": (16, "xsd:boolean"),
}
PG_TYPE = {"VARCHAR": "varchar", "CHAR": "bpchar", "INTEGER": "int4", "NUMERIC": "numeric",
           "DATE": "date", "TIMESTAMP": "timestamp", "BOOLEAN": "bool"}


def _xsd_type(t):
    return SQL_TYPES[t][1].replace("xsd:", "xs:")


class Proc:
    def __init__(self, module, name, ts, *, callable_, created_on, description="",
                 created_by="bwdev01"):
        self.module = module
        self.name = name
        self.ts = ts
        self.tns_uri = f"http://xmlns.example.com/{ts}"
        self.callable = callable_
        self.created_on = created_on
        self.created_by = created_by
        self.description = description
        self.root_ns: dict[str, str] = {}
        self.types: list = [Raw(ENGINE_TYPES)]
        self.imports: list[tuple[str, str]] = []
        self.variables: list[N] = []
        self.registry: dict[str, str] = {}  # uri -> tnsN
        self.partner_links: list[N] = []
        self.interface = ("", "")
        self.body: N | None = None
        self.var("_processContext", element=self.ns(ENGINE, "ProcessContext"))

    # namespaces -----------------------------------------------------------
    def ns(self, uri: str, local: str | None = None) -> str:
        if uri == ENGINE:
            prefix = "ns"
        else:
            for p, u in self.root_ns.items():
                if u == uri:
                    prefix = p
                    break
            else:
                prefix = f"ns{len([p for p in self.root_ns if p != 'ns'])}"
        self.root_ns[prefix] = uri
        return f"{prefix}:{local}" if local else prefix

    def tns(self, uri: str) -> str:
        if uri not in self.registry:
            n = len(self.registry)
            self.registry[uri] = "tns" if n == 0 else f"tns{n}"
        return self.registry[uri]

    def prefixes(self) -> dict[str, str]:
        out = {p: u for u, p in self.registry.items()}
        out["xsd"] = XSD
        return out

    def map(self, params, template, body) -> str:
        return xslt(self.prefixes(), params, template, body)

    def import_xsd(self, uri: str):
        if ("http://www.w3.org/2001/XMLSchema", uri) not in self.imports:
            self.imports.append(("http://www.w3.org/2001/XMLSchema", uri))

    # variables ------------------------------------------------------------
    def var(self, name, *, element=None, message=None, type_=None, parameter=None):
        attrs = {"name": name, "sca-bpel:internal": "true"}
        if element:
            attrs["element"] = element
        if message:
            attrs["messageType"] = message
        if type_:
            attrs["type"] = type_
        if parameter:
            attrs["tibex:parameter"] = parameter
        self.variables.append(N("bpws:variable", attrs))

    def prop(self, name, resource, sr_type):
        self.variables.append(N("bpws:variable", {
            "name": name, "sca-bpel:hotUpdate": "false", "sca-bpel:privateProperty": "true",
            "sca-bpel:property": "yes", "sca-bpel:sharedResourceType": sr_type,
            "type": "xsd:string"},
            [N("bpws:from", children=[N("bpws:literal", text=resource)])]))

    def add_type(self, text: str):
        self.types.append(Raw(text))

    # document ---------------------------------------------------------------
    def document(self) -> str:
        root_attrs = {
            "exitOnStandardFault": "no", "name": self.name, "suppressJoinFailure": "yes",
            "targetNamespace": self.tns_uri,
            "xmlns:bpws": BPWS,
            "xmlns:info": "http://www.tibco.com/bw/process/info",
            "xmlns:sca": "http://docs.oasis-open.org/ns/opencsa/sca/200912",
            "xmlns:sca-bpel": "http://docs.oasis-open.org/ns/opencsa/sca-bpel/200801",
            "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions",
            "xmlns:tibprop": "http://ns.tibco.com/bw/property",
            "xmlns:xsd": XSD,
        }
        for p, u in self.root_ns.items():
            root_attrs[f"xmlns:{p}"] = u
        children = [
            N("tibex:Types", children=self.types),
            N("tibex:ProcessInfo", {
                "callable": "true" if self.callable else "false",
                "createdBy": self.created_by, "createdOn": self.created_on,
                "description": self.description, "extraErrorVars": "true",
                "modifiers": "public", "productVersion": PRODUCT, "scalable": "true",
                "singleton": "true", "stateless": "true", "type": "IT"}),
            N("tibex:ProcessInterface", {"context": "", "input": self.interface[0],
                                         "output": self.interface[1]}),
            N("tibex:ProcessTemplateConfigurations"),
            Raw("<!--DIAGRAM-->"),
            N("tibex:NamespaceRegistry", {"enabled": "true"}, [
                N("tibex:namespaceItem", {"namespace": u, "prefix": p})
                for u, p in self.registry.items()]),
        ]
        children += [N("bpws:import", {"importType": t, "namespace": u})
                     for t, u in self.imports]
        if self.partner_links:
            children.append(N("bpws:partnerLinks", children=self.partner_links))
        children.append(N("bpws:variables", children=self.variables))
        children.append(N("bpws:extensions", children=[
            N("bpws:extension", {"mustUnderstand": "no", "namespace": u})
            for u in EXT_NAMESPACES]))
        children.append(self.body)
        text = render(N("bpws:process", root_attrs, children))
        return text.replace("    <!--DIAGRAM-->", "\n".join(diagram_lines(text)))

    def xid(self, activity: str) -> str:
        return xpdl(f"{self.name}/{activity}")


# --- flows and links -----------------------------------------------------------


class Flow:
    def __init__(self, name):
        self.name = name
        self.acts: list = []
        self.links: list = []

    def add(self, name, build):
        self.acts.append((name, build))
        return name

    def link(self, a, b, kind="SUCCESS", cond=None, label=None):
        name = f"{a}To{b}"
        self.links.append((name, a, b, kind, cond, label))
        return name

    def node(self) -> N:
        link_nodes = []
        for name, _a, _b, kind, _c, label in self.links:
            attrs = {"name": name, "tibex:linkType": kind}
            if label is not None:
                attrs["tibex:label"] = label
            link_nodes.append(N("bpws:link", attrs))
        children = [N("bpws:links", children=link_nodes)]
        for name, build in self.acts:
            targets = [ln for ln, _a, b, *_ in self.links if b == name]
            sources = [(ln, kind, cond) for ln, a, _b, kind, cond, _l in self.links if a == name]
            children.append(build(targets, sources))
        return N("bpws:flow", {"name": self.name}, children)


def targets_node(targets):
    if not targets:
        return None
    return N("bpws:targets", children=[N("bpws:target", {"linkName": t}) for t in targets])


def sources_node(sources):
    if not sources:
        return None
    items = []
    for name, kind, cond in sources:
        expr = cond if kind == "SUCCESSWITHCONDITION" else (
            "##otherwise##" if kind == "SUCCESSWITHNOCONDITION" else None)
        if expr is None:
            items.append(N("bpws:source", {"linkName": name}))
            continue
        items.append(N("bpws:source", {"linkName": name}, [
            N("tibex:DesignExpression", children=[
                N("tibex:expression", {"expression": expr, "expressionLanguage": XPATH_LANG})]),
            N("bpws:transitionCondition", {"expressionLanguage": XPATH_LANG}, cdata=expr),
        ]))
    return N("bpws:sources", children=items)


def input_bindings(xsl: str | None, tag="tibex:inputBinding"):
    if xsl is None:
        return None
    return N("tibex:inputBindings", children=[
        N(tag, {"expression": xsl, "expressionLanguage": XSLT_LANG})])


def bw_config(type_id, palette_prefix, palette_ns, type_href, value_attrs, value_children=(),
              extra_ns=None, version=None):
    attrs = {"activityTypeID": type_id, **BWEXT_NS, f"xmlns:{palette_prefix}": palette_ns}
    if version:
        attrs["version"] = version
    for k, v in (extra_ns or {}).items():
        attrs[f"xmlns:{k}"] = v
    return N("bwext:BWActivity", attrs, [
        N("activityConfig", children=[
            N("properties", {"name": "config", "xsi:type": "activityconfig:EMFProperty"}, [
                N("type", {"href": type_href}),
                N("value", value_attrs, list(value_children)),
            ])])])


# --- activity builders (each returns build(targets, sources) -> N) -----------


def start_event(p: Proc, name="Start", variable="Start"):
    def build(targets, sources):
        return N("bpws:extensionActivity", children=[
            N("tibex:receiveEvent", {
                "createInstance": "yes", "eventTimeout": "0", "name": name,
                "tibex:xpdlId": p.xid(name), "variable": variable,
                "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"}, [
                sources_node(sources),
                N("tibex:eventSource", children=[
                    N("tibex:StartEvent", {"xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"})]),
            ])])
    return build


def event_source(p: Proc, name, variable, config: N, timeout="60"):
    def build(targets, sources):
        return N("bpws:extensionActivity", children=[
            N("tibex:receiveEvent", {
                "createInstance": "yes", "eventTimeout": timeout, "name": name,
                "tibex:xpdlId": p.xid(name), "variable": variable,
                "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"}, [
                sources_node(sources),
                N("tibex:eventSource", children=[config]),
            ])])
    return build


def ext_activity(p: Proc, name, config: N, *, input_var=None, output_var=None, binding=None):
    def build(targets, sources):
        attrs = {"name": name, "tibex:xpdlId": p.xid(name),
                 "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"}
        if input_var:
            attrs["inputVariable"] = input_var
        if output_var:
            attrs["outputVariable"] = output_var
        return N("bpws:extensionActivity", children=[
            N("tibex:activityExtension", attrs, [
                targets_node(targets), sources_node(sources), input_bindings(binding),
                N("tibex:config", children=[config])])])
    return build


def end_activity(p: Proc, binding=None, input_var="End-input"):
    cfg = bw_config("bw.internal.end", "internalactivities",
                    "http://ns.tibco.com/bw/core/internalactivity",
                    "http://ns.tibco.com/bw/core/internalactivity#//End",
                    {"xsi:type": "internalactivities:End"})
    return ext_activity(p, "End", cfg, input_var=input_var if binding else None, binding=binding)


def call_process(p: Proc, name, subprocess, binding, *, input_var, output_var=None):
    def build(targets, sources):
        attrs = {"expression": compact(binding), "expressionLanguage": XSLT_LANG,
                 "inputVariable": input_var, "name": name,
                 "tibex:xpdlId": p.xid(name), "type": "bw.generalactivities.callprocess",
                 "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"}
        if output_var:
            attrs["outputVariable"] = output_var
        return N("bpws:extensionActivity", children=[
            N("tibex:extActivity", attrs, [
                targets_node(targets), sources_node(sources), input_bindings(binding),
                N("tibex:CallProcess", {"subProcessName": subprocess,
                                        "xmlns:tibex": "http://www.tibco.com/bpel/2007/extensions"}),
            ])])
    return build


def mapper(p: Proc, name, element_qname, element_ns, binding):
    """General Activities > Mapper with a module schema element as its input."""
    prefix, local = element_qname.split(":")
    cfg = bw_config("bw.generalactivities.mapper", "generalactivities",
                    "http://ns.tibco.com/bw/palette/generalactivities",
                    "http://ns.tibco.com/bw/palette/generalactivities#//Mapper",
                    {"inputQName": f"{prefix}:{local}", "xsi:type": "generalactivities:Mapper"},
                    extra_ns={prefix: element_ns}, version="6.0.0.001")
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name, binding=binding)


def log(p: Proc, name, role, binding):
    cfg = bw_config("bw.generalactivities.log", "generalactivities",
                    "http://ns.tibco.com/bw/palette/generalactivities",
                    "http://ns.tibco.com/bw/palette/generalactivities#//Log",
                    {"controlBy": "Application", "role": role, "suppressJobInfo": "true",
                     "xsi:type": "generalactivities:Log"}, version="6.0.0.001")
    return ext_activity(p, name, cfg, input_var=f"{name}-input", binding=binding)


def jdbc_query(p: Proc, name, sql, params, columns, binding, prop="jdbcProperty", max_rows="100"):
    cfg = bw_config("bw.jdbc.JDBCQuery", "jdbcPalette", "http://ns.tibco.com/bw/palette/jdbc",
                    "http://ns.tibco.com/bw/palette/jdbc#//JDBCQueryActivity",
                    {"maxRows": max_rows, "sharedResourceProperty": prop, "sqlStatement": sql,
                     "timeout": "10", "xsi:type": "jdbcPalette:JDBCQueryActivity"},
                    [N("PreparedParameters", {"DataType": str(SQL_TYPES[t][0]),
                                              "DataTypeDisplayValue": t, "ParameterName": n})
                     for n, t in params] +
                    [N("columnMetadata", {"columnName": c, "dataType": str(SQL_TYPES[t][0]),
                                          "status": "OptionalElement", "typeName": PG_TYPE[t]})
                     for c, t in columns])
    return ext_activity(p, name, cfg, input_var=f"{name}-input" if binding else None,
                        output_var=name, binding=binding)


def jdbc_update(p: Proc, name, sql, params, binding, prop="jdbcProperty"):
    cfg = bw_config("bw.jdbc.update", "jdbcPalette", "http://ns.tibco.com/bw/palette/jdbc",
                    "http://ns.tibco.com/bw/palette/jdbc#//JDBCUpdateActivity",
                    {"sharedResourceProperty": prop, "sqlStatement": sql, "timeout": "10",
                     "xsi:type": "jdbcPalette:JDBCUpdateActivity"},
                    [N("PreparedParameters", {"DataType": str(SQL_TYPES[t][0]),
                                              "DataTypeDisplayValue": t, "ParameterName": n})
                     for n, t in params])
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name, binding=binding)


def jdbc_input_ns(p: Proc, name, kind="input"):
    return f"http://www.tibco.com/namespaces/tnt/plugins/jdbc+{p.xid(name)}+{kind}"


def declare_jdbc_query(p: Proc, name, params, columns, record_element=None):
    """Inline input/output schemas and variables for a JDBC Query activity."""
    in_ns = jdbc_input_ns(p, name, "input")
    out_ns = jdbc_input_ns(p, name, "output")
    params_xsd = "\n".join(
        f'                <element name="{n}" type="{_xsd_type(t).replace("xs:", "")}"/>'
        for n, t in params)
    cols_xsd = "\n".join(
        f'                        <element minOccurs="0" name="{c}" '
        f'type="{_xsd_type(t).replace("xs:", "")}"/>' for c, t in columns)
    extra = ""
    if record_element:
        extra = (f'\n    <element name="{record_element}" type="tns:RecordType"/>')
    if params:
        p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{in_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{in_ns}">
    <element name="jdbcQueryActivityInput">
        <complexType>
            <sequence>
{params_xsd}
            </sequence>
        </complexType>
    </element>
</schema>''')
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{out_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{out_ns}">
    <complexType name="RecordType">
        <sequence>
{cols_xsd.replace("                        ", "            ")}
        </sequence>
    </complexType>
    <element name="resultSet">
        <complexType>
            <sequence>
                <element maxOccurs="unbounded" minOccurs="0" name="Record" type="tns:RecordType"/>
            </sequence>
        </complexType>
    </element>{extra}
</schema>''')
    if params:
        p.var(f"{name}-input", element=p.ns(in_ns, "jdbcQueryActivityInput"))
    p.var(name, element=p.ns(out_ns, "resultSet"))
    return in_ns, out_ns


_jdbc_common_added = set()


def declare_jdbc_update(p: Proc, name, params):
    in_ns = jdbc_input_ns(p, name, "input")
    params_xsd = "\n".join(
        f'                <element minOccurs="0" name="{n}" '
        f'type="{_xsd_type(t).replace("xs:", "")}"/>' for n, t in params)
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{in_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{in_ns}">
    <element name="jdbcUpdateActivityInput">
        <complexType>
            <sequence>
{params_xsd}
            </sequence>
        </complexType>
    </element>
</schema>''')
    if p.name not in _jdbc_common_added:
        _jdbc_common_added.add(p.name)
        p.add_type(f'''
<xs:schema attributeFormDefault="unqualified"
    elementFormDefault="unqualified"
    targetNamespace="{JDBC_COMMON}"
    xmlns:tns="{JDBC_COMMON}" xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:element name="jdbcUpdateActivityOutput">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="noOfUpdates" type="xs:int"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>''')
    p.var(f"{name}-input", element=p.ns(in_ns, "jdbcUpdateActivityInput"))
    p.var(name, element=p.ns(JDBC_COMMON, "jdbcUpdateActivityOutput"))
    return in_ns


_log_added = set()


def declare_log(p: Proc, name):
    if p.name not in _log_added:
        _log_added.add(p.name)
        p.add_type(f'''
<xs:schema attributeFormDefault="unqualified"
    elementFormDefault="qualified"
    targetNamespace="{LOG_NS}"
    xmlns:tns="{LOG_NS}" xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:complexType name="LogParametersType">
        <xs:sequence>
            <xs:element form="unqualified" minOccurs="0" name="msgCode" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="loggerName" type="xs:string"/>
            <xs:element form="unqualified" minOccurs="0" name="logLevel" type="xs:string"/>
            <xs:element form="unqualified" name="message" type="xs:string"/>
        </xs:sequence>
    </xs:complexType>
    <xs:element name="ActivityInput" type="tns:LogParametersType"/>
</xs:schema>''')
    p.var(f"{name}-input", element=p.ns(LOG_NS, "ActivityInput"))


def jms_ns(p: Proc, name, kind):
    return f"http://www.tibco.com/namespaces/tnt/plugins/jms+{p.xid(name)}+{kind}"


def declare_jms_receive(p: Proc, name, body_ns, body_element):
    out_ns = jms_ns(p, name, "output")
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{out_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:body="{body_ns}" xmlns:tns="{out_ns}">
    <import namespace="{body_ns}"/>
    <complexType name="JMSHeadersType">
        <sequence>
            <element minOccurs="0" name="JMSDestination" type="string"/>
            <element minOccurs="0" name="JMSReplyTo" type="string"/>
            <element minOccurs="0" name="JMSDeliveryMode" type="string"/>
            <element minOccurs="0" name="JMSMessageID" type="string"/>
            <element minOccurs="0" name="JMSTimestamp" type="long"/>
            <element minOccurs="0" name="JMSExpiration" type="long"/>
            <element minOccurs="0" name="JMSRedelivered" type="boolean"/>
            <element minOccurs="0" name="JMSPriority" type="int"/>
            <element minOccurs="0" name="JMSCorrelationID" type="string"/>
            <element minOccurs="0" name="JMSType" type="string"/>
        </sequence>
    </complexType>
    <element name="ActivityOutput">
        <complexType>
            <sequence>
                <element minOccurs="0" name="JMSHeaders" type="tns:JMSHeadersType"/>
                <element minOccurs="0" name="JMSProperties">
                    <complexType>
                        <sequence>
                            <element minOccurs="0" name="JMSXDeliveryCount" type="int"/>
                        </sequence>
                    </complexType>
                </element>
                <element minOccurs="0" name="Body">
                    <complexType>
                        <sequence>
                            <element ref="body:{body_element}"/>
                        </sequence>
                    </complexType>
                </element>
            </sequence>
        </complexType>
    </element>
</schema>''')
    p.var(name, element=p.ns(out_ns, "ActivityOutput"))
    return out_ns


def declare_jms_send(p: Proc, name, body_ns, body_element):
    in_ns = jms_ns(p, name, "input")
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{in_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:body="{body_ns}" xmlns:tns="{in_ns}">
    <import namespace="{body_ns}"/>
    <element name="ActivityInput">
        <complexType>
            <sequence>
                <element minOccurs="0" name="destinationQueue" type="string"/>
                <element minOccurs="0" name="JMSCorrelationID" type="string"/>
                <element minOccurs="0" name="JMSExpiration" type="long"/>
                <element minOccurs="0" name="JMSPriority" type="int"/>
                <element minOccurs="0" name="JMSType" type="string"/>
                <element minOccurs="0" name="Body">
                    <complexType>
                        <sequence>
                            <element ref="body:{body_element}"/>
                        </sequence>
                    </complexType>
                </element>
            </sequence>
        </complexType>
    </element>
</schema>''')
    p.var(f"{name}-input", element=p.ns(in_ns, "ActivityInput"))
    out_ns = jms_ns(p, name, "output")
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{out_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{out_ns}">
    <element name="ActivityOutput">
        <complexType>
            <sequence>
                <element minOccurs="0" name="JMSHeaders">
                    <complexType>
                        <sequence>
                            <element minOccurs="0" name="JMSDestination" type="string"/>
                            <element minOccurs="0" name="JMSDeliveryMode" type="string"/>
                            <element minOccurs="0" name="JMSMessageID" type="string"/>
                            <element minOccurs="0" name="JMSTimestamp" type="long"/>
                            <element minOccurs="0" name="JMSExpiration" type="long"/>
                            <element minOccurs="0" name="JMSPriority" type="int"/>
                        </sequence>
                    </complexType>
                </element>
            </sequence>
        </complexType>
    </element>
</schema>''')
    p.var(name, element=p.ns(out_ns, "ActivityOutput"))
    return in_ns


def compact(xsl: str) -> str:
    """Single-line form Studio keeps in the duplicate `expression` attribute."""
    head, rest = xsl.split("\n", 1)
    return head + "\n" + re.sub(r">\s+<", "><", rest).strip()


def jms_receive_config(name, dest, selector, schema_href):
    return bw_config("bw.jms.receive", "jms", "http://ns.tibco.com/bw/palette/jms",
                     "http://ns.tibco.com/bw/palette/jms#//JMSReceiveMessage",
                     {"acknowledgeMode": "1", "connectionReference": "jmsConnection",
                      "destination": dest, "messageSelector": selector,
                      "messageType": "XMLText", "messagingStyle": "Queue",
                      "pollingInterval": "2",
                      "xsi:type": "jms:JMSReceiveMessage"},
                     [N("bodyEditorElement", {"href": schema_href})], version="6.0.0.001")


def jms_send(p: Proc, name, dest, dest_type, schema_href, binding):
    cfg = bw_config("bw.jms.send", "jms", "http://ns.tibco.com/bw/palette/jms",
                    "http://ns.tibco.com/bw/palette/jms#//JMSSendMessage",
                    {"connectionReference": "jmsConnection", "deliveryMode": "2",
                     "destination": dest, "expiration": "0", "messageType": "XMLText",
                     "messagingStyle": dest_type, "priority": "4",
                     "xsi:type": "jms:JMSSendMessage"},
                    [N("bodyEditorElement", {"href": schema_href})], version="6.0.0.001")
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name,
                        binding=binding)


def http_ns(p: Proc, name, kind):
    cap = "RequestActivityInput" if kind == "input" else "RequestActivityOutput"
    return f"http://tns.tibco.com/bw/activity/sendhttprequest/{kind}+{p.xid(name)}+{cap}"


def declare_send_http(p: Proc, name):
    in_ns, out_ns = http_ns(p, name, "input"), http_ns(p, name, "output")
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{in_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{in_ns}">
    <complexType name="inputHeadersType">
        <sequence>
            <element minOccurs="0" name="Accept" type="string"/>
            <element minOccurs="0" name="Content-Type" type="string"/>
            <element minOccurs="0" name="X-Request-Id" type="string"/>
        </sequence>
    </complexType>
    <complexType name="RequestActivityInput">
        <sequence>
            <element minOccurs="0" name="Host" type="string"/>
            <element minOccurs="0" name="Port" type="int"/>
            <element minOccurs="0" name="Method" type="string"/>
            <element minOccurs="0" name="RequestURI" type="string"/>
            <element minOccurs="0" name="PostData" type="string"/>
            <element minOccurs="0" name="QueryString" type="string"/>
            <element minOccurs="0" name="Timeout" type="int"/>
            <element minOccurs="0" name="Headers" type="tns:inputHeadersType"/>
        </sequence>
    </complexType>
    <element name="RequestActivityInput" type="tns:RequestActivityInput"/>
</schema>''')
    p.add_type(f'''
<schema elementFormDefault="unqualified"
    targetNamespace="{out_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{out_ns}">
    <complexType name="statusLineType">
        <sequence>
            <element name="httpVersion" type="string"/>
            <element name="statusCode" type="int"/>
            <element name="reasonPhrase" type="string"/>
        </sequence>
    </complexType>
    <complexType name="RequestActivityOutput">
        <sequence>
            <element minOccurs="0" name="Header" type="string"/>
            <element minOccurs="0" name="statusLine" type="tns:statusLineType"/>
            <element minOccurs="0" name="asciiContent" type="string"/>
        </sequence>
    </complexType>
    <element name="RequestActivityOutput" type="tns:RequestActivityOutput"/>
</schema>''')
    p.var(f"{name}-input", element=p.ns(in_ns, "RequestActivityInput"))
    p.var(name, element=p.ns(out_ns, "RequestActivityOutput"))
    return in_ns, out_ns


def send_http(p: Proc, name, prop, binding):
    in_ns, out_ns = http_ns(p, name, "input"), http_ns(p, name, "output")
    cfg = bw_config("bw.http.sendHTTPRequest", "http", "http://ns.tibco.com/bw/palette/http",
                    "http://ns.tibco.com/bw/palette/http#//SendHTTPRequest",
                    {"httpClientResource": prop,
                     "inputHeadersQName": "RequestActivityInput:inputHeadersType",
                     "outputHeadersQName": "RequestActivityOutput:outputHeadersType",
                     "postDataAs": "String", "xsi:type": "http:SendHTTPRequest"},
                    extra_ns={"RequestActivityInput": in_ns, "RequestActivityOutput": out_ns},
                    version="6.0.0.20132205")
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name, binding=binding)


def json_render(p: Proc, name, schema_href, binding):
    cfg = bw_config("bw.restjson.JsonRender", "restjson", "http://ns.tibco.com/bw/palette/restjson",
                    "http://ns.tibco.com/bw/palette/restjson#//JsonRender",
                    {"jsonOutputStyle": "None", "schemaType": "Xsd",
                     "xsi:type": "restjson:JsonRender"},
                    [N("inputEditorElement", {"href": schema_href})])
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name, binding=binding)


def declare_json_render(p: Proc, name, in_element_qname):
    out_ns = f"activity.jsonRender.output+{p.xid(name)}+ActivityOutputType"
    p.add_type(f'''
<schema targetNamespace="{out_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{out_ns}">
    <complexType name="ActivityOutputClassType">
        <sequence>
            <element form="unqualified" name="jsonString" type="string"/>
        </sequence>
    </complexType>
    <element name="ActivityOutputClass" type="tns:ActivityOutputClassType"/>
</schema>''')
    p.var(f"{name}-input", element=in_element_qname)
    p.var(name, element=p.ns(out_ns, "ActivityOutputClass"))


def json_parse(p: Proc, name, schema_href, binding):
    cfg = bw_config("bw.restjson.JsonParser", "restjson", "http://ns.tibco.com/bw/palette/restjson",
                    "http://ns.tibco.com/bw/palette/restjson#//JsonParser",
                    {"jsonInputStyle": "None", "schemaType": "Xsd",
                     "xsi:type": "restjson:JsonParser"},
                    [N("outputEditorElement", {"href": schema_href})])
    return ext_activity(p, name, cfg, input_var=f"{name}-input", output_var=name, binding=binding)


def declare_json_parse(p: Proc, name, out_element_qname):
    in_ns = f"activity.jsonParser.input+{p.xid(name)}+ActivityInputType"
    p.add_type(f'''
<schema targetNamespace="{in_ns}"
    xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{in_ns}">
    <complexType name="ActivityInputClassType">
        <sequence>
            <element form="unqualified" name="jsonString" type="string"/>
        </sequence>
    </complexType>
    <element name="ActivityInputClass" type="tns:ActivityInputClassType"/>
</schema>''')
    p.var(f"{name}-input", element=p.ns(in_ns, "ActivityInputClass"))
    p.var(name, element=out_element_qname)
    return in_ns


def empty(p: Proc, name):
    def build(targets, sources):
        return N("bpws:empty", {"name": name, "tibex:xpdlId": p.xid(name)},
                 [targets_node(targets), sources_node(sources)])
    return build


def group_marker(p: Proc, name, kind):
    """GroupStart / GroupEnd node of a group (kind 'groupStart' or 'groupEnd')."""
    def build(targets, sources):
        return N("bpws:empty", {"name": name, "tibex:group": kind,
                                "tibex:xpdlId": p.xid(name)},
                 [targets_node(targets), sources_node(sources)])
    return build


def throw(p: Proc, name, fault_qname, binding):
    def build(targets, sources):
        return N("bpws:throw", {"faultName": fault_qname, "faultVariable": f"{name}-input",
                                "name": name, "tibex:xpdlId": p.xid(name)}, [
            input_bindings(binding, "tibex:partBinding"),
            targets_node(targets), sources_node(sources)])
    return build


def reply(p: Proc, name, operation, partner_link, port_type, variable, part_xsl, message_xsl,
          fault_name=None):
    def build(targets, sources):
        attrs = {"name": name, "operation": operation, "partnerLink": partner_link,
                 "portType": port_type, "tibex:xpdlId": p.xid(name), "variable": variable}
        if fault_name:
            attrs["faultName"] = fault_name
        return N("bpws:reply", attrs, [
            input_bindings(part_xsl, "tibex:partBinding"),
            N("tibex:inputBinding", {"expressionLanguage": XSLT_LANG}, text=message_xsl),
            targets_node(targets), sources_node(sources)])
    return build


def group_scope(p: Proc, name, kind, inner: Flow, extra_children=None):
    def build(targets, sources):
        return N("bpws:scope", {"name": name, "tibex:group": kind, "tibex:xpdlId": p.xid(name)},
                 [targets_node(targets), sources_node(sources), *(extra_children or []),
                  inner.node()])
    return build
