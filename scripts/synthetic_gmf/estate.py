"""estate.json: machine-readable inventory used later as the test oracle."""
import json
from pathlib import Path

import bw6_structure_check as bw6
from lxml import etree

CO = "CreditOrigination/Processes/creditorigination"
DF = "DealerFunding/Processes/dealerfunding"
LS = "LoanServicing/Processes/loanservicing"


def r(id_, text, where):
    return {"id": id_, "text": text, "where": where}


def fnd(id_, text, where, static_rule, severity):
    return {"id": id_, "text": text, "where": where, "static_rule": static_rule,
            "severity": severity}


PROCESSES = [
    {
        "id": "creditorigination.CreditApplicationIntake", "module": "CreditOrigination",
        "file": f"{CO}/CreditApplicationIntake.bwp",
        "trigger": {"kind": "rest", "verb": "POST", "path": "/applications"},
        "calls": ["creditorigination.AuditLog", "creditorigination.ValidateVIN",
                  "creditorigination.BureauOrchestrator", "creditorigination.DecisionEngine"],
        "rules": [
            r("INT-R1", "The SSN must match NNN-NN-NNNN and must not start with 000 or 666; "
                        "otherwise the request is rejected with HTTP 400.",
              {"activity": "CallAuditLog", "link": "CallAuditLogToCallValidateVIN"}),
            r("INT-R2", "An application for an SSN that already has an application submitted "
                        "in the last 30 days raises DuplicateApplicationFault (HTTP 409).",
              {"activity": "CheckDuplicateApplication", "link":
               "CheckDuplicateApplicationToThrowDuplicateApplication"}),
            r("INT-R3", "Every request is audited before any validation, including requests "
                        "that are later rejected.", {"activity": "CallAuditLog"}),
            r("INT-R4", "A VIN that fails ValidateVIN is rejected with HTTP 400.",
              {"activity": "CallValidateVIN", "link": "CallValidateVINToCheckDuplicateApplication"}),
        ],
        "findings": [
            fnd("INT-F1", "Response field transUnionScore is declared in the response schema "
                          "but never populated by the reply mapping.",
                {"activity": "postOut", "schema": "CreditOrigination/Schemas/CreditApplication.xsd",
                 "element": "CreditApplicationResponse/transUnionScore"}, "S4", "low"),
        ],
        "tables_read": ["credit_application"], "tables_written": [],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "creditorigination.BureauOrchestrator", "module": "CreditOrigination",
        "file": f"{CO}/BureauOrchestrator.bwp",
        "trigger": {"kind": "subprocess"},
        "calls": [],
        "rules": [
            r("BUR-R1", "Equifax and TransUnion are pulled in parallel; the higher score wins "
                        "(a tie selects Equifax).", {"activity": "MergeScores"}),
            r("BUR-R2", "When exactly one bureau responds, singleBureau is true and that "
                        "bureau's score is used.", {"activity": "MergeScores"}),
            r("BUR-R3", "When neither bureau responds, BureauUnavailableFault is raised.",
              {"activity": "ThrowBureauUnavailable", "link": "MergeScoresToThrowBureauUnavailable"}),
            r("BUR-R4", "Each bureau call times out after 1000 ms; the circuit opens at 50% "
                        "errors over a 20-request window.",
              {"resource": "CreditOrigination/Resources/creditorigination/EquifaxClient.httpClientResource"}),
        ],
        "findings": [],
        "tables_read": [], "tables_written": ["bureau_pull"],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
        "endpoints": ["EquifaxClient POST /consumer-credit/v2/scores",
                      "TransUnionClient POST /credit-report/v1/score"],
    },
    {
        "id": "creditorigination.DecisionEngine", "module": "CreditOrigination",
        "file": f"{CO}/DecisionEngine.bwp",
        "trigger": {"kind": "subprocess"},
        "calls": [],
        "rules": [
            r("DEC-R1", "DTI is monthly debt divided by monthly income.", {"activity": "ComputeDTI"}),
            r("DEC-R2", "Tier A: score 740 or above and DTI below 0.35; APR 5.49.",
              {"link": "ComputeDTIToAssignTierA"}),
            r("DEC-R3", "Tier B: score 680 to 739; APR 7.99.", {"link": "ComputeDTIToAssignTierB"}),
            r("DEC-R4", "Tier C: score 620 to 679 and DTI below 0.45; APR 12.49; term capped at "
                        "72 months.", {"link": "ComputeDTIToAssignTierC", "activity": "AssignTierC"}),
            r("DEC-R5", "Anything else is declined.", {"link": "ComputeDTIToAssignDecline"}),
            r("DEC-R6", "Missing or non-positive monthly income raises MissingIncomeFault.",
              {"link": "StartToThrowMissingIncome"}),
        ],
        "findings": [],
        "tables_read": [], "tables_written": ["decision", "credit_application"],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "creditorigination.ValidateVIN", "module": "CreditOrigination",
        "file": f"{CO}/ValidateVIN.bwp",
        "trigger": {"kind": "subprocess"},
        "calls": [],
        "rules": [
            r("VIN-R1", "A VIN has 17 characters from the VIN alphabet (no I, O or Q).",
              {"link": "StartToComputeCheckDigit"}),
            r("VIN-R2", "Position 9 is the check digit: weighted sum of transliterated "
                        "characters mod 11, with 10 written as X.", {"activity": "ComputeCheckDigit"}),
        ],
        "findings": [],
        "tables_read": [], "tables_written": [],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "creditorigination.AuditLog", "module": "CreditOrigination",
        "file": f"{CO}/AuditLog.bwp",
        "trigger": {"kind": "subprocess"},
        "calls": [],
        "rules": [
            r("AUD-R1", "Every audit event is inserted into audit_log with its event type, "
                        "application id and payload.", {"activity": "WriteAuditRecord"}),
        ],
        "findings": [
            fnd("AUD-F1", "The applicant SSN is written to audit_log in clear text.",
                {"activity": "WriteAuditRecord", "column": "ssn"}, "S7", "high"),
        ],
        "tables_read": [], "tables_written": ["audit_log"],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "dealerfunding.DealerFundingRequest", "module": "DealerFunding",
        "file": f"{DF}/DealerFundingRequest.bwp",
        "trigger": {"kind": "jms", "destination": "gmf.dealer.funding.request",
                    "selector": "MsgType = 'FUNDING_REQUEST'"},
        "calls": [],
        "rules": [
            r("DFR-R1", "The dealer must be ACTIVE; otherwise DealerInactiveFault is raised.",
              {"link": "LookupDealerToLookupDeal"}),
            r("DFR-R2", "The funding amount must be within 2% of the approved amount; otherwise "
                        "a REJECTED event is published.", {"link": "LookupDealToCheckExistingFunding"}),
            r("DFR-R3", "A deal is funded once; a repeat request is logged and ignored.",
              {"link": "CheckExistingFundingToInsertFunding"}),
            r("DFR-R4", "A FUNDED event is published to topic gmf.funding.events after the "
                        "funding row is written.", {"activity": "PublishFundingEvent"}),
        ],
        "findings": [
            fnd("DFR-F1", "Read-then-write on gmf.funding without a transaction group: two "
                          "concurrent requests for one deal can both pass the existence check "
                          "and fund twice.",
                {"activities": ["CheckExistingFunding", "InsertFunding"]}, "S1", "high"),
            fnd("DFR-F2", "dealer_id is matched with LIKE and a trailing wildcard, so dealer "
                          "D1002 also matches D10021.", {"activity": "LookupDealer"}, "S2", "high"),
        ],
        "tables_read": ["dealer", "deal", "funding"], "tables_written": ["funding"],
        "queues_in": ["gmf.dealer.funding.request"], "queues_out": [],
        "topics_out": ["gmf.funding.events"],
    },
    {
        "id": "dealerfunding.FundingStatusLookup", "module": "DealerFunding",
        "file": f"{DF}/FundingStatusLookup.bwp",
        "trigger": {"kind": "rest", "verb": "GET", "path": "/funding/{dealId}"},
        "calls": [],
        "rules": [
            r("FSL-R1", "Returns the funding row for the deal; HTTP 404 when none exists.",
              {"link": "QueryFundingStatusTogetOut"}),
        ],
        "findings": [],
        "tables_read": ["funding"], "tables_written": [],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "loanservicing.PaymentPosting", "module": "LoanServicing",
        "file": f"{LS}/PaymentPosting.bwp",
        "trigger": {"kind": "jms", "destination": "gmf.payments.inbound",
                    "selector": "PaymentChannel IN ('ACH', 'DEBIT', 'LOCKBOX')"},
        "calls": [],
        "rules": [
            r("PAY-R1", "A late fee of 25.00 is charged when the payment arrives after the "
                        "10-day grace period.", {"link": "LoadAccountToAssessLateFee",
                                                 "activity": "AssessLateFee"}),
            r("PAY-R2", "Payments are allocated to fees (including the late fee), then accrued "
                        "interest, then principal.", {"activity": "AllocatePayment"}),
            r("PAY-R3", "Account read, payment insert and balance update run in one local "
                        "transaction.", {"group": "PostPaymentTransaction"}),
        ],
        "findings": [
            fnd("PAY-F1", "The grace comparison uses >= 10 where the policy says the fee applies "
                          "after 10 days (> 10): a payment on day 10 is charged.",
                {"link": "LoadAccountToAssessLateFee"}, None, "medium"),
        ],
        "tables_read": ["loan_account"], "tables_written": ["payment", "loan_account"],
        "queues_in": ["gmf.payments.inbound"], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "loanservicing.AccountLookup", "module": "LoanServicing",
        "file": f"{LS}/AccountLookup.bwp",
        "trigger": {"kind": "rest", "verb": "GET", "path": "/accounts/{accountNumber}"},
        "calls": [],
        "rules": [
            r("ACC-R1", "The SSN is masked to its last four digits in the response.",
              {"activity": "getOut"}),
            r("ACC-R2", "HTTP 404 when the account does not exist.",
              {"link": "QueryAccountToReplyAccountNotFound"}),
        ],
        "findings": [],
        "tables_read": ["loan_account", "applicant"], "tables_written": [],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
    {
        "id": "loanservicing.NightlyDelinquencyScan", "module": "LoanServicing",
        "file": f"{LS}/NightlyDelinquencyScan.bwp",
        "trigger": {"kind": "timer", "start": "02:00", "interval": "1 Day"},
        "calls": [],
        "rules": [
            r("NDS-R1", "Daily at 02:00, ACTIVE accounts 30 or more days past due are referred to "
                        "collections, one message per account.", {"activity": "FindDelinquentAccounts"}),
            r("NDS-R2", "Each referral is recorded in delinquency with the amount past due "
                        "(monthly payment times started 30-day periods).",
              {"activity": "RecordDelinquency"}),
        ],
        "findings": [
            fnd("NDS-F1", "The collections callback URL is hard-coded in the referral mapping "
                          "instead of coming from a module property.",
                {"activity": "SendToCollections", "field": "caseCallbackUrl"}, "S3", "medium"),
        ],
        "tables_read": ["loan_account", "applicant"], "tables_written": ["delinquency"],
        "queues_in": [], "queues_out": ["gmf.collections.queue"],
        "topics_out": [],
    },
    {
        "id": "LegacyAddressStandardize", "module": "LegacyAddress",
        "file": "LegacyAddress/LegacyAddressStandardize.process",
        "trigger": {"kind": "http", "resource": "/AddressService.sharedhttp"},
        "calls": [],
        "rules": [
            r("LAS-R1", "Address lines, city and state are trimmed and upper-cased; the postal "
                        "code is cut to 5 digits.", {"activity": "Standardize Address"}),
            r("LAS-R2", "The state code must be one of the 50 states or DC.",
              {"transition": "Standardize Address -> Send HTTP Response"}),
        ],
        "findings": [
            fnd("LAS-F1", "No otherwise transition after the state check: an invalid state ends "
                          "the job without an HTTP response and the caller waits for its timeout.",
                {"activity": "Standardize Address"}, "S5", "medium"),
        ],
        "tables_read": [], "tables_written": [],
        "queues_in": [], "queues_out": [],
        "topics_out": [],
    },
]

ESTATE = {
    "synthetic": True,
    "note": "Synthetic GMF-style estate for the ExiQO Migrate demo. Original content; not a "
            "GM Financial artifact.",
    "applications": [
        {"id": "gmf.credit", "dialect": "bw6", "folder": "gmf.credit.application",
         "modules": ["CreditOrigination", "DealerFunding", "LoanServicing"]},
        {"id": "LegacyAddress", "dialect": "bw5", "folder": "LegacyAddress",
         "modules": ["LegacyAddress"]},
    ],
    "queues": ["gmf.dealer.funding.request", "gmf.payments.inbound", "gmf.collections.queue"],
    "topics": ["gmf.funding.events"],
    "tables": ["applicant", "vehicle", "credit_application", "bureau_pull", "decision", "dealer",
               "deal", "funding", "loan_account", "payment", "delinquency", "audit_log"],
    "processes": PROCESSES,
}


# Dialect used outside .bwp files (the structure checker cannot police these).
EXTRA_UNVERIFIED = [
    {"token": "jms:JMSConnectionFactory", "kind": ".jmsConnResource",
     "where": ["DealerFunding/Resources/dealerfunding/GMFEMSConnection.jmsConnResource",
               "LoanServicing/Resources/loanservicing/GMFEMSConnection.jmsConnResource"],
     "what_to_confirm": "namedResource type and configuration attribute names "
                        "(connectionFactoryType, messagingStyle, providerURL, username, "
                        "password, clientID); labels match TIBCO's JMS Connection docs"},
    {"token": "{http://xsd.tns.tibco.com/bw/models/sharedresource/jms}JMSConnectionFactory",
     "kind": "sca-bpel:sharedResourceType",
     "where": ["dealerfunding.DealerFundingRequest", "loanservicing.PaymentPosting",
               "loanservicing.NightlyDelinquencyScan"],
     "what_to_confirm": "sharedResourceType QName of the JMS connection process property"},
    {"token": "jms+<xpdlId>+input/output", "kind": "inline JMS schemas",
     "where": ["dealerfunding.DealerFundingRequest", "loanservicing.PaymentPosting",
               "loanservicing.NightlyDelinquencyScan"],
     "what_to_confirm": "generated namespace and field names of JMS activity input and output "
                        "(JMSHeaders, JMSProperties and Body are documented; namespace pattern "
                        "copied from the JDBC activities)"},
    {"token": "bpws:catch faultElement", "kind": "fault handler",
     "where": ["creditorigination.CreditApplicationIntake: catchDuplicate, catchMissingIncome"],
     "what_to_confirm": "attributes Studio writes on a typed catch (public samples only show "
                        "catchAll) and its diagram nodes (4029/4033 and the @catch.N href "
                        "are assumed from catchAll)"},
    {"token": "bpws:empty tibex:group", "kind": "group markers",
     "where": ["loanservicing.PaymentPosting: GroupStart, GroupEnd",
               "loanservicing.NightlyDelinquencyScan: GroupEnd"],
     "what_to_confirm": "element Studio writes for GroupStart/GroupEnd when the group has no "
                        "iteration variable and no accumulate option (public sample: assign "
                        "for GroupStart, accumulate end for GroupEnd)"},
    {"token": "parameterDefinitions", "kind": "module.bwm REST binding",
     "where": ["DealerFunding/META-INF/module.bwm", "LoanServicing/META-INF/module.bwm"],
     "what_to_confirm": "element and attributes for a REST path parameter "
                        "(name, type, parameterType=Path, required)"},
    {"token": "maxConnections, loginTimeout", "kind": ".jdbcResource",
     "where": ["Resources/*/*.jdbcResource"],
     "what_to_confirm": "JDBC connection pool attribute names"},
    {"token": "port", "kind": ".httpConnResource",
     "where": ["Resources/*/*Connector.httpConnResource"],
     "what_to_confirm": "attribute carrying the HTTP connector port"},
    {"token": "<parameters><parameter>", "kind": "BW5 HTTP Receiver config",
     "where": ["LegacyAddress/LegacyAddressStandardize.process: HTTP Receiver"],
     "what_to_confirm": "config shape for declared HTTP parameters (name, dataType, repeating) "
                        "and the output path ProcessStarterOutput/parameters/<name>"},
]


def _usage(root: Path) -> dict[str, list[str]]:
    """Where each checker-listed unverified token is used in the generated .bwp files."""
    found: dict[str, list[str]] = {}
    t = "{http://www.tibco.com/bpel/2007/extensions}"
    for path in sorted(root.rglob("*.bwp")):
        proc = etree.parse(str(path)).getroot()
        pid = proc.get("name")
        for act in proc.iter(f"{{{bw6.BWEXT}}}BWActivity"):
            owner = act.getparent().getparent().get("name")
            type_id = act.get("activityTypeID")
            names = {type_id}
            if type_id in bw6.UNVERIFIED_ACTIVITY_TYPES:
                for value in act.iter("value"):
                    names |= set(value.attrib)
                    names |= {etree.QName(c).localname for c in value if isinstance(c.tag, str)}
            for name in names:
                found.setdefault(name, []).append(f"{pid}: {owner}")
        for el in proc.iter():
            if isinstance(el.tag, str) and el.get(f"{t}group"):
                found.setdefault(el.get(f"{t}group"), []).append(f"{pid}: {el.get('name')}")
        for link in proc.iter(f"{{{bw6.BPWS}}}link"):
            found.setdefault(link.get(f"{t}linkType"), []).append(f"{pid}: {link.get('name')}")
    return found


def estate_json(root: Path) -> str:
    usage = _usage(Path(root))
    checklist = [{"token": u["token"], "kind": u["kind"],
                  "where": sorted(set(usage.get(u["token"], []))),
                  "what_to_confirm": u["what_to_confirm"]} for u in bw6.UNVERIFIED]
    data = dict(ESTATE)
    data["dialect_unverified"] = checklist + EXTRA_UNVERIFIED
    return json.dumps(data, indent=2) + "\n"


SYNTHETIC_TXT = """SYNTHETIC ESTATE

Everything under this folder is synthetic. It was written for the ExiQO Migrate demo
(R Systems) to look like a GM Financial style TIBCO BusinessWorks estate. It is not a
GM Financial artifact and contains no customer code, data or configuration.

Layout follows the BusinessWorks 6.5 project format (BPEL with tibex extensions,
module.bwm, META-INF/MANIFEST.MF, default.substvar, shared resources, XSD, Swagger
JSON) plus one BusinessWorks 5 project. Names, SSNs (9xx area numbers, never issued),
VINs, dealers, accounts, hosts and passwords are invented.

Contents
  gmf.credit.application/   BW6 application (modules CreditOrigination, DealerFunding,
                            LoanServicing)
  LegacyAddress/            BW5 project (LegacyAddressStandardize.process)
  Database/                 PostgreSQL DDL and seed rows, schema gmf
  Stubs/                    WireMock mappings for the Equifax and TransUnion stubs
  estate.json               inventory of processes, triggers, rules and seeded findings

The rules and findings listed in estate.json are seeded on purpose.
"""
