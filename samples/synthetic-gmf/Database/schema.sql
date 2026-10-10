-- SYNTHETIC DATA MODEL. Not a GM Financial schema; built for the ExiQO Migrate demo.
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
