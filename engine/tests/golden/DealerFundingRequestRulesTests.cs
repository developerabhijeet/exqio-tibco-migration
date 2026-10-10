using Xunit;

namespace GmfCredit.Tests;

/// <summary>One test per approved rule of DealerFundingRequest, named in the rule's own words; written before the code.</summary>
/// <remarks>A test fails with no test body until the Code Builder writes it; gate G4 refuses the build while any approved rule has no test body.</remarks>
public sealed class DealerFundingRequestRulesTests
{
    /// <summary>R1: A dealer must be ACTIVE to receive funding.</summary>
    /// <remarks>Citations: LookupDealer (condition): $LookupDealer/Record[1]/status = 'ACTIVE'</remarks>
    [Fact(DisplayName = "R1 A dealer must be ACTIVE to receive funding.")]
    [Trait("Rule", "R1")]
    public Task R1_a_dealer_must_be_active_to_receive_funding()
    {
        Assert.Fail("no test body for R1");
        return Task.CompletedTask;
    }

    /// <summary>R2: The funding amount must be within 2% of the approved deal amount.</summary>
    /// <remarks>Citations: LookupDeal (condition): number($LookupDeal/Record[1]/approved_amount) * 0.02</remarks>
    [Fact(DisplayName = "R2 The funding amount must be within 2% of the approved deal amount.")]
    [Trait("Rule", "R2")]
    public Task R2_the_funding_amount_must_be_within_2_of_the_approved_deal_amount()
    {
        Assert.Fail("no test body for R2");
        return Task.CompletedTask;
    }

    /// <summary>R3: A deal is funded at most once; a repeat request is logged as DF-409.</summary>
    /// <remarks>Citations: CheckExistingFunding (sql): select funding_id, status; InsertFunding (sql): insert into gmf.funding</remarks>
    [Fact(DisplayName = "R3 A deal is funded at most once; a repeat request is logged as DF-409.")]
    [Trait("Rule", "R3")]
    public Task R3_a_deal_is_funded_at_most_once_a_repeat_request_is_logged_as_df_409()
    {
        Assert.Fail("no test body for R3");
        return Task.CompletedTask;
    }
}
