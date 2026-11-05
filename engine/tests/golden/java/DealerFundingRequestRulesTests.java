package com.gmf.gmfcredit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.JsonNode;
import com.gmf.gmfcredit.core.DealerFundingRequestService;
import com.gmf.gmfcredit.support.Fixtures;
import com.gmf.gmfcredit.support.ScenarioSupport;
import java.math.BigDecimal;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * One test per approved rule of DealerFundingRequest, named in the rule's own words; written before the code.
 *
 * <p>A test fails with no test body until the Test Engineer writes it; gate G4 refuses the build while any approved rule has no test body.
 */
class DealerFundingRequestRulesTests extends ScenarioSupport {
    /**
     * R1: A dealer must be ACTIVE to receive funding.
     *
     * <p>Citations: LookupDealer (condition): $LookupDealer/Record[1]/status = 'ACTIVE'
     */
    @Test
    @DisplayName("R1 A dealer must be ACTIVE to receive funding.")
    void r1_a_dealer_must_be_active_to_receive_funding() {
        fail("no test body for R1");
    }

    /**
     * R2: The funding amount must be within 2% of the approved deal amount.
     *
     * <p>Citations: LookupDeal (condition): number($LookupDeal/Record[1]/approved_amount) * 0.02
     */
    @Test
    @DisplayName("R2 The funding amount must be within 2% of the approved deal amount.")
    void r2_the_funding_amount_must_be_within_2_of_the_approved_deal_amount() {
        fail("no test body for R2");
    }

    /**
     * R3: A deal is funded at most once; a repeat request is logged as DF-409.
     *
     * <p>Citations: CheckExistingFunding (sql): select funding_id, status; InsertFunding (sql): insert into gmf.funding
     */
    @Test
    @DisplayName("R3 A deal is funded at most once; a repeat request is logged as DF-409.")
    void r3_a_deal_is_funded_at_most_once_a_repeat_request_is_logged_as_df_409() {
        fail("no test body for R3");
    }
}
