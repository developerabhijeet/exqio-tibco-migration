"""Database DDL/seed and bureau stubs."""
from __future__ import annotations

import json

TRANS = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", "12345678123457923456789"))
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin(prefix8: str, suffix8: str) -> str:
    raw = prefix8 + "0" + suffix8
    total = sum(int(TRANS.get(c, c)) * w for c, w in zip(raw, WEIGHTS))
    r = total % 11
    return prefix8 + ("X" if r == 10 else str(r)) + suffix8


VINS = [vin("1G1ZD5ST", "LF104221"), vin("1GCUYDED", "NZ210947"), vin("1GNSKCKD", "MR315502"),
        vin("1GYKNDRS", "NZ400871"), vin("3GNKBHR4", "PS128730"), vin("1GTU9EED", "PZ166095")]

APPLICANTS = [
    # id, first, last, ssn, dob, income, debt, line1, city, state, zip
    (1, "MARIA", "GONZALEZ", "912-55-7781", "1984-03-12", "6200.00", "1450.00",
     "4108 W PALMER ST", "CHICAGO", "IL", "60639"),
    (2, "JAMES", "CARTER", "909-32-1184", "1979-11-02", "8900.00", "2100.00",
     "77 BRIARWOOD LN", "ARLINGTON", "TX", "76012"),
    (3, "AISHA", "KHAN", "923-61-9042", "1991-06-25", "4100.00", "1650.00",
     "1520 ELM ST APT 4", "COLUMBUS", "OH", "43215"),
    (4, "ROBERT", "NGUYEN", "918-74-2290", "1968-01-30", "11500.00", "2600.00",
     "29 HARBOR VIEW DR", "TAMPA", "FL", "33602"),
    (5, "EMILY", "BROOKS", "915-09-3378", "1995-09-14", "3200.00", "1500.00",
     "880 CEDAR AVE", "LANSING", "MI", "48906"),
    (6, "DANIEL", "OKAFOR", "931-48-6610", "1988-12-05", "5400.00", "1900.00",
     "3301 MAPLE RIDGE RD", "CHARLOTTE", "NC", "28205"),
]

SCHEMA_SQL = """-- SYNTHETIC DATA MODEL. Not a GM Financial schema; built for the ExiQO Migrate demo.
-- Schema gmf: dealer channel origination, dealer funding and loan servicing.
-- Load order: schema.sql, then seed.sql. Target: PostgreSQL 14 or later.

create table gmf.applicant (
    applicant_id    integer       primary key,
    first_name      varchar(40)   not null,
    last_name       varchar(60)   not null,
    ssn             varchar(11)   not null,
    date_of_birth   date          not null,
    monthly_income  numeric(12,2),
    monthly_debt    numeric(12,2) not null default 0,
    address_line1   varchar(80)   not null,
    city            varchar(40)   not null,
    state_code      char(2)       not null,
    postal_code     varchar(10)   not null,
    created_at      timestamp     not null default current_timestamp
);

create table gmf.vehicle (
    vin             char(17)      primary key,
    model_year      integer       not null,
    make            varchar(30)   not null,
    model           varchar(40)   not null,
    msrp            numeric(12,2) not null
);

create table gmf.dealer (
    dealer_id       varchar(10)   primary key,
    dealer_name     varchar(80)   not null,
    state_code      char(2)       not null,
    status          varchar(12)   not null check (status in ('ACTIVE', 'SUSPENDED', 'TERMINATED')),
    funding_account varchar(20)   not null
);

create table gmf.credit_application (
    application_id        varchar(20)   primary key,
    applicant_id          integer       references gmf.applicant (applicant_id),
    ssn                   varchar(11)   not null,
    vin                   char(17),
    dealer_id             varchar(10)   references gmf.dealer (dealer_id),
    requested_amount      numeric(12,2) not null,
    requested_term_months integer       not null,
    status                varchar(12)   not null,
    submitted_at          timestamp     not null default current_timestamp
);
create index credit_application_ssn_idx on gmf.credit_application (ssn, submitted_at);

create table gmf.bureau_pull (
    pull_id          serial        primary key,
    application_id   varchar(20)   not null,
    equifax_score    integer,
    transunion_score integer,
    selected_score   integer,
    single_bureau    boolean       not null default false,
    pulled_at        timestamp     not null default current_timestamp
);

create table gmf.decision (
    application_id       varchar(20)   primary key,
    tier                 char(1),
    decision             varchar(10)   not null,
    apr                  numeric(5,2)  not null,
    approved_amount      numeric(12,2) not null,
    approved_term_months integer       not null,
    dti                  numeric(8,4),
    credit_score         integer,
    single_bureau        boolean       not null default false,
    decided_at           timestamp     not null default current_timestamp
);

create table gmf.deal (
    deal_id         varchar(12)   primary key,
    application_id  varchar(20)   not null references gmf.credit_application (application_id),
    dealer_id       varchar(10)   not null references gmf.dealer (dealer_id),
    approved_amount numeric(12,2) not null,
    status          varchar(12)   not null,
    created_at      timestamp     not null default current_timestamp
);

create table gmf.funding (
    funding_id      serial        primary key,
    deal_id         varchar(12)   not null references gmf.deal (deal_id),
    dealer_id       varchar(10)   not null,
    amount          numeric(12,2) not null,
    status          varchar(12)   not null,
    funded_at       timestamp
);

create table gmf.loan_account (
    account_number    varchar(12)   primary key,
    deal_id           varchar(12)   references gmf.deal (deal_id),
    applicant_id      integer       not null references gmf.applicant (applicant_id),
    principal_balance numeric(12,2) not null,
    interest_rate     numeric(5,2)  not null,
    monthly_payment   numeric(10,2) not null,
    fees_due          numeric(10,2) not null default 0,
    accrued_interest  numeric(10,2) not null default 0,
    next_due_date     date          not null,
    days_past_due     integer       not null default 0,
    last_payment_date date,
    status            varchar(10)   not null
);

create table gmf.payment (
    payment_id        varchar(20)   primary key,
    account_number    varchar(12)   not null references gmf.loan_account (account_number),
    amount            numeric(10,2) not null,
    channel           varchar(10)   not null,
    received_date     date          not null,
    fee_applied       numeric(10,2) not null default 0,
    late_fee_applied  numeric(10,2) not null default 0,
    interest_applied  numeric(10,2) not null default 0,
    principal_applied numeric(10,2) not null default 0,
    posted_at         timestamp     not null default current_timestamp
);

create table gmf.delinquency (
    delinquency_id  serial        primary key,
    account_number  varchar(12)   not null references gmf.loan_account (account_number),
    days_past_due   integer       not null,
    amount_past_due numeric(10,2) not null,
    detected_at     timestamp     not null default current_timestamp,
    status          varchar(12)   not null
);

create table gmf.audit_log (
    audit_id        serial        primary key,
    event_type      varchar(40)   not null,
    application_id  varchar(20),
    ssn             varchar(11),
    payload         varchar(2000),
    created_at      timestamp     not null default current_timestamp
);
"""


def seed_sql() -> str:
    q = lambda v: "null" if v is None else (f"'{v}'" if isinstance(v, str) else str(v))
    out = ["-- SYNTHETIC SEED DATA. Names, SSNs (9xx area, never issued), VINs and accounts are",
           "-- invented for the ExiQO Migrate demo. Dates are relative to the load date.",
           ""]
    out.append("insert into gmf.applicant (applicant_id, first_name, last_name, ssn, date_of_birth, "
               "monthly_income, monthly_debt, address_line1, city, state_code, postal_code) values")
    rows = [f"    ({a[0]}, '{a[1]}', '{a[2]}', '{a[3]}', date '{a[4]}', {a[5]}, {a[6]}, '{a[7]}', "
            f"'{a[8]}', '{a[9]}', '{a[10]}')" for a in APPLICANTS]
    out.append(",\n".join(rows) + ";\n")
    vehicles = [(VINS[0], 2020, "CHEVROLET", "BOLT EV", "37995.00"),
                (VINS[1], 2022, "CHEVROLET", "SILVERADO 1500", "48600.00"),
                (VINS[2], 2021, "CHEVROLET", "TAHOE", "61250.00"),
                (VINS[3], 2022, "CADILLAC", "XT5", "52900.00"),
                (VINS[4], 2023, "CHEVROLET", "TRAX", "23400.00"),
                (VINS[5], 2023, "GMC", "SIERRA 1500", "54100.00")]
    out.append("insert into gmf.vehicle (vin, model_year, make, model, msrp) values")
    out.append(",\n".join(f"    ('{v[0]}', {v[1]}, '{v[2]}', '{v[3]}', {v[4]})" for v in vehicles)
               + ";\n")
    out.append("-- D10021 is listed before D1002 on purpose: DealerFundingRequest matches dealer_id")
    out.append("-- with LIKE 'D1002%', so a funding request for suspended dealer D1002 finds D10021.")
    dealers = [("D10021", "LAKEVIEW CHEVROLET BUICK GMC", "IL", "ACTIVE", "FA-0010021"),
               ("D1002", "PRAIRIE STATE CHEVROLET", "IL", "SUSPENDED", "FA-0001002"),
               ("D2040", "HARBOR POINT CADILLAC", "FL", "ACTIVE", "FA-0002040"),
               ("D3317", "SUMMIT RIDGE CHEVROLET", "OH", "ACTIVE", "FA-0003317"),
               ("D4501", "RIVERBEND GMC", "NC", "TERMINATED", "FA-0004501")]
    out.append("insert into gmf.dealer (dealer_id, dealer_name, state_code, status, funding_account) values")
    out.append(",\n".join(f"    ({', '.join(q(x) for x in d)})" for d in dealers) + ";\n")
    apps = [("CA-2026-000101", 1, "912-55-7781", VINS[0], "D10021", "31500.00", 60, "APPROVED", 40),
            ("CA-2026-000102", 2, "909-32-1184", VINS[1], "D2040", "42000.00", 72, "APPROVED", 35),
            ("CA-2026-000103", 3, "923-61-9042", VINS[2], "D3317", "52000.00", 84, "APPROVED", 33),
            ("CA-2026-000104", 4, "918-74-2290", VINS[3], "D1002", "45500.00", 60, "APPROVED", 31),
            ("CA-2026-000105", 5, "915-09-3378", VINS[4], "D3317", "21000.00", 72, "DECLINED", 12),
            ("CA-2026-000106", 1, "912-55-7781", VINS[5], "D10021", "47000.00", 72, "APPROVED", 5)]
    out.append("-- CA-2026-000106 was submitted 5 days ago: a new application for SSN 912-55-7781")
    out.append("-- hits the 30-day duplicate rule in CreditApplicationIntake.")
    out.append("insert into gmf.credit_application (application_id, applicant_id, ssn, vin, dealer_id, "
               "requested_amount, requested_term_months, status, submitted_at) values")
    out.append(",\n".join(
        f"    ('{a[0]}', {a[1]}, '{a[2]}', '{a[3]}', '{a[4]}', {a[5]}, {a[6]}, '{a[7]}', "
        f"current_timestamp - interval '{a[8]} days')" for a in apps) + ";\n")
    pulls = [("CA-2026-000101", 752, 744, 752, "false"), ("CA-2026-000102", 701, 715, 715, "false"),
             ("CA-2026-000103", 648, 655, 655, "false"), ("CA-2026-000104", 781, None, 781, "true"),
             ("CA-2026-000105", 590, 602, 602, "false"), ("CA-2026-000106", 752, 744, 752, "false")]
    out.append("insert into gmf.bureau_pull (application_id, equifax_score, transunion_score, "
               "selected_score, single_bureau) values")
    out.append(",\n".join(f"    ('{p[0]}', {q(p[1])}, {q(p[2])}, {p[3]}, {p[4]})" for p in pulls)
               + ";\n")
    decisions = [("CA-2026-000101", "'A'", "APPROVED", "5.49", "31500.00", 60, "0.2339", 752, "false"),
                 ("CA-2026-000102", "'B'", "APPROVED", "7.99", "42000.00", 72, "0.2360", 715, "false"),
                 ("CA-2026-000103", "'C'", "APPROVED", "12.49", "52000.00", 72, "0.4024", 655, "false"),
                 ("CA-2026-000104", "'A'", "APPROVED", "5.49", "45500.00", 60, "0.2261", 781, "true"),
                 ("CA-2026-000105", "null", "DECLINED", "0", "0", 0, "0.4688", 602, "false"),
                 ("CA-2026-000106", "'A'", "APPROVED", "5.49", "47000.00", 72, "0.2339", 752, "false")]
    out.append("insert into gmf.decision (application_id, tier, decision, apr, approved_amount, "
               "approved_term_months, dti, credit_score, single_bureau) values")
    out.append(",\n".join(f"    ('{d[0]}', {d[1]}, '{d[2]}', {d[3]}, {d[4]}, {d[5]}, {d[6]}, {d[7]}, {d[8]})"
                          for d in decisions) + ";\n")
    deals = [("DL-50001", "CA-2026-000101", "D10021", "31500.00", "FUNDED"),
             ("DL-50002", "CA-2026-000102", "D2040", "42000.00", "FUNDED"),
             ("DL-50003", "CA-2026-000103", "D3317", "52000.00", "CONTRACTED"),
             ("DL-50004", "CA-2026-000104", "D1002", "45500.00", "CONTRACTED")]
    out.append("insert into gmf.deal (deal_id, application_id, dealer_id, approved_amount, status) values")
    out.append(",\n".join(f"    ({', '.join(q(x) if not x[0].isdigit() else x for x in d)})"
                          for d in deals) + ";\n")
    out.append("insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at) values")
    out.append("    ('DL-50001', 'D10021', 31500.00, 'FUNDED', current_timestamp - interval '38 days'),\n"
               "    ('DL-50002', 'D2040', 42000.00, 'FUNDED', current_timestamp - interval '33 days');\n")
    loans = [
        ("GMF-10000001", "'DL-50001'", 1, "30842.17", "5.49", "601.66", "0.00", "141.10", 20, 0, "ACTIVE"),
        ("GMF-10000002", "'DL-50002'", 2, "41315.52", "7.99", "736.42", "0.00", "275.09", 25, 0, "ACTIVE"),
        ("GMF-10000003", "null", 3, "18904.40", "12.49", "455.12", "0.00", "196.77", -12, 12, "ACTIVE"),
        ("GMF-10000004", "null", 4, "27760.95", "5.49", "529.30", "25.00", "127.01", -35, 35, "ACTIVE"),
        ("GMF-10000005", "null", 5, "9988.61", "12.49", "312.84", "50.00", "103.97", -47, 47, "ACTIVE"),
        ("GMF-10000006", "null", 6, "22410.00", "7.99", "468.20", "0.00", "149.22", -10, 10, "ACTIVE"),
        ("GMF-10000007", "null", 2, "15230.77", "7.99", "377.55", "25.00", "101.41", -31, 31, "ACTIVE"),
        ("GMF-10000008", "null", 1, "4120.06", "12.49", "298.00", "75.00", "42.88", -60, 60, "CLOSED"),
    ]
    out.append("-- GMF-10000006 is exactly 10 days past due: a payment posted today is charged the")
    out.append("-- 25.00 late fee because PaymentPosting compares days late with >= 10.")
    out.append("insert into gmf.loan_account (account_number, deal_id, applicant_id, principal_balance, "
               "interest_rate, monthly_payment, fees_due, accrued_interest, next_due_date, "
               "days_past_due, status) values")
    out.append(",\n".join(
        f"    ('{l[0]}', {l[1]}, {l[2]}, {l[3]}, {l[4]}, {l[5]}, {l[6]}, {l[7]}, "
        f"current_date + {l[8]}, {l[9]}, '{l[10]}')" for l in loans) + ";\n")
    payments = [("PMT-0000981", "GMF-10000001", "601.66", "ACH", 10, "0.00", "0.00", "140.82", "460.84"),
                ("PMT-0000982", "GMF-10000002", "736.42", "DEBIT", 5, "0.00", "0.00", "275.88", "460.54"),
                ("PMT-0000983", "GMF-10000003", "455.12", "LOCKBOX", 42, "0.00", "0.00", "197.12", "258.00"),
                ("PMT-0000984", "GMF-10000006", "468.20", "ACH", 40, "0.00", "0.00", "149.58", "318.62")]
    out.append("insert into gmf.payment (payment_id, account_number, amount, channel, received_date, "
               "fee_applied, late_fee_applied, interest_applied, principal_applied) values")
    out.append(",\n".join(
        f"    ('{p[0]}', '{p[1]}', {p[2]}, '{p[3]}', current_date - {p[4]}, {p[5]}, {p[6]}, {p[7]}, {p[8]})"
        for p in payments) + ";\n")
    out.append("insert into gmf.delinquency (account_number, days_past_due, amount_past_due, detected_at, status) values")
    out.append("    ('GMF-10000005', 46, 625.68, current_timestamp - interval '1 day', 'REFERRED');\n")
    out.append("-- Audit rows carry the applicant SSN in clear, as written by creditorigination.AuditLog.")
    out.append("insert into gmf.audit_log (event_type, application_id, ssn, payload, created_at) values")
    audits = [("CA-2026-000105", "915-09-3378", "dealer=D3317;vin=" + VINS[4] + ";amount=21000.00;term=72", 12),
              ("CA-2026-000106", "912-55-7781", "dealer=D10021;vin=" + VINS[5] + ";amount=47000.00;term=72", 5),
              ("CA-2026-000104", "918-74-2290", "dealer=D1002;vin=" + VINS[3] + ";amount=45500.00;term=60", 31)]
    out.append(",\n".join(
        f"    ('APPLICATION_RECEIVED', '{a[0]}', '{a[1]}', '{a[2]}', current_timestamp - interval '{a[3]} days')"
        for a in audits) + ";")
    return "\n".join(out) + "\n"


def _mapping(path, ssn, body=None, status=200, delay=None, priority=1):
    m = {"priority": priority, "metadata": {"synthetic": True},
         "request": {"method": "POST", "urlPath": path,
                     "bodyPatterns": [{"matchesJsonPath": {"expression": "$.ssn",
                                                           "equalTo": ssn}}]},
         "response": {"status": status, "headers": {"Content-Type": "application/json"}}}
    if body is not None:
        m["response"]["jsonBody"] = body
    if delay:
        m["response"]["fixedDelayMilliseconds"] = delay
    return m


def stub(bureau, path, scores, delays, failures, default_score):
    maps = []
    for ssn, score in scores.items():
        maps.append(_mapping(path, ssn, {"bureau": bureau, "score": score,
                                          "referenceId": f"{bureau[:3]}-{ssn[-4:]}-01",
                                          "inquiries": 1},
                             delay=delays.get(ssn)))
    for ssn in failures:
        maps.append(_mapping(path, ssn, {"error": "SERVICE_UNAVAILABLE"}, status=503))
    maps.append({"priority": 10, "metadata": {"synthetic": True},
                 "request": {"method": "POST", "urlPath": path},
                 "response": {"status": 200, "headers": {"Content-Type": "application/json"},
                              "jsonBody": {"bureau": bureau, "score": default_score,
                                           "referenceId": f"{bureau[:3]}-DEFAULT",
                                           "inquiries": 0}}})
    return json.dumps({"mappings": maps}, indent=2) + "\n"


EQUIFAX = stub("EQUIFAX", "/consumer-credit/v2/scores",
               {"912-55-7781": 752, "909-32-1184": 701, "923-61-9042": 648, "918-74-2290": 781,
                "915-09-3378": 590}, {}, ["931-48-6610"], 690)
TRANSUNION = stub("TRANSUNION", "/credit-report/v1/score",
                  {"912-55-7781": 744, "909-32-1184": 715, "923-61-9042": 655, "918-74-2290": 779,
                   "915-09-3378": 602}, {"918-74-2290": 2500}, ["931-48-6610"], 684)
