"""One test per approved rule of DealerFundingRequest, named in the rule's own words; written before the code. A test fails with no test body until the Test Engineer writes it; gate G4 refuses the build while any approved rule has no test body."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from gmf_credit.core import tib
from gmf_credit.core.dealer_funding_request_service import DealerFundingRequestService
from gmf_credit.core.tib import ProcessResult, ProcessState
from tests.scenario_support import ScenarioSupport


# R1: A dealer must be ACTIVE to receive funding.
# Citations: LookupDealer (condition): $LookupDealer/Record[1]/status = 'ACTIVE'
def test_r1_a_dealer_must_be_active_to_receive_funding(support: ScenarioSupport) -> None:
    pytest.fail("no test body for R1")


# R2: The funding amount must be within 2% of the approved deal amount.
# Citations: LookupDeal (condition): number($LookupDeal/Record[1]/approved_amount) * 0.02
def test_r2_the_funding_amount_must_be_within_2_of_the_approved_deal_amount(support: ScenarioSupport) -> None:
    pytest.fail("no test body for R2")


# R3: A deal is funded at most once; a repeat request is logged as DF-409.
# Citations: CheckExistingFunding (sql): select funding_id, status; InsertFunding (sql): insert into gmf.funding
def test_r3_a_deal_is_funded_at_most_once_a_repeat_request_is_logged_as_df_409(support: ScenarioSupport) -> None:
    pytest.fail("no test body for R3")
