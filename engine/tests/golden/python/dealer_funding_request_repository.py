"""Database steps of DealerFundingRequest on psycopg 3: every statement is the TIBCO SQL verbatim, its placeholders bound in order."""

from __future__ import annotations

from typing import Any

from gmf_credit.core import tib
from gmf_credit.infrastructure.database import Database

# TIBCO step LookupDealer (jdbc.query) on GMFFundingDB: the SQL statement verbatim.
LOOKUP_DEALER_SQL = """select dealer_id, dealer_name, status
  from gmf.dealer
 where dealer_id like %s"""

# TIBCO step LookupDeal (jdbc.query) on GMFFundingDB: the SQL statement verbatim.
LOOKUP_DEAL_SQL = """select deal_id, dealer_id, approved_amount, status
  from gmf.deal
 where deal_id = %s"""

# TIBCO step CheckExistingFunding (jdbc.query) on GMFFundingDB: the SQL statement verbatim.
CHECK_EXISTING_FUNDING_SQL = """select funding_id, status
  from gmf.funding
 where deal_id = %s"""

# TIBCO step InsertFunding (jdbc.update) on GMFFundingDB: the SQL statement verbatim.
INSERT_FUNDING_SQL = """insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at)
values (%s, %s, %s, 'FUNDED', current_timestamp)"""


class DealerFundingRequestRepository:
    """The JDBC activities of DealerFundingRequest."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def lookup_dealer(self, values: dict[str, Any]) -> dict[str, Any]:
        """TIBCO step LookupDealer (jdbc.query) on GMFFundingDB. Rules: R1. Findings: S2-DealerFundingRequest-1 (kept). TIBCO: LookupDealer line 1014

        Parameters, bound in order to the %s placeholders: 1 = dealerId (string).
        TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
        """
        rows = await self.database.query("GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", LOOKUP_DEALER_SQL, [
            tib.jdbc(tib.path(values, "dealerId"), "string"),
        ], 10, 100)
        return tib.records(rows)

    async def lookup_deal(self, values: dict[str, Any]) -> dict[str, Any]:
        """TIBCO step LookupDeal (jdbc.query) on GMFFundingDB. Rules: R2, R4. Findings: none. TIBCO: LookupDeal line 1091

        Parameters, bound in order to the %s placeholders: 1 = dealId (string).
        TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
        """
        rows = await self.database.query("GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", LOOKUP_DEAL_SQL, [
            tib.jdbc(tib.path(values, "dealId"), "string"),
        ], 10, 100)
        return tib.records(rows)

    async def check_existing_funding(self, values: dict[str, Any]) -> dict[str, Any]:
        """TIBCO step CheckExistingFunding (jdbc.query) on GMFFundingDB. Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: CheckExistingFunding line 1199

        Parameters, bound in order to the %s placeholders: 1 = dealId (string).
        TIBCO maxRows 100: rows past 100 are dropped; the SQL stays verbatim.
        """
        rows = await self.database.query("GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", CHECK_EXISTING_FUNDING_SQL, [
            tib.jdbc(tib.path(values, "dealId"), "string"),
        ], 10, 100)
        return tib.records(rows)

    async def insert_funding(self, values: dict[str, Any]) -> dict[str, Any]:
        """TIBCO step InsertFunding (jdbc.update) on GMFFundingDB. Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: InsertFunding line 1292

        Parameters, bound in order to the %s placeholders: 1 = dealId (string), 2 = dealerId (string), 3 = amount (decimal).
        """
        count = await self.database.update("GMFFundingDB", "CONNECTION_STRINGS_GMF_FUNDING_DB", INSERT_FUNDING_SQL, [
            tib.jdbc(tib.path(values, "dealId"), "string"),
            tib.jdbc(tib.path(values, "dealerId"), "string"),
            tib.jdbc(tib.path(values, "amount"), "decimal"),
        ], 10)
        return tib.updated(count)
