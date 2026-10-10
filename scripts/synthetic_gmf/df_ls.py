"""DealerFunding and LoanServicing module processes."""
from __future__ import annotations

from .bwlib import N
from .bwproc import (
    LOG_NS,
    SR_JDBC,
    SR_JMS,
    TIMER_NS,
    XPATH_LANG,
    Flow,
    Proc,
    bw_config,
    declare_jdbc_query,
    declare_jdbc_update,
    declare_jms_receive,
    declare_jms_send,
    declare_log,
    event_source,
    group_marker,
    group_scope,
    jdbc_query,
    jdbc_update,
    jms_receive_config,
    jms_send,
    log,
    mapper,
    sources_node,
    targets_node,
    throw,
)
from .names import DF_EVT, DF_FLT, DF_PRM, DF_REQ, DF_STS, LS_ACC, LS_COL, LS_PAY, LS_PRM
from .rest import fault_body, pick_body, rest_reply, rest_service


def sql(*lines):
    return "\n".join(lines)


def jms_prop(p: Proc, resource):
    p.prop("jmsConnection", resource, SR_JMS)


# ---------------------------------------------------------------------------------
def dealer_funding() -> Proc:
    p = Proc("DealerFunding", "dealerfunding.DealerFundingRequest", "20200211082245",
             callable_=False, created_on="Tue Feb 11 08:22:45 EST 2020",
             description="Funds a dealer deal from the funding request queue.")
    schema = "platform:/resource/DealerFunding/Schemas"
    for ns in (DF_REQ, DF_EVT, DF_FLT):
        p.import_xsd(ns)
    R, E, F = p.tns(DF_REQ), p.tns(DF_EVT), p.tns(DF_FLT)
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    jms_prop(p, "dealerfunding.GMFEMSConnection")
    p.prop("jdbcProperty", "dealerfunding.GMFFundingDB", SR_JDBC)
    declare_jms_receive(p, "ReceiveFundingRequest", DF_REQ, "FundingRequest")
    dealer_cols = [("dealer_id", "VARCHAR"), ("dealer_name", "VARCHAR"), ("status", "VARCHAR")]
    deal_cols = [("deal_id", "VARCHAR"), ("dealer_id", "VARCHAR"),
                 ("approved_amount", "NUMERIC"), ("status", "VARCHAR")]
    fund_cols = [("funding_id", "INTEGER"), ("status", "VARCHAR")]
    ins_params = [("dealId", "VARCHAR"), ("dealerId", "VARCHAR"), ("amount", "NUMERIC")]
    q1 = declare_jdbc_query(p, "LookupDealer", [("dealerId", "VARCHAR")], dealer_cols)[0]
    q2 = declare_jdbc_query(p, "LookupDeal", [("dealId", "VARCHAR")], deal_cols)[0]
    q3 = declare_jdbc_query(p, "CheckExistingFunding", [("dealId", "VARCHAR")], fund_cols)[0]
    u1 = declare_jdbc_update(p, "InsertFunding", ins_params)
    s1 = declare_jms_send(p, "PublishFundingEvent", DF_EVT, "FundingEvent")
    s2 = declare_jms_send(p, "PublishFundingRejected", DF_EVT, "FundingEvent")
    declare_log(p, "LogDuplicateFunding")
    p.var("ThrowDealerInactive-input", element=p.ns(DF_FLT, "DealerInactiveFault"))
    Q1, Q2, Q3, U1, S1, S2, L = (p.tns(q1), p.tns(q2), p.tns(q3), p.tns(u1), p.tns(s1),
                                 p.tns(s2), p.tns(LOG_NS))
    msg = f"$ReceiveFundingRequest/Body/{R}:FundingRequest"

    def event(prefix, status, reason=None):
        reason_xml = (f'''
            <{E}:reason>
                <xsl:value-of select="&quot;{reason}&quot;"/>
            </{E}:reason>''' if reason else "")
        return f'''
<{prefix}:ActivityInput>
    <Body>
        <{E}:FundingEvent>
            <{E}:dealId>
                <xsl:value-of select="{msg}/{R}:dealId"/>
            </{E}:dealId>
            <{E}:dealerId>
                <xsl:value-of select="{msg}/{R}:dealerId"/>
            </{E}:dealerId>
            <{E}:amount>
                <xsl:value-of select="{msg}/{R}:fundingAmount"/>
            </{E}:amount>
            <{E}:status>
                <xsl:value-of select="&quot;{status}&quot;"/>
            </{E}:status>{reason_xml}
            <{E}:eventTime>
                <xsl:value-of select="current-dateTime()"/>
            </{E}:eventTime>
        </{E}:FundingEvent>
    </Body>
</{prefix}:ActivityInput>'''

    f = Flow("flow")
    f.add("ReceiveFundingRequest", event_source(
        p, "ReceiveFundingRequest", "ReceiveFundingRequest",
        jms_receive_config("ReceiveFundingRequest", "gmf.dealer.funding.request",
                           "MsgType = 'FUNDING_REQUEST'",
                           f"{schema}/FundingRequest.xsd#//FundingRequest;XSDElementDeclaration")))
    f.add("LookupDealer", jdbc_query(
        p, "LookupDealer",
        sql("select dealer_id, dealer_name, status", "  from gmf.dealer",
            " where dealer_id like ?"),
        [("dealerId", "VARCHAR")], dealer_cols,
        p.map(["ReceiveFundingRequest"], "LookupDealer-input", f'''
<{Q1}:jdbcQueryActivityInput>
    <dealerId>
        <xsl:value-of select="concat(normalize-space({msg}/{R}:dealerId), '%')"/>
    </dealerId>
</{Q1}:jdbcQueryActivityInput>''')))
    f.add("ThrowDealerInactive", throw(p, "ThrowDealerInactive", p.ns(DF_FLT, "DealerInactiveFault"), p.map(
        ["ReceiveFundingRequest", "LookupDealer"], "ThrowDealerInactive-input", f'''
<{F}:DealerInactiveFault>
    <{F}:dealerId>
        <xsl:value-of select="{msg}/{R}:dealerId"/>
    </{F}:dealerId>
    <xsl:if test="$LookupDealer/Record[1]/status">
        <{F}:status>
            <xsl:value-of select="$LookupDealer/Record[1]/status"/>
        </{F}:status>
    </xsl:if>
    <{F}:message>
        <xsl:value-of select="&quot;Dealer is not active for funding&quot;"/>
    </{F}:message>
</{F}:DealerInactiveFault>''')))
    f.add("LookupDeal", jdbc_query(
        p, "LookupDeal",
        sql("select deal_id, dealer_id, approved_amount, status", "  from gmf.deal",
            " where deal_id = ?"),
        [("dealId", "VARCHAR")], deal_cols,
        p.map(["ReceiveFundingRequest"], "LookupDeal-input", f'''
<{Q2}:jdbcQueryActivityInput>
    <dealId>
        <xsl:value-of select="{msg}/{R}:dealId"/>
    </dealId>
</{Q2}:jdbcQueryActivityInput>''')))
    f.add("PublishFundingRejected", jms_send(
        p, "PublishFundingRejected", "gmf.funding.events", "Topic",
        f"{schema}/FundingEvent.xsd#//FundingEvent;XSDElementDeclaration",
        p.map(["ReceiveFundingRequest"], "PublishFundingRejected-input",
              event(S2, "REJECTED", "AMOUNT_OUTSIDE_TOLERANCE"))))
    f.add("CheckExistingFunding", jdbc_query(
        p, "CheckExistingFunding",
        sql("select funding_id, status", "  from gmf.funding", " where deal_id = ?"),
        [("dealId", "VARCHAR")], fund_cols,
        p.map(["ReceiveFundingRequest"], "CheckExistingFunding-input", f'''
<{Q3}:jdbcQueryActivityInput>
    <dealId>
        <xsl:value-of select="{msg}/{R}:dealId"/>
    </dealId>
</{Q3}:jdbcQueryActivityInput>''')))
    f.add("LogDuplicateFunding", log(p, "LogDuplicateFunding", "Warn", p.map(
        ["ReceiveFundingRequest"], "LogDuplicateFunding-input", f'''
<{L}:ActivityInput>
    <msgCode>
        <xsl:value-of select="&quot;DF-409&quot;"/>
    </msgCode>
    <message>
        <xsl:value-of select="concat('Deal ', {msg}/{R}:dealId, ' already funded; request ignored')"/>
    </message>
</{L}:ActivityInput>''')))
    f.add("InsertFunding", jdbc_update(
        p, "InsertFunding",
        sql("insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at)",
            "values (?, ?, ?, 'FUNDED', current_timestamp)"),
        ins_params, p.map(["ReceiveFundingRequest"], "InsertFunding-input", f'''
<{U1}:jdbcUpdateActivityInput>
    <dealId>
        <xsl:value-of select="{msg}/{R}:dealId"/>
    </dealId>
    <dealerId>
        <xsl:value-of select="{msg}/{R}:dealerId"/>
    </dealerId>
    <amount>
        <xsl:value-of select="{msg}/{R}:fundingAmount"/>
    </amount>
</{U1}:jdbcUpdateActivityInput>''')))
    f.add("PublishFundingEvent", jms_send(
        p, "PublishFundingEvent", "gmf.funding.events", "Topic",
        f"{schema}/FundingEvent.xsd#//FundingEvent;XSDElementDeclaration",
        p.map(["ReceiveFundingRequest"], "PublishFundingEvent-input", event(S1, "FUNDED"))))

    f.link("ReceiveFundingRequest", "LookupDealer")
    f.link("LookupDealer", "LookupDeal", "SUCCESSWITHCONDITION",
           "$LookupDealer/Record[1]/status = 'ACTIVE'", "Dealer active")
    f.link("LookupDealer", "ThrowDealerInactive", "SUCCESSWITHNOCONDITION", label="")
    f.link("LookupDeal", "CheckExistingFunding", "SUCCESSWITHCONDITION",
           f"abs(number({msg}/{R}:fundingAmount) - number($LookupDeal/Record[1]/approved_amount)) "
           f"<= number($LookupDeal/Record[1]/approved_amount) * 0.02", "Within 2% of approved")
    f.link("LookupDeal", "PublishFundingRejected", "SUCCESSWITHNOCONDITION", label="")
    f.link("CheckExistingFunding", "InsertFunding", "SUCCESSWITHCONDITION",
           "count($CheckExistingFunding/Record) = 0", "Not yet funded")
    f.link("CheckExistingFunding", "LogDuplicateFunding", "SUCCESSWITHNOCONDITION", label="")
    f.link("InsertFunding", "PublishFundingEvent")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


# ---------------------------------------------------------------------------------
def funding_status() -> Proc:
    p = Proc("DealerFunding", "dealerfunding.FundingStatusLookup", "20200224151530",
             callable_=False, created_on="Mon Feb 24 15:15:30 EST 2020",
             description="Funding status for a deal (GET /funding/{dealId}).")
    plt = rest_service(p, port_type="funding", apipath="/funding/{dealId}", basepath="Funding",
                       resource="Service Descriptors/dealerfunding.FundingStatusLookup-funding.json",
                       operation="get", method_doc="Funding status for a dealer deal.",
                       params_element="fundingGetParameters", params_ns=DF_PRM,
                       response_element="FundingStatusResponse", response_ns=DF_STS)
    PR, ST = p.tns(DF_PRM), p.tns(DF_STS)
    plt_ns = p.ns(plt)
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.prop("jdbcProperty", "dealerfunding.GMFFundingDB", SR_JDBC)
    cols = [("funding_id", "INTEGER"), ("deal_id", "VARCHAR"), ("dealer_id", "VARCHAR"),
            ("amount", "NUMERIC"), ("status", "VARCHAR"), ("funded_at", "TIMESTAMP")]
    q = declare_jdbc_query(p, "QueryFundingStatus", [("dealId", "VARCHAR")], cols)[0]
    p.var("ReplyNotFound-input", message=f"{plt_ns}:get4XXFaultMessage")
    Q = p.tns(q)
    p.tns("http://tns.tibco.com/bw/REST")
    rec = "$QueryFundingStatus/Record[1]"
    f = Flow("flow1")
    f.add("QueryFundingStatus", jdbc_query(
        p, "QueryFundingStatus",
        sql("select f.funding_id, f.deal_id, f.dealer_id, f.amount, f.status, f.funded_at",
            "  from gmf.funding f", " where f.deal_id = ?"),
        [("dealId", "VARCHAR")], cols, p.map(["get.parameters"], "QueryFundingStatus-input", f'''
<{Q}:jdbcQueryActivityInput>
    <dealId>
        <xsl:value-of select="$get.parameters/{PR}:dealId"/>
    </dealId>
</{Q}:jdbcQueryActivityInput>''')))
    f.add("getOut", rest_reply(p, "getOut", plt=plt, port_type="funding", operation="get",
                               params=["QueryFundingStatus"], element_body=f'''
<{ST}:FundingStatusResponse>
    <{ST}:dealId>
        <xsl:value-of select="{rec}/deal_id"/>
    </{ST}:dealId>
    <{ST}:fundingId>
        <xsl:value-of select="{rec}/funding_id"/>
    </{ST}:fundingId>
    <{ST}:dealerId>
        <xsl:value-of select="{rec}/dealer_id"/>
    </{ST}:dealerId>
    <{ST}:amount>
        <xsl:value-of select="{rec}/amount"/>
    </{ST}:amount>
    <{ST}:status>
        <xsl:value-of select="{rec}/status"/>
    </{ST}:status>
    <xsl:if test="{rec}/funded_at">
        <{ST}:fundedAt>
            <xsl:value-of select="{rec}/funded_at"/>
        </{ST}:fundedAt>
    </xsl:if>
</{ST}:FundingStatusResponse>'''))
    f.add("ReplyNotFound", rest_reply(
        p, "ReplyNotFound", plt=plt, port_type="funding", operation="get",
        params=["get.parameters"], fault="client", variable="ReplyNotFound-input",
        element_body=fault_body(p, "client", "404",
                                f"concat(&quot;No funding for deal &quot;, $get.parameters/{PR}:dealId)")))
    f.link("QueryFundingStatus", "getOut", "SUCCESSWITHCONDITION",
           "count($QueryFundingStatus/Record) > 0", "Found")
    f.link("QueryFundingStatus", "ReplyNotFound", "SUCCESSWITHNOCONDITION", label="")
    p.body = pick_body(p, port_type="funding", operation="get", inner=f)
    return p


# ---------------------------------------------------------------------------------
def payment_posting() -> Proc:
    p = Proc("LoanServicing", "loanservicing.PaymentPosting", "20200603094118",
             callable_=False, created_on="Wed Jun 03 09:41:18 EDT 2020",
             description="Posts inbound payments: late fee, then fees, interest, principal.")
    schema = "platform:/resource/LoanServicing/Schemas"
    p.import_xsd(LS_PAY)
    P = p.tns(LS_PAY)
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    jms_prop(p, "loanservicing.GMFEMSConnection")
    p.prop("jdbcProperty", "loanservicing.GMFServicingDB", SR_JDBC)
    declare_jms_receive(p, "ReceivePayment", LS_PAY, "Payment")
    acct_cols = [("account_number", "VARCHAR"), ("fees_due", "NUMERIC"),
                 ("accrued_interest", "NUMERIC"), ("principal_balance", "NUMERIC"),
                 ("next_due_date", "DATE"), ("status", "VARCHAR")]
    q = declare_jdbc_query(p, "LoadAccount", [("accountNumber", "VARCHAR")], acct_cols)[0]
    for n in ("AssessLateFee", "NoLateFee"):
        p.var(f"{n}-input", element=p.ns(LS_PAY, "LateFeeAssessment"))
        p.var(n, element=p.ns(LS_PAY, "LateFeeAssessment"))
    p.var("AllocatePayment-input", element=p.ns(LS_PAY, "PaymentAllocation"))
    p.var("AllocatePayment", element=p.ns(LS_PAY, "PaymentAllocation"))
    pay_params = [("paymentId", "VARCHAR"), ("accountNumber", "VARCHAR"), ("amount", "NUMERIC"),
                  ("channel", "VARCHAR"), ("receivedDate", "DATE"), ("feeApplied", "NUMERIC"),
                  ("lateFeeApplied", "NUMERIC"), ("interestApplied", "NUMERIC"),
                  ("principalApplied", "NUMERIC")]
    upd_params = [("feesDue", "NUMERIC"), ("accruedInterest", "NUMERIC"),
                  ("principalBalance", "NUMERIC"), ("lastPaymentDate", "DATE"),
                  ("accountNumber", "VARCHAR")]
    u1 = declare_jdbc_update(p, "InsertPayment", pay_params)
    u2 = declare_jdbc_update(p, "UpdateLoanAccount", upd_params)
    declare_log(p, "LogPaymentPosted")
    Q, U1, U2, L = p.tns(q), p.tns(u1), p.tns(u2), p.tns(LOG_NS)
    msg = f"$ReceivePayment/Body/{P}:Payment"
    acct = "$LoadAccount/Record[1]"
    days_late = (f"days-from-duration(xsd:date({msg}/{P}:receivedDate) - "
                 f"xsd:date({acct}/next_due_date))")

    def fee(name, amount, days):
        return mapper(p, name, f"{p.ns(LS_PAY)}:LateFeeAssessment", LS_PAY, p.map(
            ["ReceivePayment", "LoadAccount"], f"{name}-input", f'''
<{P}:LateFeeAssessment>
    <{P}:lateFee>
        <xsl:value-of select="{amount}"/>
    </{P}:lateFee>
    <{P}:daysLate>
        <xsl:value-of select="{days}"/>
    </{P}:daysLate>
</{P}:LateFeeAssessment>'''))

    g = Flow("flow1")
    g.add("GroupStart", group_marker(p, "GroupStart", "groupStart"))
    g.add("LoadAccount", jdbc_query(
        p, "LoadAccount",
        sql("select account_number, fees_due, accrued_interest, principal_balance, next_due_date, status",
            "  from gmf.loan_account", " where account_number = ?", "   for update"),
        [("accountNumber", "VARCHAR")], acct_cols,
        p.map(["ReceivePayment"], "LoadAccount-input", f'''
<{Q}:jdbcQueryActivityInput>
    <accountNumber>
        <xsl:value-of select="{msg}/{P}:accountNumber"/>
    </accountNumber>
</{Q}:jdbcQueryActivityInput>''')))
    g.add("AssessLateFee", fee("AssessLateFee", "25.00", days_late))
    g.add("NoLateFee", fee("NoLateFee", "0.00", f"max((0, {days_late}))"))
    g.add("AllocatePayment", mapper(p, "AllocatePayment", f"{p.ns(LS_PAY)}:PaymentAllocation", LS_PAY, p.map(
        ["ReceivePayment", "LoadAccount", "AssessLateFee", "NoLateFee"], "AllocatePayment-input", f'''
<xsl:variable name="amount" select="xsd:decimal({msg}/{P}:amount)"/>
<xsl:variable name="lateFee" select="xsd:decimal(($AssessLateFee, $NoLateFee)[1]/{P}:lateFee)"/>
<xsl:variable name="fees" select="xsd:decimal({acct}/fees_due) + $lateFee"/>
<xsl:variable name="interest" select="xsd:decimal({acct}/accrued_interest)"/>
<xsl:variable name="feeApplied" select="min(($amount, $fees))"/>
<xsl:variable name="interestApplied" select="min(($amount - $feeApplied, $interest))"/>
<xsl:variable name="principalApplied" select="$amount - $feeApplied - $interestApplied"/>
<{P}:PaymentAllocation>
    <{P}:feeApplied>
        <xsl:value-of select="$feeApplied"/>
    </{P}:feeApplied>
    <{P}:lateFeeApplied>
        <xsl:value-of select="min(($feeApplied, $lateFee))"/>
    </{P}:lateFeeApplied>
    <{P}:interestApplied>
        <xsl:value-of select="$interestApplied"/>
    </{P}:interestApplied>
    <{P}:principalApplied>
        <xsl:value-of select="$principalApplied"/>
    </{P}:principalApplied>
    <{P}:newFeesDue>
        <xsl:value-of select="$fees - $feeApplied"/>
    </{P}:newFeesDue>
    <{P}:newAccruedInterest>
        <xsl:value-of select="$interest - $interestApplied"/>
    </{P}:newAccruedInterest>
    <{P}:newPrincipalBalance>
        <xsl:value-of select="xsd:decimal({acct}/principal_balance) - $principalApplied"/>
    </{P}:newPrincipalBalance>
</{P}:PaymentAllocation>''')))
    g.add("InsertPayment", jdbc_update(
        p, "InsertPayment",
        sql("insert into gmf.payment",
            "  (payment_id, account_number, amount, channel, received_date, fee_applied, late_fee_applied, interest_applied, principal_applied, posted_at)",
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)"),
        pay_params, p.map(["ReceivePayment", "AllocatePayment"], "InsertPayment-input", f'''
<{U1}:jdbcUpdateActivityInput>
    <paymentId>
        <xsl:value-of select="{msg}/{P}:paymentId"/>
    </paymentId>
    <accountNumber>
        <xsl:value-of select="{msg}/{P}:accountNumber"/>
    </accountNumber>
    <amount>
        <xsl:value-of select="{msg}/{P}:amount"/>
    </amount>
    <channel>
        <xsl:value-of select="{msg}/{P}:channel"/>
    </channel>
    <receivedDate>
        <xsl:value-of select="{msg}/{P}:receivedDate"/>
    </receivedDate>
    <feeApplied>
        <xsl:value-of select="$AllocatePayment/{P}:feeApplied"/>
    </feeApplied>
    <lateFeeApplied>
        <xsl:value-of select="$AllocatePayment/{P}:lateFeeApplied"/>
    </lateFeeApplied>
    <interestApplied>
        <xsl:value-of select="$AllocatePayment/{P}:interestApplied"/>
    </interestApplied>
    <principalApplied>
        <xsl:value-of select="$AllocatePayment/{P}:principalApplied"/>
    </principalApplied>
</{U1}:jdbcUpdateActivityInput>''')))
    g.add("UpdateLoanAccount", jdbc_update(
        p, "UpdateLoanAccount",
        sql("update gmf.loan_account",
            "   set fees_due = ?, accrued_interest = ?, principal_balance = ?, last_payment_date = ?",
            " where account_number = ?"),
        upd_params, p.map(["ReceivePayment", "AllocatePayment"], "UpdateLoanAccount-input", f'''
<{U2}:jdbcUpdateActivityInput>
    <feesDue>
        <xsl:value-of select="$AllocatePayment/{P}:newFeesDue"/>
    </feesDue>
    <accruedInterest>
        <xsl:value-of select="$AllocatePayment/{P}:newAccruedInterest"/>
    </accruedInterest>
    <principalBalance>
        <xsl:value-of select="$AllocatePayment/{P}:newPrincipalBalance"/>
    </principalBalance>
    <lastPaymentDate>
        <xsl:value-of select="{msg}/{P}:receivedDate"/>
    </lastPaymentDate>
    <accountNumber>
        <xsl:value-of select="{msg}/{P}:accountNumber"/>
    </accountNumber>
</{U2}:jdbcUpdateActivityInput>''')))
    g.add("GroupEnd", group_marker(p, "GroupEnd", "groupEnd"))
    g.link("GroupStart", "LoadAccount")
    g.link("LoadAccount", "AssessLateFee", "SUCCESSWITHCONDITION", f"{days_late} >= 10",
           "Past 10-day grace")
    g.link("LoadAccount", "NoLateFee", "SUCCESSWITHNOCONDITION", label="")
    g.link("AssessLateFee", "AllocatePayment")
    g.link("NoLateFee", "AllocatePayment")
    g.link("AllocatePayment", "InsertPayment")
    g.link("InsertPayment", "UpdateLoanAccount")
    g.link("UpdateLoanAccount", "GroupEnd")

    f = Flow("flow")
    f.add("ReceivePayment", event_source(
        p, "ReceivePayment", "ReceivePayment",
        jms_receive_config("ReceivePayment", "gmf.payments.inbound",
                           "PaymentChannel IN ('ACH', 'DEBIT', 'LOCKBOX')",
                           f"{schema}/Payment.xsd#//Payment;XSDElementDeclaration")))
    f.add("PostPaymentTransaction", group_scope(p, "PostPaymentTransaction", "localTransaction", g))
    f.add("LogPaymentPosted", log(p, "LogPaymentPosted", "Info", p.map(
        ["ReceivePayment"], "LogPaymentPosted-input", f'''
<{L}:ActivityInput>
    <msgCode>
        <xsl:value-of select="&quot;LS-200&quot;"/>
    </msgCode>
    <message>
        <xsl:value-of select="concat('Payment ', {msg}/{P}:paymentId, ' posted')"/>
    </message>
</{L}:ActivityInput>''')))
    f.link("ReceivePayment", "PostPaymentTransaction")
    f.link("PostPaymentTransaction", "LogPaymentPosted")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


# ---------------------------------------------------------------------------------
def account_lookup() -> Proc:
    p = Proc("LoanServicing", "loanservicing.AccountLookup", "20200615113902",
             callable_=False, created_on="Mon Jun 15 11:39:02 EDT 2020",
             description="Account summary for servicing agents (GET /accounts/{accountNumber}).")
    plt = rest_service(p, port_type="accounts", apipath="/accounts/{accountNumber}",
                       basepath="Accounts",
                       resource="Service Descriptors/loanservicing.AccountLookup-accounts.json",
                       operation="get", method_doc="Loan account summary.",
                       params_element="accountsGetParameters", params_ns=LS_PRM,
                       response_element="AccountSummary", response_ns=LS_ACC)
    PR, AC = p.tns(LS_PRM), p.tns(LS_ACC)
    plt_ns = p.ns(plt)
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.prop("jdbcProperty", "loanservicing.GMFServicingDB", SR_JDBC)
    cols = [("account_number", "VARCHAR"), ("principal_balance", "NUMERIC"),
            ("next_due_date", "DATE"), ("days_past_due", "INTEGER"), ("status", "VARCHAR"),
            ("first_name", "VARCHAR"), ("last_name", "VARCHAR"), ("ssn", "VARCHAR")]
    q = declare_jdbc_query(p, "QueryAccount", [("accountNumber", "VARCHAR")], cols)[0]
    p.var("ReplyAccountNotFound-input", message=f"{plt_ns}:get4XXFaultMessage")
    Q = p.tns(q)
    p.tns("http://tns.tibco.com/bw/REST")
    rec = "$QueryAccount/Record[1]"
    f = Flow("flow1")
    f.add("QueryAccount", jdbc_query(
        p, "QueryAccount",
        sql("select la.account_number, la.principal_balance, la.next_due_date, la.days_past_due, la.status,",
            "       a.first_name, a.last_name, a.ssn",
            "  from gmf.loan_account la",
            "  join gmf.applicant a on a.applicant_id = la.applicant_id",
            " where la.account_number = ?"),
        [("accountNumber", "VARCHAR")], cols, p.map(["get.parameters"], "QueryAccount-input", f'''
<{Q}:jdbcQueryActivityInput>
    <accountNumber>
        <xsl:value-of select="$get.parameters/{PR}:accountNumber"/>
    </accountNumber>
</{Q}:jdbcQueryActivityInput>''')))
    f.add("getOut", rest_reply(p, "getOut", plt=plt, port_type="accounts", operation="get",
                               params=["QueryAccount"], element_body=f'''
<{AC}:AccountSummary>
    <{AC}:accountNumber>
        <xsl:value-of select="{rec}/account_number"/>
    </{AC}:accountNumber>
    <{AC}:borrowerName>
        <xsl:value-of select="concat({rec}/first_name, ' ', {rec}/last_name)"/>
    </{AC}:borrowerName>
    <{AC}:maskedSsn>
        <xsl:value-of select="concat('***-**-', substring({rec}/ssn, 8, 4))"/>
    </{AC}:maskedSsn>
    <{AC}:principalBalance>
        <xsl:value-of select="{rec}/principal_balance"/>
    </{AC}:principalBalance>
    <{AC}:nextDueDate>
        <xsl:value-of select="{rec}/next_due_date"/>
    </{AC}:nextDueDate>
    <{AC}:daysPastDue>
        <xsl:value-of select="{rec}/days_past_due"/>
    </{AC}:daysPastDue>
    <{AC}:status>
        <xsl:value-of select="{rec}/status"/>
    </{AC}:status>
</{AC}:AccountSummary>'''))
    f.add("ReplyAccountNotFound", rest_reply(
        p, "ReplyAccountNotFound", plt=plt, port_type="accounts", operation="get",
        params=[], fault="client", variable="ReplyAccountNotFound-input",
        element_body=fault_body(p, "client", "404", "&quot;Account not found&quot;")))
    f.link("QueryAccount", "getOut", "SUCCESSWITHCONDITION",
           "count($QueryAccount/Record) > 0", "Found")
    f.link("QueryAccount", "ReplyAccountNotFound", "SUCCESSWITHNOCONDITION", label="")
    p.body = pick_body(p, port_type="accounts", operation="get", inner=f)
    return p


# ---------------------------------------------------------------------------------
def nightly_scan() -> Proc:
    p = Proc("LoanServicing", "loanservicing.NightlyDelinquencyScan", "20200722190455",
             callable_=False, created_on="Wed Jul 22 19:04:55 EDT 2020",
             description="Daily 02:00 scan: refers accounts 30+ days past due to collections.")
    schema = "platform:/resource/LoanServicing/Schemas"
    p.import_xsd(LS_COL)
    C = p.tns(LS_COL)
    p.add_type(f'''
<xs:schema attributeFormDefault="unqualified"
    elementFormDefault="unqualified"
    targetNamespace="{TIMER_NS}"
    xmlns:tns="{TIMER_NS}" xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:complexType name="TimerOutputSchemaType">
        <xs:sequence>
            <xs:element name="Time" type="xs:long"/>
            <xs:element name="FormattedDate" type="xs:string"/>
            <xs:element name="Hour" type="xs:int"/>
            <xs:element name="Minute" type="xs:int"/>
            <xs:element name="Second" type="xs:int"/>
            <xs:element name="Week" type="xs:int"/>
            <xs:element name="Month" type="xs:int"/>
            <xs:element name="Year" type="xs:int"/>
            <xs:element name="Date" type="xs:int"/>
            <xs:element name="DayOfWeek" type="xs:int"/>
            <xs:element name="DayOfMonth" type="xs:int"/>
            <xs:element name="Millisecond" type="xs:int"/>
        </xs:sequence>
    </xs:complexType>
    <xs:element name="TimerOutputSchema" type="tns:TimerOutputSchemaType"/>
</xs:schema>''')
    p.var("_error", element=p.ns("http://www.tibco.com/pe/EngineTypes", "ErrorReport"))
    p.var("DailyAt0200", element=p.ns(TIMER_NS, "TimerOutputSchema"))
    p.prop("jdbcProperty", "loanservicing.GMFServicingDB", SR_JDBC)
    jms_prop(p, "loanservicing.GMFEMSConnection")
    cols = [("account_number", "VARCHAR"), ("days_past_due", "INTEGER"),
            ("monthly_payment", "NUMERIC"), ("first_name", "VARCHAR"), ("last_name", "VARCHAR")]
    _, out_ns = declare_jdbc_query(p, "FindDelinquentAccounts", [], cols,
                                   record_element="ForEachDelinquentAccount_Current_Element_Record")
    rec_params = [("accountNumber", "VARCHAR"), ("daysPastDue", "INTEGER"),
                  ("amountPastDue", "NUMERIC")]
    u = declare_jdbc_update(p, "RecordDelinquency", rec_params)
    s = declare_jms_send(p, "SendToCollections", LS_COL, "CollectionsReferral")
    declare_log(p, "LogScanSummary")
    U, S, L = p.tns(u), p.tns(s), p.tns(LOG_NS)
    cur = "$currentElement"

    timer_cfg = bw_config("bw.generalactivities.timer", "generalactivities",
                          "http://ns.tibco.com/bw/palette/generalactivities",
                          "http://ns.tibco.com/bw/palette/generalactivities#//Timer",
                          {"intervalUnit": "Day", "runOnce": "false",
                           "startTime": "2020-08-01T02:00:00.000-0400", "timeInterval": "1",
                           "xsi:type": "generalactivities:Timer"}, version="6.0.0.001")

    inner = Flow("flow1")
    inner.add("GroupStart", lambda t, src: N("bpws:assign", {
        "name": "GroupStart", "tibex:group": "groupStart", "tibex:xpdlId": p.xid("GroupStart"),
        "validate": "no"}, [
        sources_node(src),
        N("bpws:copy", children=[
            N("bpws:from", {"expressionLanguage": XPATH_LANG},
              cdata="$FindDelinquentAccounts/Record[number($index)]"),
            N("bpws:to", {"variable": "currentElement"})])]))
    inner.add("RecordDelinquency", jdbc_update(
        p, "RecordDelinquency",
        sql("insert into gmf.delinquency (account_number, days_past_due, amount_past_due, detected_at, status)",
            "values (?, ?, ?, current_timestamp, 'REFERRED')"),
        rec_params, p.map(["currentElement"], "RecordDelinquency-input", f'''
<{U}:jdbcUpdateActivityInput>
    <accountNumber>
        <xsl:value-of select="{cur}/account_number"/>
    </accountNumber>
    <daysPastDue>
        <xsl:value-of select="{cur}/days_past_due"/>
    </daysPastDue>
    <amountPastDue>
        <xsl:value-of select="xsd:decimal({cur}/monthly_payment) * ceiling(xsd:integer({cur}/days_past_due) div 30)"/>
    </amountPastDue>
</{U}:jdbcUpdateActivityInput>''')))
    inner.add("SendToCollections", jms_send(
        p, "SendToCollections", "gmf.collections.queue", "Queue",
        f"{schema}/CollectionsReferral.xsd#//CollectionsReferral;XSDElementDeclaration",
        p.map(["currentElement"], "SendToCollections-input", f'''
<{S}:ActivityInput>
    <JMSCorrelationID>
        <xsl:value-of select="{cur}/account_number"/>
    </JMSCorrelationID>
    <Body>
        <{C}:CollectionsReferral>
            <{C}:accountNumber>
                <xsl:value-of select="{cur}/account_number"/>
            </{C}:accountNumber>
            <{C}:borrowerName>
                <xsl:value-of select="concat({cur}/first_name, ' ', {cur}/last_name)"/>
            </{C}:borrowerName>
            <{C}:daysPastDue>
                <xsl:value-of select="{cur}/days_past_due"/>
            </{C}:daysPastDue>
            <{C}:amountPastDue>
                <xsl:value-of select="xsd:decimal({cur}/monthly_payment) * ceiling(xsd:integer({cur}/days_past_due) div 30)"/>
            </{C}:amountPastDue>
            <{C}:referredAt>
                <xsl:value-of select="current-dateTime()"/>
            </{C}:referredAt>
            <{C}:caseCallbackUrl>
                <xsl:value-of select="&quot;http://coll-api-prd01.gmfin.local:8443/collections/v1/cases&quot;"/>
            </{C}:caseCallbackUrl>
        </{C}:CollectionsReferral>
    </Body>
</{S}:ActivityInput>''')))
    inner.add("GroupEnd", group_marker(p, "GroupEnd", "groupEnd"))
    inner.link("GroupStart", "RecordDelinquency")
    inner.link("RecordDelinquency", "SendToCollections")
    inner.link("SendToCollections", "GroupEnd")

    rec_el = p.ns(out_ns, "ForEachDelinquentAccount_Current_Element_Record")
    loop = N("bpws:forEach", {"counterName": "index", "name": "iterate", "parallel": "no",
                              "tibex:group": "iterate"}, [
        N("tibex:DesignExpression", {"name": "variableList"}, [
            N("tibex:expression", {"expression": "$FindDelinquentAccounts/Record",
                                   "expressionLanguage": XPATH_LANG})]),
        N("bpws:startCounterValue", cdata="1"),
        N("bpws:finalCounterValue", {"expressionLanguage": XPATH_LANG},
          cdata="count($FindDelinquentAccounts/Record)"),
        N("bpws:scope", {"name": "innerScope"}, [
            N("bpws:variables", children=[
                N("bpws:variable", {"element": rec_el, "name": "currentElement",
                                    "sca-bpel:internal": "true"})]),
            inner.node()]),
    ])

    def iterate_build(targets, sources):
        return N("bpws:scope", {"name": "ForEachDelinquentAccount", "tibex:group": "iterate",
                                "tibex:xpdlId": p.xid("ForEachDelinquentAccount")},
                 [targets_node(targets), sources_node(sources), loop])

    f = Flow("flow")
    f.add("DailyAt0200", event_source(p, "DailyAt0200", "DailyAt0200", timer_cfg))
    f.add("FindDelinquentAccounts", jdbc_query(
        p, "FindDelinquentAccounts",
        sql("select la.account_number, la.days_past_due, la.monthly_payment, a.first_name, a.last_name",
            "  from gmf.loan_account la",
            "  join gmf.applicant a on a.applicant_id = la.applicant_id",
            " where la.days_past_due >= 30",
            "   and la.status = 'ACTIVE'",
            " order by la.days_past_due desc"),
        [], cols, None, max_rows="0"))
    f.add("ForEachDelinquentAccount", iterate_build)
    f.add("LogScanSummary", log(p, "LogScanSummary", "Info", p.map(
        ["FindDelinquentAccounts"], "LogScanSummary-input", f'''
<{L}:ActivityInput>
    <msgCode>
        <xsl:value-of select="&quot;LS-310&quot;"/>
    </msgCode>
    <message>
        <xsl:value-of select="concat('Delinquency scan referred ', count($FindDelinquentAccounts/Record), ' accounts to collections')"/>
    </message>
</{L}:ActivityInput>''')))
    f.link("DailyAt0200", "FindDelinquentAccounts")
    f.link("FindDelinquentAccounts", "ForEachDelinquentAccount")
    f.link("ForEachDelinquentAccount", "LogScanSummary")
    p.body = N("bpws:scope", {"name": "scope"}, [f.node()])
    return p


DF_PROCESSES = [dealer_funding, funding_status]
LS_PROCESSES = [payment_posting, account_lookup, nightly_scan]
