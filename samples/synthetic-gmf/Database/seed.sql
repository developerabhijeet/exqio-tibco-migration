-- SYNTHETIC SEED DATA. Names, SSNs (9xx area, never issued), VINs and accounts are
-- invented for the ExiQO Migrate demo. Dates are relative to the load date.

insert into gmf.applicant (applicant_id, first_name, last_name, ssn, date_of_birth, monthly_income, monthly_debt, address_line1, city, state_code, postal_code) values
    (1, 'MARIA', 'GONZALEZ', '912-55-7781', date '1984-03-12', 6200.00, 1450.00, '4108 W PALMER ST', 'CHICAGO', 'IL', '60639'),
    (2, 'JAMES', 'CARTER', '909-32-1184', date '1979-11-02', 8900.00, 2100.00, '77 BRIARWOOD LN', 'ARLINGTON', 'TX', '76012'),
    (3, 'AISHA', 'KHAN', '923-61-9042', date '1991-06-25', 4100.00, 1650.00, '1520 ELM ST APT 4', 'COLUMBUS', 'OH', '43215'),
    (4, 'ROBERT', 'NGUYEN', '918-74-2290', date '1968-01-30', 11500.00, 2600.00, '29 HARBOR VIEW DR', 'TAMPA', 'FL', '33602'),
    (5, 'EMILY', 'BROOKS', '915-09-3378', date '1995-09-14', 3200.00, 1500.00, '880 CEDAR AVE', 'LANSING', 'MI', '48906'),
    (6, 'DANIEL', 'OKAFOR', '931-48-6610', date '1988-12-05', 5400.00, 1900.00, '3301 MAPLE RIDGE RD', 'CHARLOTTE', 'NC', '28205');

insert into gmf.vehicle (vin, model_year, make, model, msrp) values
    ('1G1ZD5ST5LF104221', 2020, 'CHEVROLET', 'BOLT EV', 37995.00),
    ('1GCUYDED3NZ210947', 2022, 'CHEVROLET', 'SILVERADO 1500', 48600.00),
    ('1GNSKCKD1MR315502', 2021, 'CHEVROLET', 'TAHOE', 61250.00),
    ('1GYKNDRS0NZ400871', 2022, 'CADILLAC', 'XT5', 52900.00),
    ('3GNKBHR44PS128730', 2023, 'CHEVROLET', 'TRAX', 23400.00),
    ('1GTU9EED1PZ166095', 2023, 'GMC', 'SIERRA 1500', 54100.00);

-- D10021 is listed before D1002 on purpose: DealerFundingRequest matches dealer_id
-- with LIKE 'D1002%', so a funding request for suspended dealer D1002 finds D10021.
insert into gmf.dealer (dealer_id, dealer_name, state_code, status, funding_account) values
    ('D10021', 'LAKEVIEW CHEVROLET BUICK GMC', 'IL', 'ACTIVE', 'FA-0010021'),
    ('D1002', 'PRAIRIE STATE CHEVROLET', 'IL', 'SUSPENDED', 'FA-0001002'),
    ('D2040', 'HARBOR POINT CADILLAC', 'FL', 'ACTIVE', 'FA-0002040'),
    ('D3317', 'SUMMIT RIDGE CHEVROLET', 'OH', 'ACTIVE', 'FA-0003317'),
    ('D4501', 'RIVERBEND GMC', 'NC', 'TERMINATED', 'FA-0004501');

-- CA-2026-000106 was submitted 5 days ago: a new application for SSN 912-55-7781
-- hits the 30-day duplicate rule in CreditApplicationIntake.
insert into gmf.credit_application (application_id, applicant_id, ssn, vin, dealer_id, requested_amount, requested_term_months, status, submitted_at) values
    ('CA-2026-000101', 1, '912-55-7781', '1G1ZD5ST5LF104221', 'D10021', 31500.00, 60, 'APPROVED', current_timestamp - interval '40 days'),
    ('CA-2026-000102', 2, '909-32-1184', '1GCUYDED3NZ210947', 'D2040', 42000.00, 72, 'APPROVED', current_timestamp - interval '35 days'),
    ('CA-2026-000103', 3, '923-61-9042', '1GNSKCKD1MR315502', 'D3317', 52000.00, 84, 'APPROVED', current_timestamp - interval '33 days'),
    ('CA-2026-000104', 4, '918-74-2290', '1GYKNDRS0NZ400871', 'D1002', 45500.00, 60, 'APPROVED', current_timestamp - interval '31 days'),
    ('CA-2026-000105', 5, '915-09-3378', '3GNKBHR44PS128730', 'D3317', 21000.00, 72, 'DECLINED', current_timestamp - interval '12 days'),
    ('CA-2026-000106', 1, '912-55-7781', '1GTU9EED1PZ166095', 'D10021', 47000.00, 72, 'APPROVED', current_timestamp - interval '5 days');

insert into gmf.bureau_pull (application_id, equifax_score, transunion_score, selected_score, single_bureau) values
    ('CA-2026-000101', 752, 744, 752, false),
    ('CA-2026-000102', 701, 715, 715, false),
    ('CA-2026-000103', 648, 655, 655, false),
    ('CA-2026-000104', 781, null, 781, true),
    ('CA-2026-000105', 590, 602, 602, false),
    ('CA-2026-000106', 752, 744, 752, false);

insert into gmf.decision (application_id, tier, decision, apr, approved_amount, approved_term_months, dti, credit_score, single_bureau) values
    ('CA-2026-000101', 'A', 'APPROVED', 5.49, 31500.00, 60, 0.2339, 752, false),
    ('CA-2026-000102', 'B', 'APPROVED', 7.99, 42000.00, 72, 0.2360, 715, false),
    ('CA-2026-000103', 'C', 'APPROVED', 12.49, 52000.00, 72, 0.4024, 655, false),
    ('CA-2026-000104', 'A', 'APPROVED', 5.49, 45500.00, 60, 0.2261, 781, true),
    ('CA-2026-000105', null, 'DECLINED', 0, 0, 0, 0.4688, 602, false),
    ('CA-2026-000106', 'A', 'APPROVED', 5.49, 47000.00, 72, 0.2339, 752, false);

insert into gmf.deal (deal_id, application_id, dealer_id, approved_amount, status) values
    ('DL-50001', 'CA-2026-000101', 'D10021', 31500.00, 'FUNDED'),
    ('DL-50002', 'CA-2026-000102', 'D2040', 42000.00, 'FUNDED'),
    ('DL-50003', 'CA-2026-000103', 'D3317', 52000.00, 'CONTRACTED'),
    ('DL-50004', 'CA-2026-000104', 'D1002', 45500.00, 'CONTRACTED');

insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at) values
    ('DL-50001', 'D10021', 31500.00, 'FUNDED', current_timestamp - interval '38 days'),
    ('DL-50002', 'D2040', 42000.00, 'FUNDED', current_timestamp - interval '33 days');

-- GMF-10000006 is exactly 10 days past due: a payment posted today is charged the
-- 25.00 late fee because PaymentPosting compares days late with >= 10.
insert into gmf.loan_account (account_number, deal_id, applicant_id, principal_balance, interest_rate, monthly_payment, fees_due, accrued_interest, next_due_date, days_past_due, status) values
    ('GMF-10000001', 'DL-50001', 1, 30842.17, 5.49, 601.66, 0.00, 141.10, current_date + 20, 0, 'ACTIVE'),
    ('GMF-10000002', 'DL-50002', 2, 41315.52, 7.99, 736.42, 0.00, 275.09, current_date + 25, 0, 'ACTIVE'),
    ('GMF-10000003', null, 3, 18904.40, 12.49, 455.12, 0.00, 196.77, current_date + -12, 12, 'ACTIVE'),
    ('GMF-10000004', null, 4, 27760.95, 5.49, 529.30, 25.00, 127.01, current_date + -35, 35, 'ACTIVE'),
    ('GMF-10000005', null, 5, 9988.61, 12.49, 312.84, 50.00, 103.97, current_date + -47, 47, 'ACTIVE'),
    ('GMF-10000006', null, 6, 22410.00, 7.99, 468.20, 0.00, 149.22, current_date + -10, 10, 'ACTIVE'),
    ('GMF-10000007', null, 2, 15230.77, 7.99, 377.55, 25.00, 101.41, current_date + -31, 31, 'ACTIVE'),
    ('GMF-10000008', null, 1, 4120.06, 12.49, 298.00, 75.00, 42.88, current_date + -60, 60, 'CLOSED');

insert into gmf.payment (payment_id, account_number, amount, channel, received_date, fee_applied, late_fee_applied, interest_applied, principal_applied) values
    ('PMT-0000981', 'GMF-10000001', 601.66, 'ACH', current_date - 10, 0.00, 0.00, 140.82, 460.84),
    ('PMT-0000982', 'GMF-10000002', 736.42, 'DEBIT', current_date - 5, 0.00, 0.00, 275.88, 460.54),
    ('PMT-0000983', 'GMF-10000003', 455.12, 'LOCKBOX', current_date - 42, 0.00, 0.00, 197.12, 258.00),
    ('PMT-0000984', 'GMF-10000006', 468.20, 'ACH', current_date - 40, 0.00, 0.00, 149.58, 318.62);

insert into gmf.delinquency (account_number, days_past_due, amount_past_due, detected_at, status) values
    ('GMF-10000005', 46, 625.68, current_timestamp - interval '1 day', 'REFERRED');

-- Audit rows carry the applicant SSN in clear, as written by creditorigination.AuditLog.
insert into gmf.audit_log (event_type, application_id, ssn, payload, created_at) values
    ('APPLICATION_RECEIVED', 'CA-2026-000105', '915-09-3378', 'dealer=D3317;vin=3GNKBHR44PS128730;amount=21000.00;term=72', current_timestamp - interval '12 days'),
    ('APPLICATION_RECEIVED', 'CA-2026-000106', '912-55-7781', 'dealer=D10021;vin=1GTU9EED1PZ166095;amount=47000.00;term=72', current_timestamp - interval '5 days'),
    ('APPLICATION_RECEIVED', 'CA-2026-000104', '918-74-2290', 'dealer=D1002;vin=1GYKNDRS0NZ400871;amount=45500.00;term=60', current_timestamp - interval '31 days');
