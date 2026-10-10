"""CreditOrigination module processes."""
from __future__ import annotations

from .bwlib import N
from .bwproc import (
    SR_HTTPCLIENT,
    SR_JDBC,
    Flow,
    Proc,
    call_process,
    declare_jdbc_query,
    declare_jdbc_update,
    declare_json_parse,
    declare_json_render,
    declare_send_http,
    empty,
    end_activity,
    jdbc_query,
    jdbc_update,
    json_parse,
    json_render,
    mapper,
    send_http,
    start_event,
    throw,
)
from .names import CO_APP, CO_AUD, CO_BUR, CO_DEC, CO_FLT, CO_VEH, REST_NS
from .rest import catch, catch_all, fault_body, pick_body, rest_reply, rest_service

MOD = "CreditOrigination"
SCHEMA = "platform:/resource/CreditOrigination/Schemas"
DB = "creditorigination.GMFOriginationDB"


def sql(*lines):
    return "\n".join(lines)


# ---------------------------------------------------------------------------------
def intake() -> Proc:
    p = Proc(MOD, "creditorigination.CreditApplicationIntake", "20190314093512",
             callable_=False, created_on="Thu Mar 14 09:35:12 EDT 2019",
             description="Dealer channel credit application intake (POST /applications).")
    plt = rest_service(p, port_type="applications", apipath="/applications",
                       basepath="Applications",
                       resource="Service Descriptors/creditorigination.CreditApplicationIntake-applications.json",
                       operation="post", method_doc="Submit a retail credit application.",
                       request_element="CreditApplicationRequest", request_ns=CO_APP,
                       response_element="CreditApplicationResponse", response_ns=CO_APP)
    for ns in (CO_AUD, CO_VEH, CO_BUR, CO_DEC, CO_FLT):
        p.import_xsd(ns)
    A, AU, V, B, D, F = (p.tns(CO_APP), p.tns(CO_AUD), p.tns(CO_VEH), p.tns(CO_BUR),
                         p.tns(CO_DEC), p.tns(CO_FLT))
    p.tns(REST_NS)
    plt_ns = p.ns(plt)

    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.prop("jdbcProperty", DB, SR_JDBC)
    p.var("NormalizeApplicant-input", element=p.ns(CO_APP, "ApplicantSnapshot"))
    p.var("NormalizeApplicant", element=p.ns(CO_APP, "ApplicantSnapshot"))
    p.var("CallAuditLog-input", element=p.ns(CO_AUD, "AuditEvent"))
    p.var("CallValidateVIN-input", element=p.ns(CO_VEH, "VinValidationRequest"))
    p.var("CallValidateVIN", element=p.ns(CO_VEH, "VinValidationResult"))
    dup_params = [("ssn", "VARCHAR")]
    dup_cols = [("application_id", "VARCHAR"), ("status", "VARCHAR"),
                ("submitted_at", "TIMESTAMP")]
    _, dup_in = None, declare_jdbc_query(p, "CheckDuplicateApplication", dup_params, dup_cols)[0]
    p.var("ThrowDuplicateApplication-input", element=p.ns(CO_FLT, "DuplicateApplicationFault"))
    p.var("CallBureauOrchestrator-input", element=p.ns(CO_BUR, "BureauRequest"))
    p.var("CallBureauOrchestrator", element=p.ns(CO_BUR, "BureauResult"))
    p.var("CallDecisionEngine-input", element=p.ns(CO_DEC, "DecisionRequest"))
    p.var("CallDecisionEngine", element=p.ns(CO_DEC, "DecisionResult"))
    for v in ("ReplyInvalidSSN-input", "ReplyInvalidVIN-input", "ReplyDuplicate-input",
              "ReplyIncomplete-input"):
        p.var(v, message=f"{plt_ns}:post4XXFaultMessage")
    p.var("ReplyServerError-input", message=f"{plt_ns}:post5XXFaultMessage")
    p.var("DuplicateApplicationFault", element=p.ns(CO_FLT, "DuplicateApplicationFault"))
    p.var("MissingIncomeFault", element=p.ns(CO_FLT, "MissingIncomeFault"))
    p.var("FaultName", type_="xsd:string")
    p.var("FaultDetails", element=p.ns("http://www.tibco.com/pe/EngineTypes", "FaultDetail"))
    J = p.tns(dup_in)

    req = "$post.item"
    f = Flow("flow1")
    f.add("NormalizeApplicant", mapper(p, "NormalizeApplicant", f"{p.ns(CO_APP)}:ApplicantSnapshot", CO_APP, p.map(
        ["post.item"], "NormalizeApplicant-input", f'''
<{A}:ApplicantSnapshot>
    <{A}:applicationId>
        <xsl:value-of select="normalize-space({req}/{A}:applicationId)"/>
    </{A}:applicationId>
    <{A}:dealerId>
        <xsl:value-of select="upper-case(normalize-space({req}/{A}:dealerId))"/>
    </{A}:dealerId>
    <{A}:ssn>
        <xsl:value-of select="normalize-space({req}/{A}:applicant/{A}:ssn)"/>
    </{A}:ssn>
    <{A}:firstName>
        <xsl:value-of select="upper-case(normalize-space({req}/{A}:applicant/{A}:firstName))"/>
    </{A}:firstName>
    <{A}:lastName>
        <xsl:value-of select="upper-case(normalize-space({req}/{A}:applicant/{A}:lastName))"/>
    </{A}:lastName>
    <{A}:dateOfBirth>
        <xsl:value-of select="{req}/{A}:applicant/{A}:dateOfBirth"/>
    </{A}:dateOfBirth>
    <{A}:vin>
        <xsl:value-of select="upper-case(normalize-space({req}/{A}:vehicle/{A}:vin))"/>
    </{A}:vin>
</{A}:ApplicantSnapshot>''')))

    f.add("CallAuditLog", call_process(p, "CallAuditLog", "creditorigination.AuditLog", p.map(
        ["NormalizeApplicant", "post.item"], "CallAuditLog-input", f'''
<{AU}:AuditEvent>
    <{AU}:eventType>
        <xsl:value-of select="&quot;APPLICATION_RECEIVED&quot;"/>
    </{AU}:eventType>
    <{AU}:applicationId>
        <xsl:value-of select="$NormalizeApplicant/{A}:applicationId"/>
    </{AU}:applicationId>
    <{AU}:ssn>
        <xsl:value-of select="$NormalizeApplicant/{A}:ssn"/>
    </{AU}:ssn>
    <{AU}:payload>
        <xsl:value-of select="concat('dealer=', $NormalizeApplicant/{A}:dealerId, ';vin=', $NormalizeApplicant/{A}:vin, ';amount=', {req}/{A}:requestedAmount, ';term=', {req}/{A}:requestedTermMonths)"/>
    </{AU}:payload>
</{AU}:AuditEvent>'''), input_var="CallAuditLog-input"))

    ssn_ok = (f"matches($NormalizeApplicant/{A}:ssn, '^[0-9]{{3}}-[0-9]{{2}}-[0-9]{{4}}$') and "
              f"not(substring($NormalizeApplicant/{A}:ssn, 1, 3) = ('000', '666'))")
    f.add("ReplyInvalidSSN", rest_reply(
        p, "ReplyInvalidSSN", plt=plt, port_type="applications", operation="post", params=[],
        fault="client", variable="ReplyInvalidSSN-input",
        element_body=fault_body(p, "client", "400", "&quot;SSN must be formatted NNN-NN-NNNN&quot;")))

    f.add("CallValidateVIN", call_process(p, "CallValidateVIN", "creditorigination.ValidateVIN", p.map(
        ["NormalizeApplicant"], "CallValidateVIN-input", f'''
<{V}:VinValidationRequest>
    <{V}:vin>
        <xsl:value-of select="$NormalizeApplicant/{A}:vin"/>
    </{V}:vin>
</{V}:VinValidationRequest>'''), input_var="CallValidateVIN-input", output_var="CallValidateVIN"))

    f.add("ReplyInvalidVIN", rest_reply(
        p, "ReplyInvalidVIN", plt=plt, port_type="applications", operation="post",
        params=["CallValidateVIN"], fault="client", variable="ReplyInvalidVIN-input",
        element_body=fault_body(p, "client", "400",
                                f"concat(&quot;VIN rejected: &quot;, $CallValidateVIN/{V}:reason)")))

    f.add("CheckDuplicateApplication", jdbc_query(
        p, "CheckDuplicateApplication",
        sql("select application_id, status, submitted_at",
            "  from gmf.credit_application",
            " where ssn = ?",
            "   and submitted_at >= current_timestamp - interval '30 days'"),
        dup_params, dup_cols, p.map(["NormalizeApplicant"], "CheckDuplicateApplication-input", f'''
<{J}:jdbcQueryActivityInput>
    <ssn>
        <xsl:value-of select="$NormalizeApplicant/{A}:ssn"/>
    </ssn>
</{J}:jdbcQueryActivityInput>''')))

    f.add("ThrowDuplicateApplication", throw(
        p, "ThrowDuplicateApplication", p.ns(CO_FLT, "DuplicateApplicationFault"), p.map(
            ["NormalizeApplicant", "CheckDuplicateApplication"], "ThrowDuplicateApplication-input", f'''
<{F}:DuplicateApplicationFault>
    <{F}:applicationId>
        <xsl:value-of select="$NormalizeApplicant/{A}:applicationId"/>
    </{F}:applicationId>
    <{F}:existingApplicationId>
        <xsl:value-of select="$CheckDuplicateApplication/Record[1]/application_id"/>
    </{F}:existingApplicationId>
    <{F}:message>
        <xsl:value-of select="&quot;Application already submitted for this SSN within 30 days&quot;"/>
    </{F}:message>
</{F}:DuplicateApplicationFault>''')))

    f.add("CallBureauOrchestrator", call_process(p, "CallBureauOrchestrator", "creditorigination.BureauOrchestrator", p.map(
        ["NormalizeApplicant"], "CallBureauOrchestrator-input", f'''
<{B}:BureauRequest>
    <{B}:applicationId>
        <xsl:value-of select="$NormalizeApplicant/{A}:applicationId"/>
    </{B}:applicationId>
    <{B}:ssn>
        <xsl:value-of select="$NormalizeApplicant/{A}:ssn"/>
    </{B}:ssn>
    <{B}:firstName>
        <xsl:value-of select="$NormalizeApplicant/{A}:firstName"/>
    </{B}:firstName>
    <{B}:lastName>
        <xsl:value-of select="$NormalizeApplicant/{A}:lastName"/>
    </{B}:lastName>
    <{B}:dateOfBirth>
        <xsl:value-of select="$NormalizeApplicant/{A}:dateOfBirth"/>
    </{B}:dateOfBirth>
</{B}:BureauRequest>'''), input_var="CallBureauOrchestrator-input", output_var="CallBureauOrchestrator"))

    f.add("CallDecisionEngine", call_process(p, "CallDecisionEngine", "creditorigination.DecisionEngine", p.map(
        ["NormalizeApplicant", "CallBureauOrchestrator", "post.item"], "CallDecisionEngine-input", f'''
<{D}:DecisionRequest>
    <parameters>
        <applicationId>
            <xsl:value-of select="$NormalizeApplicant/{A}:applicationId"/>
        </applicationId>
        <ssn>
            <xsl:value-of select="$NormalizeApplicant/{A}:ssn"/>
        </ssn>
        <dealerId>
            <xsl:value-of select="$NormalizeApplicant/{A}:dealerId"/>
        </dealerId>
        <vin>
            <xsl:value-of select="$NormalizeApplicant/{A}:vin"/>
        </vin>
        <creditScore>
            <xsl:value-of select="$CallBureauOrchestrator/{B}:selectedScore"/>
        </creditScore>
        <singleBureau>
            <xsl:value-of select="$CallBureauOrchestrator/{B}:singleBureau"/>
        </singleBureau>
        <xsl:if test="{req}/{A}:applicant/{A}:monthlyIncome">
            <monthlyIncome>
                <xsl:value-of select="{req}/{A}:applicant/{A}:monthlyIncome"/>
            </monthlyIncome>
        </xsl:if>
        <monthlyDebt>
            <xsl:value-of select="{req}/{A}:applicant/{A}:monthlyDebt"/>
        </monthlyDebt>
        <requestedAmount>
            <xsl:value-of select="{req}/{A}:requestedAmount"/>
        </requestedAmount>
        <requestedTermMonths>
            <xsl:value-of select="{req}/{A}:requestedTermMonths"/>
        </requestedTermMonths>
    </parameters>
</{D}:DecisionRequest>'''), input_var="CallDecisionEngine-input", output_var="CallDecisionEngine"))

    f.add("postOut", rest_reply(
        p, "postOut", plt=plt, port_type="applications", operation="post",
        params=["NormalizeApplicant", "CallBureauOrchestrator", "CallDecisionEngine"],
        element_body=f'''
<{A}:CreditApplicationResponse>
    <{A}:applicationId>
        <xsl:value-of select="$NormalizeApplicant/{A}:applicationId"/>
    </{A}:applicationId>
    <{A}:decision>
        <xsl:value-of select="$CallDecisionEngine/decision"/>
    </{A}:decision>
    <xsl:if test="$CallDecisionEngine/tier">
        <{A}:tier>
            <xsl:value-of select="$CallDecisionEngine/tier"/>
        </{A}:tier>
    </xsl:if>
    <{A}:apr>
        <xsl:value-of select="$CallDecisionEngine/apr"/>
    </{A}:apr>
    <{A}:approvedAmount>
        <xsl:value-of select="$CallDecisionEngine/approvedAmount"/>
    </{A}:approvedAmount>
    <{A}:approvedTermMonths>
        <xsl:value-of select="$CallDecisionEngine/approvedTermMonths"/>
    </{A}:approvedTermMonths>
    <{A}:creditScore>
        <xsl:value-of select="$CallBureauOrchestrator/{B}:selectedScore"/>
    </{A}:creditScore>
    <xsl:if test="$CallBureauOrchestrator/{B}:equifaxScore">
        <{A}:equifaxScore>
            <xsl:value-of select="$CallBureauOrchestrator/{B}:equifaxScore"/>
        </{A}:equifaxScore>
    </xsl:if>
    <{A}:singleBureau>
        <xsl:value-of select="$CallBureauOrchestrator/{B}:singleBureau"/>
    </{A}:singleBureau>
    <{A}:decidedAt>
        <xsl:value-of select="current-dateTime()"/>
    </{A}:decidedAt>
</{A}:CreditApplicationResponse>'''))

    f.link("NormalizeApplicant", "CallAuditLog")
    f.link("CallAuditLog", "CallValidateVIN", "SUCCESSWITHCONDITION", ssn_ok, "SSN format valid")
    f.link("CallAuditLog", "ReplyInvalidSSN", "SUCCESSWITHNOCONDITION", label="")
    f.link("CallValidateVIN", "CheckDuplicateApplication", "SUCCESSWITHCONDITION",
           f"$CallValidateVIN/{V}:valid = 'true'", "VIN valid")
    f.link("CallValidateVIN", "ReplyInvalidVIN", "SUCCESSWITHNOCONDITION", label="")
    f.link("CheckDuplicateApplication", "ThrowDuplicateApplication", "SUCCESSWITHCONDITION",
           "count($CheckDuplicateApplication/Record) > 0", "Duplicate within 30 days")
    f.link("CheckDuplicateApplication", "CallBureauOrchestrator", "SUCCESSWITHNOCONDITION",
           label="")
    f.link("CallBureauOrchestrator", "CallDecisionEngine")
    f.link("CallDecisionEngine", "postOut")

    dup = Flow("flow3")
    dup.add("ReplyDuplicate", rest_reply(
        p, "ReplyDuplicate", plt=plt, port_type="applications", operation="post",
        params=["DuplicateApplicationFault"], fault="client", variable="ReplyDuplicate-input",
        element_body=fault_body(p, "client", "409",
                                f"$DuplicateApplicationFault/{F}:message")))
    inc = Flow("flow4")
    inc.add("ReplyIncomplete", rest_reply(
        p, "ReplyIncomplete", plt=plt, port_type="applications", operation="post",
        params=["MissingIncomeFault"], fault="client", variable="ReplyIncomplete-input",
        element_body=fault_body(p, "client", "422", f"$MissingIncomeFault/{F}:message")))
    err = Flow("flow2")
    err.add("ReplyServerError", rest_reply(
        p, "ReplyServerError", plt=plt, port_type="applications", operation="post",
        params=["FaultDetails"], fault="server", variable="ReplyServerError-input",
        element_body=fault_body(p, "server", "503", "$FaultDetails/Msg")))
    handlers = [
        catch(p, "catchDuplicate", p.ns(CO_FLT, "DuplicateApplicationFault"),
              "DuplicateApplicationFault", dup),
        catch(p, "catchMissingIncome", p.ns(CO_FLT, "MissingIncomeFault"),
              "MissingIncomeFault", inc),
        catch_all(p, err),
    ]
    p.body = pick_body(p, port_type="applications", operation="post", inner=f,
                       fault_handlers=handlers)
    return p


# ---------------------------------------------------------------------------------
def bureau() -> Proc:
    p = Proc(MOD, "creditorigination.BureauOrchestrator", "20190318141007", callable_=True,
             created_on="Mon Mar 18 14:10:07 EDT 2019",
             description="Pulls Equifax and TransUnion in parallel and keeps the higher score.")
    for ns in (CO_BUR, CO_FLT):
        p.import_xsd(ns)
    B, F = p.tns(CO_BUR), p.tns(CO_FLT)
    p.interface = (f"{{{CO_BUR}}}BureauRequest", f"{{{CO_BUR}}}BureauResult")
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.var("Start", element=p.ns(CO_BUR, "BureauRequest"), parameter="in")
    p.var("End-input", element=p.ns(CO_BUR, "BureauResult"), parameter="out")
    p.prop("equifaxClient", "creditorigination.EquifaxClient", SR_HTTPCLIENT)
    p.prop("transUnionClient", "creditorigination.TransUnionClient", SR_HTTPCLIENT)
    p.prop("jdbcProperty", DB, SR_JDBC)
    declare_json_render(p, "RenderBureauRequest", p.ns(CO_BUR, "BureauScoreRequest"))
    eq_in, _ = declare_send_http(p, "PullEquifax")
    tu_in, _ = declare_send_http(p, "PullTransUnion")
    peq = declare_json_parse(p, "ParseEquifax", p.ns(CO_BUR, "BureauScore"))
    ptu = declare_json_parse(p, "ParseTransUnion", p.ns(CO_BUR, "BureauScore"))
    p.var("MergeScores-input", element=p.ns(CO_BUR, "BureauResult"))
    p.var("MergeScores", element=p.ns(CO_BUR, "BureauResult"))
    rec_params = [("applicationId", "VARCHAR"), ("equifaxScore", "INTEGER"),
                  ("transUnionScore", "INTEGER"), ("selectedScore", "INTEGER"),
                  ("singleBureau", "BOOLEAN")]
    rec_in = declare_jdbc_update(p, "RecordBureauPull", rec_params)
    p.var("ThrowBureauUnavailable-input", element=p.ns(CO_FLT, "BureauUnavailableFault"))
    HE, HT, JE, JT, R = p.tns(eq_in), p.tns(tu_in), p.tns(peq), p.tns(ptu), p.tns(rec_in)

    f = Flow("flow")
    f.add("Start", start_event(p))
    f.add("RenderBureauRequest", json_render(
        p, "RenderBureauRequest", f"{SCHEMA}/Bureau.xsd#//BureauScoreRequest;XSDElementDeclaration",
        p.map(["Start"], "RenderBureauRequest-input", f'''
<{B}:BureauScoreRequest>
    <{B}:ssn>
        <xsl:value-of select="$Start/{B}:ssn"/>
    </{B}:ssn>
    <{B}:firstName>
        <xsl:value-of select="$Start/{B}:firstName"/>
    </{B}:firstName>
    <{B}:lastName>
        <xsl:value-of select="$Start/{B}:lastName"/>
    </{B}:lastName>
    <{B}:dateOfBirth>
        <xsl:value-of select="$Start/{B}:dateOfBirth"/>
    </{B}:dateOfBirth>
</{B}:BureauScoreRequest>''')))

    def pull(name, prop, prefix, uri):
        return send_http(p, name, prop, p.map(["RenderBureauRequest", "Start"], f"{name}-input", f'''
<{prefix}:RequestActivityInput>
    <Method>
        <xsl:value-of select="&quot;POST&quot;"/>
    </Method>
    <RequestURI>
        <xsl:value-of select="&quot;{uri}&quot;"/>
    </RequestURI>
    <PostData>
        <xsl:value-of select="$RenderBureauRequest/jsonString"/>
    </PostData>
    <Headers>
        <Accept>
            <xsl:value-of select="&quot;application/json&quot;"/>
        </Accept>
        <Content-Type>
            <xsl:value-of select="&quot;application/json&quot;"/>
        </Content-Type>
        <X-Request-Id>
            <xsl:value-of select="$Start/{B}:applicationId"/>
        </X-Request-Id>
    </Headers>
</{prefix}:RequestActivityInput>'''))

    def parse(name, source, prefix):
        return json_parse(p, name, f"{SCHEMA}/Bureau.xsd#//BureauScore;XSDElementDeclaration",
                          p.map([source], f"{name}-input", f'''
<{prefix}:ActivityInputClass>
    <jsonString>
        <xsl:value-of select="${source}/asciiContent"/>
    </jsonString>
</{prefix}:ActivityInputClass>'''))

    f.add("PullEquifax", pull("PullEquifax", "equifaxClient", HE, "/consumer-credit/v2/scores"))
    f.add("PullTransUnion", pull("PullTransUnion", "transUnionClient", HT, "/credit-report/v1/score"))
    f.add("ParseEquifax", parse("ParseEquifax", "PullEquifax", JE))
    f.add("EquifaxUnavailable", empty(p, "EquifaxUnavailable"))
    f.add("ParseTransUnion", parse("ParseTransUnion", "PullTransUnion", JT))
    f.add("TransUnionUnavailable", empty(p, "TransUnionUnavailable"))

    f.add("MergeScores", mapper(p, "MergeScores", f"{p.ns(CO_BUR)}:BureauResult", CO_BUR, p.map(
        ["ParseEquifax", "ParseTransUnion"], "MergeScores-input", f'''
<xsl:variable name="equifax" select="$ParseEquifax/{B}:score"/>
<xsl:variable name="transUnion" select="$ParseTransUnion/{B}:score"/>
<{B}:BureauResult>
    <xsl:if test="exists($equifax)">
        <{B}:equifaxScore>
            <xsl:value-of select="$equifax"/>
        </{B}:equifaxScore>
    </xsl:if>
    <xsl:if test="exists($transUnion)">
        <{B}:transUnionScore>
            <xsl:value-of select="$transUnion"/>
        </{B}:transUnionScore>
    </xsl:if>
    <xsl:if test="exists(($equifax, $transUnion))">
        <{B}:selectedScore>
            <xsl:value-of select="max((xsd:integer($equifax), xsd:integer($transUnion)))"/>
        </{B}:selectedScore>
        <{B}:selectedBureau>
            <xsl:value-of select="if (empty($transUnion) or (exists($equifax) and xsd:integer($equifax) >= xsd:integer($transUnion))) then 'EQUIFAX' else 'TRANSUNION'"/>
        </{B}:selectedBureau>
    </xsl:if>
    <{B}:singleBureau>
        <xsl:value-of select="count(($equifax, $transUnion)) = 1"/>
    </{B}:singleBureau>
    <{B}:bureauCount>
        <xsl:value-of select="count(($equifax, $transUnion))"/>
    </{B}:bureauCount>
</{B}:BureauResult>''')))

    f.add("ThrowBureauUnavailable", throw(
        p, "ThrowBureauUnavailable", p.ns(CO_FLT, "BureauUnavailableFault"), p.map(
            ["Start"], "ThrowBureauUnavailable-input", f'''
<{F}:BureauUnavailableFault>
    <{F}:applicationId>
        <xsl:value-of select="$Start/{B}:applicationId"/>
    </{F}:applicationId>
    <{F}:message>
        <xsl:value-of select="&quot;Neither Equifax nor TransUnion returned a score&quot;"/>
    </{F}:message>
</{F}:BureauUnavailableFault>''')))

    f.add("RecordBureauPull", jdbc_update(
        p, "RecordBureauPull",
        sql("insert into gmf.bureau_pull",
            "  (application_id, equifax_score, transunion_score, selected_score, single_bureau, pulled_at)",
            "values (?, ?, ?, ?, ?, current_timestamp)"),
        rec_params, p.map(["Start", "MergeScores"], "RecordBureauPull-input", f'''
<{R}:jdbcUpdateActivityInput>
    <applicationId>
        <xsl:value-of select="$Start/{B}:applicationId"/>
    </applicationId>
    <xsl:if test="$MergeScores/{B}:equifaxScore">
        <equifaxScore>
            <xsl:value-of select="$MergeScores/{B}:equifaxScore"/>
        </equifaxScore>
    </xsl:if>
    <xsl:if test="$MergeScores/{B}:transUnionScore">
        <transUnionScore>
            <xsl:value-of select="$MergeScores/{B}:transUnionScore"/>
        </transUnionScore>
    </xsl:if>
    <selectedScore>
        <xsl:value-of select="$MergeScores/{B}:selectedScore"/>
    </selectedScore>
    <singleBureau>
        <xsl:value-of select="$MergeScores/{B}:singleBureau"/>
    </singleBureau>
</{R}:jdbcUpdateActivityInput>''')))

    f.add("End", end_activity(p, p.map(["MergeScores"], "End-input", f'''
<{B}:BureauResult>
    <xsl:if test="$MergeScores/{B}:equifaxScore">
        <{B}:equifaxScore>
            <xsl:value-of select="$MergeScores/{B}:equifaxScore"/>
        </{B}:equifaxScore>
    </xsl:if>
    <xsl:if test="$MergeScores/{B}:transUnionScore">
        <{B}:transUnionScore>
            <xsl:value-of select="$MergeScores/{B}:transUnionScore"/>
        </{B}:transUnionScore>
    </xsl:if>
    <{B}:selectedScore>
        <xsl:value-of select="$MergeScores/{B}:selectedScore"/>
    </{B}:selectedScore>
    <{B}:selectedBureau>
        <xsl:value-of select="$MergeScores/{B}:selectedBureau"/>
    </{B}:selectedBureau>
    <{B}:singleBureau>
        <xsl:value-of select="$MergeScores/{B}:singleBureau"/>
    </{B}:singleBureau>
    <{B}:bureauCount>
        <xsl:value-of select="$MergeScores/{B}:bureauCount"/>
    </{B}:bureauCount>
</{B}:BureauResult>''')))

    f.link("Start", "RenderBureauRequest")
    # Two unlinked branches out of RenderBureauRequest run in parallel (BPEL flow semantics)
    # and join at MergeScores once both have completed or failed.
    f.link("RenderBureauRequest", "PullEquifax")
    f.link("RenderBureauRequest", "PullTransUnion")
    f.link("PullEquifax", "ParseEquifax")
    f.link("PullEquifax", "EquifaxUnavailable", "ERROR")
    f.link("PullTransUnion", "ParseTransUnion")
    f.link("PullTransUnion", "TransUnionUnavailable", "ERROR")
    for branch_end in ("ParseEquifax", "EquifaxUnavailable", "ParseTransUnion",
                       "TransUnionUnavailable"):
        f.link(branch_end, "MergeScores")
    f.link("MergeScores", "ThrowBureauUnavailable", "SUCCESSWITHCONDITION",
           f"xsd:integer($MergeScores/{B}:bureauCount) = 0", "No bureau responded")
    f.link("MergeScores", "RecordBureauPull", "SUCCESSWITHNOCONDITION", label="")
    f.link("RecordBureauPull", "End")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


# ---------------------------------------------------------------------------------
def decision() -> Proc:
    p = Proc(MOD, "creditorigination.DecisionEngine", "20190321103344", callable_=True,
             created_on="Thu Mar 21 10:33:44 EDT 2019",
             description="Tiering, DTI and term cap for retail installment contracts.")
    for ns in (CO_DEC, CO_FLT):
        p.import_xsd(ns)
    D, F = p.tns(CO_DEC), p.tns(CO_FLT)
    p.interface = (f"{{{CO_DEC}}}DecisionRequest", f"{{{CO_DEC}}}DecisionResult")
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.var("Start", element=p.ns(CO_DEC, "DecisionRequest"), parameter="in")
    p.var("End-input", element=p.ns(CO_DEC, "DecisionResult"), parameter="out")
    p.prop("jdbcProperty", DB, SR_JDBC)
    p.var("ComputeDTI-input", element=p.ns(CO_DEC, "DtiResult"))
    p.var("ComputeDTI", element=p.ns(CO_DEC, "DtiResult"))
    for t in ("AssignTierA", "AssignTierB", "AssignTierC", "AssignDecline"):
        p.var(f"{t}-input", element=p.ns(CO_DEC, "TierAssignment"))
        p.var(t, element=p.ns(CO_DEC, "TierAssignment"))
    dec_params = [("applicationId", "VARCHAR"), ("tier", "CHAR"), ("decision", "VARCHAR"),
                  ("apr", "NUMERIC"), ("approvedAmount", "NUMERIC"),
                  ("approvedTermMonths", "INTEGER"), ("dti", "NUMERIC"),
                  ("creditScore", "INTEGER"), ("singleBureau", "BOOLEAN")]
    app_params = [("applicationId", "VARCHAR"), ("ssn", "VARCHAR"), ("vin", "VARCHAR"),
                  ("dealerId", "VARCHAR"), ("requestedAmount", "NUMERIC"),
                  ("requestedTermMonths", "INTEGER"), ("status", "VARCHAR")]
    dec_in = declare_jdbc_update(p, "RecordDecision", dec_params)
    app_in = declare_jdbc_update(p, "RecordApplication", app_params)
    p.var("ThrowMissingIncome-input", element=p.ns(CO_FLT, "MissingIncomeFault"))
    RD, RA = p.tns(dec_in), p.tns(app_in)
    S = "$Start/parameters"

    def tier(name, tier_code, decision_, apr, term_expr, amount_expr):
        tier_xml = (f"    <tier>\n        <xsl:value-of select=\"&quot;{tier_code}&quot;\"/>\n"
                    "    </tier>\n") if tier_code else ""
        return mapper(p, name, f"{p.ns(CO_DEC)}:TierAssignment", CO_DEC, p.map(
            ["Start", "ComputeDTI"], f"{name}-input", f'''
<{D}:TierAssignment>
{tier_xml}    <decision>
        <xsl:value-of select="&quot;{decision_}&quot;"/>
    </decision>
    <apr>
        <xsl:value-of select="{apr}"/>
    </apr>
    <approvedAmount>
        <xsl:value-of select="{amount_expr}"/>
    </approvedAmount>
    <approvedTermMonths>
        <xsl:value-of select="{term_expr}"/>
    </approvedTermMonths>
</{D}:TierAssignment>'''))

    f = Flow("flow")
    f.add("Start", start_event(p))
    f.add("ThrowMissingIncome", throw(p, "ThrowMissingIncome", p.ns(CO_FLT, "MissingIncomeFault"), p.map(
        ["Start"], "ThrowMissingIncome-input", f'''
<{F}:MissingIncomeFault>
    <{F}:applicationId>
        <xsl:value-of select="{S}/applicationId"/>
    </{F}:applicationId>
    <{F}:message>
        <xsl:value-of select="&quot;Monthly income is required to compute DTI&quot;"/>
    </{F}:message>
</{F}:MissingIncomeFault>''')))
    f.add("ComputeDTI", mapper(p, "ComputeDTI", f"{p.ns(CO_DEC)}:DtiResult", CO_DEC, p.map(
        ["Start"], "ComputeDTI-input", f'''
<{D}:DtiResult>
    <dti>
        <xsl:value-of select="number($Start/parameters/monthlyDebt) div number($Start/parameters/monthlyIncome)"/>
    </dti>
</{D}:DtiResult>''')))
    f.add("AssignTierA", tier("AssignTierA", "A", "APPROVED", "5.49",
                              f"{S}/requestedTermMonths", f"{S}/requestedAmount"))
    f.add("AssignTierB", tier("AssignTierB", "B", "APPROVED", "7.99",
                              f"{S}/requestedTermMonths", f"{S}/requestedAmount"))
    f.add("AssignTierC", tier("AssignTierC", "C", "APPROVED", "12.49",
                              f"if (number({S}/requestedTermMonths) > 72) then 72 else {S}/requestedTermMonths",
                              f"{S}/requestedAmount"))
    f.add("AssignDecline", tier("AssignDecline", None, "DECLINED", "0", "0", "0"))

    pick = "($AssignTierA, $AssignTierB, $AssignTierC, $AssignDecline)[1]"
    f.add("RecordDecision", jdbc_update(
        p, "RecordDecision",
        sql("insert into gmf.decision",
            "  (application_id, tier, decision, apr, approved_amount, approved_term_months, dti, credit_score, single_bureau, decided_at)",
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)"),
        dec_params, p.map(["Start", "ComputeDTI", "AssignTierA", "AssignTierB", "AssignTierC",
                           "AssignDecline"], "RecordDecision-input", f'''
<xsl:variable name="assigned" select="{pick}"/>
<{RD}:jdbcUpdateActivityInput>
    <applicationId>
        <xsl:value-of select="{S}/applicationId"/>
    </applicationId>
    <xsl:if test="$assigned/tier">
        <tier>
            <xsl:value-of select="$assigned/tier"/>
        </tier>
    </xsl:if>
    <decision>
        <xsl:value-of select="$assigned/decision"/>
    </decision>
    <apr>
        <xsl:value-of select="$assigned/apr"/>
    </apr>
    <approvedAmount>
        <xsl:value-of select="$assigned/approvedAmount"/>
    </approvedAmount>
    <approvedTermMonths>
        <xsl:value-of select="$assigned/approvedTermMonths"/>
    </approvedTermMonths>
    <dti>
        <xsl:value-of select="$ComputeDTI/dti"/>
    </dti>
    <creditScore>
        <xsl:value-of select="{S}/creditScore"/>
    </creditScore>
    <singleBureau>
        <xsl:value-of select="{S}/singleBureau"/>
    </singleBureau>
</{RD}:jdbcUpdateActivityInput>''')))
    f.add("RecordApplication", jdbc_update(
        p, "RecordApplication",
        sql("insert into gmf.credit_application",
            "  (application_id, ssn, vin, dealer_id, requested_amount, requested_term_months, status, submitted_at)",
            "values (?, ?, ?, ?, ?, ?, ?, current_timestamp)"),
        app_params, p.map(["Start", "AssignTierA", "AssignTierB", "AssignTierC", "AssignDecline"],
                          "RecordApplication-input", f'''
<{RA}:jdbcUpdateActivityInput>
    <applicationId>
        <xsl:value-of select="{S}/applicationId"/>
    </applicationId>
    <ssn>
        <xsl:value-of select="{S}/ssn"/>
    </ssn>
    <vin>
        <xsl:value-of select="{S}/vin"/>
    </vin>
    <dealerId>
        <xsl:value-of select="{S}/dealerId"/>
    </dealerId>
    <requestedAmount>
        <xsl:value-of select="{S}/requestedAmount"/>
    </requestedAmount>
    <requestedTermMonths>
        <xsl:value-of select="{S}/requestedTermMonths"/>
    </requestedTermMonths>
    <status>
        <xsl:value-of select="{pick}/decision"/>
    </status>
</{RA}:jdbcUpdateActivityInput>''')))
    f.add("End", end_activity(p, p.map(
        ["ComputeDTI", "AssignTierA", "AssignTierB", "AssignTierC", "AssignDecline"], "End-input", f'''
<xsl:variable name="assigned" select="{pick}"/>
<{D}:DecisionResult>
    <decision>
        <xsl:value-of select="$assigned/decision"/>
    </decision>
    <xsl:if test="$assigned/tier">
        <tier>
            <xsl:value-of select="$assigned/tier"/>
        </tier>
    </xsl:if>
    <apr>
        <xsl:value-of select="$assigned/apr"/>
    </apr>
    <approvedAmount>
        <xsl:value-of select="$assigned/approvedAmount"/>
    </approvedAmount>
    <approvedTermMonths>
        <xsl:value-of select="$assigned/approvedTermMonths"/>
    </approvedTermMonths>
    <dti>
        <xsl:value-of select="$ComputeDTI/dti"/>
    </dti>
</{D}:DecisionResult>''')))

    f.link("Start", "ThrowMissingIncome", "SUCCESSWITHCONDITION",
           f"string-length({S}/monthlyIncome) = 0 or number({S}/monthlyIncome) <= 0",
           "Income missing")
    f.link("Start", "ComputeDTI", "SUCCESSWITHNOCONDITION", label="")
    f.link("ComputeDTI", "AssignTierA", "SUCCESSWITHCONDITION",
           f"number({S}/creditScore) >= 740 and number($ComputeDTI/dti) < 0.35", "Tier A")
    f.link("ComputeDTI", "AssignTierB", "SUCCESSWITHCONDITION",
           f"number({S}/creditScore) >= 680 and number({S}/creditScore) <= 739", "Tier B")
    f.link("ComputeDTI", "AssignTierC", "SUCCESSWITHCONDITION",
           f"number({S}/creditScore) >= 620 and number({S}/creditScore) <= 679 and number($ComputeDTI/dti) < 0.45",
           "Tier C")
    f.link("ComputeDTI", "AssignDecline", "SUCCESSWITHNOCONDITION", label="Decline")
    for t in ("AssignTierA", "AssignTierB", "AssignTierC", "AssignDecline"):
        f.link(t, "RecordDecision")
    f.link("RecordDecision", "RecordApplication")
    f.link("RecordApplication", "End")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


# ---------------------------------------------------------------------------------
def validate_vin() -> Proc:
    p = Proc(MOD, "creditorigination.ValidateVIN", "20190315161250", callable_=True,
             created_on="Fri Mar 15 16:12:50 EDT 2019",
             description="VIN length, alphabet and check digit (position 9).")
    p.import_xsd(CO_VEH)
    V = p.tns(CO_VEH)
    p.interface = (f"{{{CO_VEH}}}VinValidationRequest", f"{{{CO_VEH}}}VinValidationResult")
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.var("Start", element=p.ns(CO_VEH, "VinValidationRequest"), parameter="in")
    p.var("End-input", element=p.ns(CO_VEH, "VinValidationResult"), parameter="out")
    for n in ("ComputeCheckDigit", "RejectVinFormat"):
        p.var(f"{n}-input", element=p.ns(CO_VEH, "VinValidationResult"))
        p.var(n, element=p.ns(CO_VEH, "VinValidationResult"))

    f = Flow("flow")
    f.add("Start", start_event(p))
    f.add("ComputeCheckDigit", mapper(p, "ComputeCheckDigit", f"{p.ns(CO_VEH)}:VinValidationResult", CO_VEH, p.map(
        ["Start"], "ComputeCheckDigit-input", f'''
<xsl:variable name="vin" select="string($Start/{V}:vin)"/>
<xsl:variable name="values" select="translate($vin, 'ABCDEFGHJKLMNPRSTUVWXYZ', '12345678123457923456789')"/>
<xsl:variable name="weights" select="(8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)"/>
<xsl:variable name="remainder" select="xsd:integer(sum(for $i in 1 to 17 return xsd:integer(substring($values, $i, 1)) * $weights[$i])) mod 11"/>
<xsl:variable name="expected" select="if ($remainder = 10) then 'X' else string($remainder)"/>
<{V}:VinValidationResult>
    <{V}:vin>
        <xsl:value-of select="$vin"/>
    </{V}:vin>
    <{V}:valid>
        <xsl:value-of select="substring($vin, 9, 1) = $expected"/>
    </{V}:valid>
    <xsl:if test="substring($vin, 9, 1) != $expected">
        <{V}:reason>
            <xsl:value-of select="&quot;CHECK_DIGIT_MISMATCH&quot;"/>
        </{V}:reason>
    </xsl:if>
    <{V}:computedCheckDigit>
        <xsl:value-of select="$expected"/>
    </{V}:computedCheckDigit>
</{V}:VinValidationResult>''')))
    f.add("RejectVinFormat", mapper(p, "RejectVinFormat", f"{p.ns(CO_VEH)}:VinValidationResult", CO_VEH, p.map(
        ["Start"], "RejectVinFormat-input", f'''
<{V}:VinValidationResult>
    <{V}:vin>
        <xsl:value-of select="$Start/{V}:vin"/>
    </{V}:vin>
    <{V}:valid>
        <xsl:value-of select="false()"/>
    </{V}:valid>
    <{V}:reason>
        <xsl:value-of select="&quot;INVALID_FORMAT&quot;"/>
    </{V}:reason>
</{V}:VinValidationResult>''')))
    f.add("End", end_activity(p, p.map(["ComputeCheckDigit", "RejectVinFormat"], "End-input", f'''
<xsl:variable name="result" select="($ComputeCheckDigit, $RejectVinFormat)[1]"/>
<{V}:VinValidationResult>
    <{V}:vin>
        <xsl:value-of select="$result/{V}:vin"/>
    </{V}:vin>
    <{V}:valid>
        <xsl:value-of select="$result/{V}:valid"/>
    </{V}:valid>
    <xsl:if test="$result/{V}:reason">
        <{V}:reason>
            <xsl:value-of select="$result/{V}:reason"/>
        </{V}:reason>
    </xsl:if>
    <xsl:if test="$result/{V}:computedCheckDigit">
        <{V}:computedCheckDigit>
            <xsl:value-of select="$result/{V}:computedCheckDigit"/>
        </{V}:computedCheckDigit>
    </xsl:if>
</{V}:VinValidationResult>''')))
    f.link("Start", "ComputeCheckDigit", "SUCCESSWITHCONDITION",
           f"string-length($Start/{V}:vin) = 17 and matches($Start/{V}:vin, '^[A-HJ-NPR-Z0-9]+$')",
           "17 characters, VIN alphabet")
    f.link("Start", "RejectVinFormat", "SUCCESSWITHNOCONDITION", label="")
    f.link("ComputeCheckDigit", "End")
    f.link("RejectVinFormat", "End")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


# ---------------------------------------------------------------------------------
def audit_log() -> Proc:
    p = Proc(MOD, "creditorigination.AuditLog", "20190314120416", callable_=True,
             created_on="Thu Mar 14 12:04:16 EDT 2019",
             description="Writes one origination audit record.")
    p.import_xsd(CO_AUD)
    AU = p.tns(CO_AUD)
    p.interface = (f"{{{CO_AUD}}}AuditEvent", "")
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.var("Start", element=p.ns(CO_AUD, "AuditEvent"), parameter="in")
    p.prop("jdbcProperty", DB, SR_JDBC)
    params = [("eventType", "VARCHAR"), ("applicationId", "VARCHAR"), ("ssn", "VARCHAR"),
              ("payload", "VARCHAR")]
    w_in = declare_jdbc_update(p, "WriteAuditRecord", params)
    W = p.tns(w_in)
    f = Flow("flow")
    f.add("Start", start_event(p))
    f.add("WriteAuditRecord", jdbc_update(
        p, "WriteAuditRecord",
        sql("insert into gmf.audit_log (event_type, application_id, ssn, payload, created_at)",
            "values (?, ?, ?, ?, current_timestamp)"),
        params, p.map(["Start"], "WriteAuditRecord-input", f'''
<{W}:jdbcUpdateActivityInput>
    <eventType>
        <xsl:value-of select="$Start/{AU}:eventType"/>
    </eventType>
    <applicationId>
        <xsl:value-of select="$Start/{AU}:applicationId"/>
    </applicationId>
    <ssn>
        <xsl:value-of select="$Start/{AU}:ssn"/>
    </ssn>
    <payload>
        <xsl:value-of select="$Start/{AU}:payload"/>
    </payload>
</{W}:jdbcUpdateActivityInput>''')))
    f.add("End", end_activity(p))
    f.link("Start", "WriteAuditRecord")
    f.link("WriteAuditRecord", "End")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


PROCESSES = [intake, bureau, decision, validate_vin, audit_log]
