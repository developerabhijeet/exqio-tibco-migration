"""REST service (bw.rest binding) plumbing shared by the REST-started processes."""
from __future__ import annotations

import re

from .bwlib import N, Raw, xslt
from .bwproc import REST_NS, Flow, Proc, reply


def rest_service(p: Proc, *, port_type, apipath, basepath, resource, operation, method_doc,
                 request_element=None, request_ns=None, params_element=None, params_ns=None,
                 response_element, response_ns):
    plt = p.tns_uri + "PLT"
    plt_prefix = p.ns(plt)
    p.import_xsd(REST_NS)
    for ns in filter(None, (request_ns, params_ns, response_ns)):
        p.import_xsd(ns)
    ext = {"extns": REST_NS}
    req_parts = []
    if request_element:
        ext["extns1"] = request_ns
        req_parts.append(f'<wsdl:part element="extns1:{request_element}"\n'
                         f'            name="item" tibex:hasMultipleNamespaces="false"/>')
    if params_element:
        ext["extns2"] = params_ns
        req_parts.append(f'<wsdl:part element="extns2:{params_element}"\n'
                         f'            name="parameters" tibex:source="bw.rest"/>')
    req_parts.append('<wsdl:part element="extns:httpHeaders"\n'
                     '            name="httpHeaders" tibex:source="bw.rest"/>')
    resp_prefix = "extns1" if response_ns == request_ns else "extns3"
    ext[resp_prefix] = response_ns
    decls = "\n    ".join(f'xmlns:{k}="{v}"' for k, v in sorted(ext.items()))
    imports = "\n    ".join(f'<wsdl:import namespace="{v}"/>' for k, v in sorted(ext.items()))
    parts = "\n        ".join(req_parts)
    p.add_type(f'''
<wsdl:definitions
    targetNamespace="{plt}"
    {decls}
    xmlns:plnk="http://docs.oasis-open.org/wsbpel/2.0/plnktype"
    xmlns:tibex="http://www.tibco.com/bpel/2007/extensions"
    xmlns:tns="{plt}"
    xmlns:vprop="http://docs.oasis-open.org/wsbpel/2.0/varprop"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <plnk:partnerLinkType name="partnerLinkType">
        <plnk:role name="use" portType="tns:{port_type}"/>
    </plnk:partnerLinkType>
    {imports}
    <wsdl:message name="{operation}Request">
        {parts}
    </wsdl:message>
    <wsdl:message name="{operation}Response">
        <wsdl:part element="{resp_prefix}:{response_element}"
            name="item" tibex:hasMultipleNamespaces="false"/>
    </wsdl:message>
    <wsdl:message name="{operation}4XXFaultMessage">
        <wsdl:part element="extns:client4XXError" name="clientError"/>
    </wsdl:message>
    <wsdl:message name="{operation}5XXFaultMessage">
        <wsdl:part element="extns:server5XXError" name="serverError"/>
    </wsdl:message>
    <wsdl:portType name="{port_type}"
        tibex:bw.rest.apipath="{apipath}"
        tibex:bw.rest.basepath="{basepath}"
        tibex:bw.rest.resource="{resource}"
        tibex:bw.rest.resource.source="generated" tibex:source="bw.rest.service">
        <wsdl:documentation>{method_doc}</wsdl:documentation>
        <wsdl:operation name="{operation}">
            <wsdl:input message="tns:{operation}Request" name="{operation}Input"/>
            <wsdl:output message="tns:{operation}Response" name="{operation}Output"/>
            <wsdl:fault message="tns:{operation}4XXFaultMessage" name="clientFault"/>
            <wsdl:fault message="tns:{operation}5XXFaultMessage" name="serverFault"/>
        </wsdl:operation>
    </wsdl:portType>
</wsdl:definitions>''')
    p.partner_links.append(N("bpws:partnerLink", {
        "myRole": "use", "name": port_type, "partnerLinkType": f"{plt_prefix}:partnerLinkType",
        "sca-bpel:ignore": "false", "sca-bpel:service": port_type}))
    p.var(operation, message=f"{plt_prefix}:{operation}Request")
    p.var(f"{operation}Out-input", message=f"{plt_prefix}:{operation}Response")
    return plt


def compact(xsl: str) -> str:
    head, rest = xsl.split("\n", 1)
    return head + "\n" + re.sub(r">\s+<", "><", rest).strip()


def rest_reply(p: Proc, name, *, plt, port_type, operation, params, element_body,
               fault=None, variable=None):
    """bpws:reply carrying both the part binding and the whole-message binding, as
    Studio writes them. `fault` is None, 'client' or 'server'."""
    prefixes = p.prefixes()
    plt_tns = p.tns(plt)
    prefixes = p.prefixes()
    template = variable or f"{operation}Out-input"
    part_xsl = xslt(prefixes, params, template, element_body)
    if fault == "client":
        msg, part = f"{operation}4XXFaultMessage", "clientError"
    elif fault == "server":
        msg, part = f"{operation}5XXFaultMessage", "serverError"
    else:
        msg, part = f"{operation}Response", "item"
    indented = "\n".join("        " + ln for ln in element_body.strip("\n").splitlines())
    wrapped = f"<{plt_tns}:{msg}>\n    <{part}>\n{indented}\n    </{part}>\n</{plt_tns}:{msg}>"
    msg_xsl = compact(xslt(prefixes, params, template, wrapped))
    fault_name = {"client": "clientFault", "server": "serverFault"}.get(fault)
    return reply(p, name, operation, port_type, f"{p.ns(plt)}:{port_type}", template,
                 part_xsl, msg_xsl, fault_name=fault_name)


def fault_body(p: Proc, kind, status, message_expr):
    rest = p.tns(REST_NS)
    el = "client4XXError" if kind == "client" else "server5XXError"
    return f'''
<{rest}:{el}>
    <statusCode>
        <xsl:value-of select="{status}"/>
    </statusCode>
    <message>
        <xsl:value-of select="{message_expr}"/>
    </message>
</{rest}:{el}>'''


def pick_body(p: Proc, *, port_type, operation, inner: Flow, fault_handlers=None) -> N:
    plt_prefix = p.ns(p.tns_uri + "PLT")
    inner.acts.insert(0, ("OnMessageStart", lambda t, s: N("bpws:empty", {
        "name": "OnMessageStart", "tibex:constructor": "onMessageStart",
        "tibex:xpdlId": p.xid("OnMessageStart")})))
    inner.acts.insert(1, ("OnMessageEnd", lambda t, s: N("bpws:empty", {
        "name": "OnMessageEnd", "tibex:constructor": "onMessageEnd",
        "tibex:xpdlId": p.xid("OnMessageEnd")})))
    scope1 = N("bpws:scope", {"name": "scope1"}, [
        N("bpws:faultHandlers", children=fault_handlers) if fault_handlers else None,
        inner.node()])
    return N("bpws:scope", {"name": "scope"}, [
        N("bpws:flow", {"name": "flow"}, [
            N("bpws:links"),
            N("bpws:pick", {"createInstance": "yes", "name": "pick",
                            "tibex:xpdlId": p.xid("pick")}, [
                N("bpws:onMessage", {"operation": operation, "partnerLink": port_type,
                                     "portType": f"{plt_prefix}:{port_type}",
                                     "tibex:xpdlId": p.xid("onMessage"),
                                     "variable": operation}, [scope1])])])])


def catch(p: Proc, key, fault_qname, variable, flow: Flow) -> N:
    return N("bpws:catch", {"faultElement": fault_qname, "faultName": fault_qname,
                            "faultVariable": variable, "tibex:xpdlId": p.xid(key)}, [
        N("bpws:scope", {"name": f"{flow.name}Scope"}, [flow.node()])])


def catch_all(p: Proc, flow: Flow) -> N:
    return N("bpws:catchAll", {"tibex:faultDetailsVar": "FaultDetails",
                               "tibex:faultNameVar": "FaultName",
                               "tibex:xpdlId": p.xid("catchAll")}, [
        N("bpws:scope", {"name": f"{flow.name}Scope"}, [flow.node()])])


def raw_type(p: Proc, text: str):
    p.types.append(Raw(text))
