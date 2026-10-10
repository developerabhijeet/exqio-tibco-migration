"""Module XSDs and REST service descriptors."""
from __future__ import annotations

import json

from .names import (
    CO_APP,
    CO_AUD,
    CO_BUR,
    CO_DEC,
    CO_FLT,
    CO_VEH,
    DF_EVT,
    DF_FLT,
    DF_PRM,
    DF_REQ,
    DF_STS,
    LA_ADDR,
    LS_ACC,
    LS_COL,
    LS_PAY,
    LS_PRM,
    REST_NS,
)

HDR = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'


def schema(tns, body, qualified=True, doc=None):
    form = "qualified" if qualified else "unqualified"
    ann = ""
    if doc:
        ann = (f"  <xs:annotation>\n    <xs:documentation>{doc}</xs:documentation>\n"
               "  </xs:annotation>\n")
    return (HDR + f'<xs:schema xmlns:tns="{tns}" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            f'attributeFormDefault="unqualified" elementFormDefault="{form}" '
            f'targetNamespace="{tns}" version="1.0">\n' + ann + body.strip("\n") +
            "\n</xs:schema>\n")


REST_SCHEMA = HDR + f'''<schema xmlns="http://www.w3.org/2001/XMLSchema" xmlns:tns="{REST_NS}" targetNamespace="{REST_NS}">

	<complexType name="httpTransportHeaders">
		<sequence>
			<element form="unqualified" minOccurs="0" name="Accept" type="string"/>
			<element form="unqualified" minOccurs="0" name="Accept-Encoding" type="string"/>
			<element form="unqualified" minOccurs="0" name="Content-Type" type="string"/>
			<element form="unqualified" minOccurs="0" name="Content-Length" type="string"/>
			<element form="unqualified" minOccurs="0" name="Authorization" type="string"/>
			<element form="unqualified" minOccurs="0" name="X-Correlation-Id" type="string"/>
		</sequence>
	</complexType>

	<complexType name="faultType">
		<sequence>
			<element form="unqualified" name="statusCode" type="int"/>
			<element form="unqualified" minOccurs="0" name="message" type="string"/>
		</sequence>
	</complexType>

	<element name="httpHeaders" type="tns:httpTransportHeaders"/>
	<element name="client4XXError" type="tns:faultType"/>
	<element name="server5XXError" type="tns:faultType"/>
</schema>
'''

CREDIT_APPLICATION = schema(CO_APP, '''
  <xs:complexType name="AddressType">
    <xs:sequence>
      <xs:element name="line1" type="xs:string"/>
      <xs:element minOccurs="0" name="line2" type="xs:string"/>
      <xs:element name="city" type="xs:string"/>
      <xs:element name="state" type="xs:string"/>
      <xs:element name="postalCode" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="ApplicantType">
    <xs:sequence>
      <xs:element name="firstName" type="xs:string"/>
      <xs:element name="lastName" type="xs:string"/>
      <xs:element name="ssn" type="xs:string"/>
      <xs:element name="dateOfBirth" type="xs:date"/>
      <xs:element minOccurs="0" name="monthlyIncome" type="xs:decimal"/>
      <xs:element name="monthlyDebt" type="xs:decimal"/>
      <xs:element name="address" type="tns:AddressType"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="VehicleType">
    <xs:sequence>
      <xs:element name="vin" type="xs:string"/>
      <xs:element name="modelYear" type="xs:int"/>
      <xs:element name="make" type="xs:string"/>
      <xs:element name="model" type="xs:string"/>
      <xs:element name="price" type="xs:decimal"/>
    </xs:sequence>
  </xs:complexType>
  <xs:element name="CreditApplicationRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element name="applicant" type="tns:ApplicantType"/>
        <xs:element name="vehicle" type="tns:VehicleType"/>
        <xs:element name="requestedAmount" type="xs:decimal"/>
        <xs:element name="requestedTermMonths" type="xs:int"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="CreditApplicationResponse">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="decision" type="xs:string"/>
        <xs:element minOccurs="0" name="tier" type="xs:string"/>
        <xs:element minOccurs="0" name="apr" type="xs:decimal"/>
        <xs:element minOccurs="0" name="approvedAmount" type="xs:decimal"/>
        <xs:element minOccurs="0" name="approvedTermMonths" type="xs:int"/>
        <xs:element minOccurs="0" name="creditScore" type="xs:int"/>
        <xs:element minOccurs="0" name="equifaxScore" type="xs:int"/>
        <xs:element minOccurs="0" name="transUnionScore" type="xs:int"/>
        <xs:element minOccurs="0" name="singleBureau" type="xs:boolean"/>
        <xs:element name="decidedAt" type="xs:dateTime"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="ApplicantSnapshot">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element name="ssn" type="xs:string"/>
        <xs:element name="firstName" type="xs:string"/>
        <xs:element name="lastName" type="xs:string"/>
        <xs:element name="dateOfBirth" type="xs:date"/>
        <xs:element name="vin" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Retail installment credit application, dealer channel. Request and response "
         "bodies of POST /applications.")

BUREAU = schema(CO_BUR, '''
  <xs:element name="BureauRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="ssn" type="xs:string"/>
        <xs:element name="firstName" type="xs:string"/>
        <xs:element name="lastName" type="xs:string"/>
        <xs:element name="dateOfBirth" type="xs:date"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="BureauScoreRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="ssn" type="xs:string"/>
        <xs:element name="firstName" type="xs:string"/>
        <xs:element name="lastName" type="xs:string"/>
        <xs:element name="dateOfBirth" type="xs:date"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="BureauScore">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="bureau" type="xs:string"/>
        <xs:element name="score" type="xs:int"/>
        <xs:element minOccurs="0" name="referenceId" type="xs:string"/>
        <xs:element minOccurs="0" name="inquiries" type="xs:int"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="BureauResult">
    <xs:complexType>
      <xs:sequence>
        <xs:element minOccurs="0" name="equifaxScore" type="xs:int"/>
        <xs:element minOccurs="0" name="transUnionScore" type="xs:int"/>
        <xs:element minOccurs="0" name="selectedScore" type="xs:int"/>
        <xs:element minOccurs="0" name="selectedBureau" type="xs:string"/>
        <xs:element name="singleBureau" type="xs:boolean"/>
        <xs:element name="bureauCount" type="xs:int"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Consumer credit bureau pulls (Equifax, TransUnion) for origination.")

DECISION = schema(CO_DEC, '''
  <xs:element name="DecisionRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="parameters">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="applicationId" type="xs:string"/>
              <xs:element name="ssn" type="xs:string"/>
              <xs:element name="dealerId" type="xs:string"/>
              <xs:element name="vin" type="xs:string"/>
              <xs:element name="creditScore" type="xs:int"/>
              <xs:element name="singleBureau" type="xs:boolean"/>
              <xs:element minOccurs="0" name="monthlyIncome" type="xs:decimal"/>
              <xs:element name="monthlyDebt" type="xs:decimal"/>
              <xs:element name="requestedAmount" type="xs:decimal"/>
              <xs:element name="requestedTermMonths" type="xs:int"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="DtiResult">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="dti" type="xs:decimal"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="TierAssignment">
    <xs:complexType>
      <xs:sequence>
        <xs:element minOccurs="0" name="tier" type="xs:string"/>
        <xs:element name="decision" type="xs:string"/>
        <xs:element name="apr" type="xs:decimal"/>
        <xs:element name="approvedAmount" type="xs:decimal"/>
        <xs:element name="approvedTermMonths" type="xs:int"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="DecisionResult">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="decision" type="xs:string"/>
        <xs:element minOccurs="0" name="tier" type="xs:string"/>
        <xs:element name="apr" type="xs:decimal"/>
        <xs:element name="approvedAmount" type="xs:decimal"/>
        <xs:element name="approvedTermMonths" type="xs:int"/>
        <xs:element name="dti" type="xs:decimal"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', qualified=False, doc="Credit decision interface. Local elements are unqualified "
                          "(legacy contract shared with the decision rules team).")

VEHICLE = schema(CO_VEH, '''
  <xs:element name="VinValidationRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="vin" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="VinValidationResult">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="vin" type="xs:string"/>
        <xs:element name="valid" type="xs:boolean"/>
        <xs:element minOccurs="0" name="reason" type="xs:string"/>
        <xs:element minOccurs="0" name="computedCheckDigit" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Vehicle identification number validation (49 CFR 565 check digit).")

AUDIT = schema(CO_AUD, '''
  <xs:element name="AuditEvent">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="eventType" type="xs:string"/>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element minOccurs="0" name="ssn" type="xs:string"/>
        <xs:element minOccurs="0" name="payload" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Origination audit trail event.")

CO_FAULTS = schema(CO_FLT, '''
  <xs:element name="DuplicateApplicationFault">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="existingApplicationId" type="xs:string"/>
        <xs:element name="message" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="MissingIncomeFault">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="message" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="BureauUnavailableFault">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="message" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Business faults raised by CreditOrigination processes.")

FUNDING_REQUEST = schema(DF_REQ, '''
  <xs:element name="FundingRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="dealId" type="xs:string"/>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element name="applicationId" type="xs:string"/>
        <xs:element name="fundingAmount" type="xs:decimal"/>
        <xs:element name="contractDate" type="xs:date"/>
        <xs:element minOccurs="0" name="requestedBy" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Dealer funding request, JMS queue gmf.dealer.funding.request (XML text body).")

FUNDING_EVENT = schema(DF_EVT, '''
  <xs:element name="FundingEvent">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="dealId" type="xs:string"/>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element name="amount" type="xs:decimal"/>
        <xs:element name="status" type="xs:string"/>
        <xs:element minOccurs="0" name="reason" type="xs:string"/>
        <xs:element name="eventTime" type="xs:dateTime"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Funding outcome event, JMS topic gmf.funding.events.")

FUNDING_STATUS = schema(DF_STS, '''
  <xs:element name="FundingStatusResponse">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="dealId" type="xs:string"/>
        <xs:element name="fundingId" type="xs:int"/>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element name="amount" type="xs:decimal"/>
        <xs:element name="status" type="xs:string"/>
        <xs:element minOccurs="0" name="fundedAt" type="xs:dateTime"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Response body of GET /funding/{dealId}.")

DF_FAULTS = schema(DF_FLT, '''
  <xs:element name="DealerInactiveFault">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="dealerId" type="xs:string"/>
        <xs:element minOccurs="0" name="status" type="xs:string"/>
        <xs:element name="message" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Business faults raised by DealerFunding processes.")


def parameter_schema(tns, element, name):
    return (HDR + f'<xs:schema xmlns:source="bw.rest" xmlns:tns="{tns}" '
            'xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified" '
            f'targetNamespace="{tns}">\n'
            f'  <xs:element name="{element}">\n    <xs:complexType>\n      <xs:sequence>\n'
            f'        <xs:element name="{name}" type="xs:string"/>\n'
            '      </xs:sequence>\n    </xs:complexType>\n  </xs:element>\n</xs:schema>\n')


FUNDING_PARAMS = parameter_schema(DF_PRM, "fundingGetParameters", "dealId")
ACCOUNT_PARAMS = parameter_schema(LS_PRM, "accountsGetParameters", "accountNumber")

PAYMENT = schema(LS_PAY, '''
  <xs:element name="Payment">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="paymentId" type="xs:string"/>
        <xs:element name="accountNumber" type="xs:string"/>
        <xs:element name="amount" type="xs:decimal"/>
        <xs:element name="receivedDate" type="xs:date"/>
        <xs:element name="channel" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="LateFeeAssessment">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="lateFee" type="xs:decimal"/>
        <xs:element name="daysLate" type="xs:int"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
  <xs:element name="PaymentAllocation">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="feeApplied" type="xs:decimal"/>
        <xs:element name="lateFeeApplied" type="xs:decimal"/>
        <xs:element name="interestApplied" type="xs:decimal"/>
        <xs:element name="principalApplied" type="xs:decimal"/>
        <xs:element name="newFeesDue" type="xs:decimal"/>
        <xs:element name="newAccruedInterest" type="xs:decimal"/>
        <xs:element name="newPrincipalBalance" type="xs:decimal"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Inbound loan payment, JMS queue gmf.payments.inbound (XML text body).")

ACCOUNT = schema(LS_ACC, '''
  <xs:element name="AccountSummary">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="accountNumber" type="xs:string"/>
        <xs:element name="borrowerName" type="xs:string"/>
        <xs:element name="maskedSsn" type="xs:string"/>
        <xs:element name="principalBalance" type="xs:decimal"/>
        <xs:element name="nextDueDate" type="xs:date"/>
        <xs:element name="daysPastDue" type="xs:int"/>
        <xs:element name="status" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Response body of GET /accounts/{accountNumber}.")

COLLECTIONS = schema(LS_COL, '''
  <xs:element name="CollectionsReferral">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="accountNumber" type="xs:string"/>
        <xs:element name="borrowerName" type="xs:string"/>
        <xs:element name="daysPastDue" type="xs:int"/>
        <xs:element name="amountPastDue" type="xs:decimal"/>
        <xs:element name="referredAt" type="xs:dateTime"/>
        <xs:element name="caseCallbackUrl" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Collections referral, JMS queue gmf.collections.queue (XML text body).")

ADDRESS = schema(LA_ADDR, '''
  <xs:element name="StandardizedAddress">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="line1" type="xs:string"/>
        <xs:element minOccurs="0" name="line2" type="xs:string"/>
        <xs:element name="city" type="xs:string"/>
        <xs:element name="state" type="xs:string"/>
        <xs:element name="postalCode" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
''', doc="Standardized US mailing address returned by the legacy address service.")


# --- Swagger 2.0 descriptors (BW6 REST binding, JSON client format) -------------


def _props(fields):
    out = {}
    for name, typ in fields:
        if typ.startswith("#/"):
            out[name] = {"$ref": typ}
        elif typ in ("integer", "number", "string", "boolean"):
            out[name] = {"type": typ}
        elif typ == "date":
            out[name] = {"type": "string", "format": "date"}
        elif typ == "date-time":
            out[name] = {"type": "string", "format": "date-time"}
    return {"type": "object", "properties": out}


def _swagger(title, description, port, path, method, op_id, params, response_ref, defs,
             not_found=False, client_error=True):
    responses = {"200": {"description": f"a {response_ref} to be returned",
                         "schema": {"$ref": f"#/definitions/{response_ref}"}}}
    if client_error:
        responses["400"] = {"description": "Client error", "schema": {"type": "string"}}
    if not_found:
        responses["404"] = {"description": "Not found", "schema": {"type": "string"}}
    return json.dumps({
        "swagger": "2.0",
        "info": {"version": "1.0", "title": title, "description": description},
        "host": f"localhost:{port}",
        "basePath": "/",
        "schemes": ["http"],
        "paths": {path: {method: {
            "summary": description, "description": "", "operationId": op_id,
            "consumes": ["application/json"], "produces": ["application/json"],
            "parameters": params, "responses": responses}}},
        "definitions": defs,
    }, indent=2) + "\n"


INTAKE_SWAGGER = _swagger(
    "Applications", "Submit a retail credit application from the dealer channel.", 9081,
    "/applications", "post", "post-applications",
    [{"name": "body", "in": "body", "description": "", "required": True,
      "schema": {"$ref": "#/definitions/CreditApplicationRequest"}}],
    "CreditApplicationResponse",
    {
        "Address": _props([("line1", "string"), ("line2", "string"), ("city", "string"),
                           ("state", "string"), ("postalCode", "string")]),
        "Applicant": _props([("firstName", "string"), ("lastName", "string"),
                             ("ssn", "string"), ("dateOfBirth", "date"),
                             ("monthlyIncome", "number"), ("monthlyDebt", "number"),
                             ("address", "#/definitions/Address")]),
        "Vehicle": _props([("vin", "string"), ("modelYear", "integer"), ("make", "string"),
                           ("model", "string"), ("price", "number")]),
        "CreditApplicationRequest": _props([
            ("applicationId", "string"), ("dealerId", "string"),
            ("applicant", "#/definitions/Applicant"), ("vehicle", "#/definitions/Vehicle"),
            ("requestedAmount", "number"), ("requestedTermMonths", "integer")]),
        "CreditApplicationResponse": _props([
            ("applicationId", "string"), ("decision", "string"), ("tier", "string"),
            ("apr", "number"), ("approvedAmount", "number"), ("approvedTermMonths", "integer"),
            ("creditScore", "integer"), ("equifaxScore", "integer"),
            ("transUnionScore", "integer"), ("singleBureau", "boolean"),
            ("decidedAt", "date-time")]),
    })

FUNDING_SWAGGER = _swagger(
    "Funding", "Funding status for a dealer deal.", 9082, "/funding/{dealId}", "get",
    "get-funding",
    [{"name": "dealId", "in": "path", "description": "", "required": True, "type": "string"}],
    "FundingStatusResponse",
    {"FundingStatusResponse": _props([
        ("dealId", "string"), ("fundingId", "integer"), ("dealerId", "string"),
        ("amount", "number"), ("status", "string"), ("fundedAt", "date-time")])},
    not_found=True, client_error=False)

ACCOUNT_SWAGGER = _swagger(
    "Accounts", "Loan account summary for servicing agents.", 9083,
    "/accounts/{accountNumber}", "get", "get-accounts",
    [{"name": "accountNumber", "in": "path", "description": "", "required": True,
      "type": "string"}],
    "AccountSummary",
    {"AccountSummary": _props([
        ("accountNumber", "string"), ("borrowerName", "string"), ("maskedSsn", "string"),
        ("principalBalance", "number"), ("nextDueDate", "date"), ("daysPastDue", "integer"),
        ("status", "string")])},
    not_found=True, client_error=False)
