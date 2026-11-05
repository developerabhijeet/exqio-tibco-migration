package infrastructure

import (
	"context"

	"gmf-credit/internal/tib"
)

// dealerFundingRequestLookupDealerSQL is the SQL of TIBCO step LookupDealer, verbatim.
const dealerFundingRequestLookupDealerSQL = `
select dealer_id, dealer_name, status
  from gmf.dealer
 where dealer_id like $1
`

// dealerFundingRequestLookupDealSQL is the SQL of TIBCO step LookupDeal, verbatim.
const dealerFundingRequestLookupDealSQL = `
select deal_id, dealer_id, approved_amount, status
  from gmf.deal
 where deal_id = $1
`

// dealerFundingRequestCheckExistingFundingSQL is the SQL of TIBCO step CheckExistingFunding, verbatim.
const dealerFundingRequestCheckExistingFundingSQL = `
select funding_id, status
  from gmf.funding
 where deal_id = $1
`

// dealerFundingRequestInsertFundingSQL is the SQL of TIBCO step InsertFunding, verbatim.
const dealerFundingRequestInsertFundingSQL = `
insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at)
values ($1, $2, $3, 'FUNDED', current_timestamp)
`

// DealerFundingRequestRepository holds the database steps of DealerFundingRequest: every statement is the TIBCO SQL verbatim, its placeholders bound in order.
type DealerFundingRequestRepository struct {
	db *DataSources
}

// NewDealerFundingRequestRepository is the repository over the data sources.
func NewDealerFundingRequestRepository(db *DataSources) *DealerFundingRequestRepository {
	return &DealerFundingRequestRepository{db: db}
}

// LookupDealer is TIBCO step LookupDealer (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
//
// Rules: R1. Findings: S2-DealerFundingRequest-1 (kept). TIBCO: LookupDealer line 1014
//
// Parameters, bound in order to the placeholders: $1 = dealerId (string).
//
// TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
func (s *DealerFundingRequestRepository) LookupDealer(ctx context.Context, input tib.Value) (tib.Value, error) {
	return s.db.Query(ctx, "GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", 10, 100, dealerFundingRequestLookupDealerSQL,
		tib.JDBC(tib.Path(input, "dealerId"), "string"))
}

// LookupDeal is TIBCO step LookupDeal (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
//
// Rules: R2, R4. Findings: none. TIBCO: LookupDeal line 1091
//
// Parameters, bound in order to the placeholders: $1 = dealId (string).
//
// TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
func (s *DealerFundingRequestRepository) LookupDeal(ctx context.Context, input tib.Value) (tib.Value, error) {
	return s.db.Query(ctx, "GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", 10, 100, dealerFundingRequestLookupDealSQL,
		tib.JDBC(tib.Path(input, "dealId"), "string"))
}

// CheckExistingFunding is TIBCO step CheckExistingFunding (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
//
// Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: CheckExistingFunding line 1199
//
// Parameters, bound in order to the placeholders: $1 = dealId (string).
//
// TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
func (s *DealerFundingRequestRepository) CheckExistingFunding(ctx context.Context, input tib.Value) (tib.Value, error) {
	return s.db.Query(ctx, "GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", 10, 100, dealerFundingRequestCheckExistingFundingSQL,
		tib.JDBC(tib.Path(input, "dealId"), "string"))
}

// InsertFunding is TIBCO step InsertFunding (jdbc.update) on GMFFundingDB: the SQL statement is kept verbatim.
//
// Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: InsertFunding line 1292
//
// Parameters, bound in order to the placeholders: $1 = dealId (string), $2 = dealerId (string), $3 = amount (decimal).
func (s *DealerFundingRequestRepository) InsertFunding(ctx context.Context, input tib.Value) (tib.Value, error) {
	return s.db.Exec(ctx, "GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", 10, dealerFundingRequestInsertFundingSQL,
		tib.JDBC(tib.Path(input, "dealId"), "string"),
		tib.JDBC(tib.Path(input, "dealerId"), "string"),
		tib.JDBC(tib.Path(input, "amount"), "decimal"))
}
