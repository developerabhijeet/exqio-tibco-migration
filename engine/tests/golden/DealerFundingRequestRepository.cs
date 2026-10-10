using System.Data;
using Dapper;
using GmfCredit.Core.Data;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace GmfCredit.Infrastructure.Repositories;

/// <summary>Database steps of DealerFundingRequest on Dapper and Npgsql; every statement is the TIBCO SQL verbatim.</summary>
public sealed class DealerFundingRequestRepository([FromKeyedServices("GMFFundingDB")] NpgsqlDataSource gmfFundingDB) : IDealerFundingRequestRepository
{
    private const string LookupDealerSql = """
        select dealer_id, dealer_name, status
          from gmf.dealer
         where dealer_id like @p0
        """;

    /// <summary>TIBCO step LookupDealer (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.</summary>
    /// <remarks>Rules: R1. Findings: S2-DealerFundingRequest-1 (kept). TIBCO: LookupDealer line 1014
    /// <para>Parameters: the JDBC ? placeholders were converted at generation time to named Npgsql parameters, bound in order: @p0 = dealerId.</para></remarks>
    public async Task<IReadOnlyList<LookupDealerRow>> LookupDealerAsync(LookupDealerInput input, CancellationToken cancellationToken)
    {
        var parameters = new DynamicParameters();
        parameters.Add("p0", input.DealerId);
        var command = new CommandDefinition(LookupDealerSql, parameters, commandTimeout: 10, commandType: CommandType.Text, cancellationToken: cancellationToken);
        await using var connection = await gmfFundingDB.OpenConnectionAsync(cancellationToken);
        var rows = await connection.QueryAsync<LookupDealerRow>(command);
        return [.. rows.Take(100)]; // TIBCO maxRows 100
    }

    private const string LookupDealSql = """
        select deal_id, dealer_id, approved_amount, status
          from gmf.deal
         where deal_id = @p0
        """;

    /// <summary>TIBCO step LookupDeal (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.</summary>
    /// <remarks>Rules: R2, R4. Findings: none. TIBCO: LookupDeal line 1091
    /// <para>Parameters: the JDBC ? placeholders were converted at generation time to named Npgsql parameters, bound in order: @p0 = dealId.</para></remarks>
    public async Task<IReadOnlyList<LookupDealRow>> LookupDealAsync(LookupDealInput input, CancellationToken cancellationToken)
    {
        var parameters = new DynamicParameters();
        parameters.Add("p0", input.DealId);
        var command = new CommandDefinition(LookupDealSql, parameters, commandTimeout: 10, commandType: CommandType.Text, cancellationToken: cancellationToken);
        await using var connection = await gmfFundingDB.OpenConnectionAsync(cancellationToken);
        var rows = await connection.QueryAsync<LookupDealRow>(command);
        return [.. rows.Take(100)]; // TIBCO maxRows 100
    }

    private const string CheckExistingFundingSql = """
        select funding_id, status
          from gmf.funding
         where deal_id = @p0
        """;

    /// <summary>TIBCO step CheckExistingFunding (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.</summary>
    /// <remarks>Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: CheckExistingFunding line 1199
    /// <para>Parameters: the JDBC ? placeholders were converted at generation time to named Npgsql parameters, bound in order: @p0 = dealId.</para></remarks>
    public async Task<IReadOnlyList<CheckExistingFundingRow>> CheckExistingFundingAsync(CheckExistingFundingInput input, CancellationToken cancellationToken)
    {
        var parameters = new DynamicParameters();
        parameters.Add("p0", input.DealId);
        var command = new CommandDefinition(CheckExistingFundingSql, parameters, commandTimeout: 10, commandType: CommandType.Text, cancellationToken: cancellationToken);
        await using var connection = await gmfFundingDB.OpenConnectionAsync(cancellationToken);
        var rows = await connection.QueryAsync<CheckExistingFundingRow>(command);
        return [.. rows.Take(100)]; // TIBCO maxRows 100
    }

    private const string InsertFundingSql = """
        insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at)
        values (@p0, @p1, @p2, 'FUNDED', current_timestamp)
        """;

    /// <summary>TIBCO step InsertFunding (jdbc.update) on GMFFundingDB: the SQL statement is kept verbatim.</summary>
    /// <remarks>Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: InsertFunding line 1292
    /// <para>Parameters: the JDBC ? placeholders were converted at generation time to named Npgsql parameters, bound in order: @p0 = dealId, @p1 = dealerId, @p2 = amount.</para></remarks>
    public async Task<int> InsertFundingAsync(InsertFundingInput input, CancellationToken cancellationToken)
    {
        var parameters = new DynamicParameters();
        parameters.Add("p0", input.DealId);
        parameters.Add("p1", input.DealerId);
        parameters.Add("p2", input.Amount);
        var command = new CommandDefinition(InsertFundingSql, parameters, commandTimeout: 10, commandType: CommandType.Text, cancellationToken: cancellationToken);
        await using var connection = await gmfFundingDB.OpenConnectionAsync(cancellationToken);
        return await connection.ExecuteAsync(command);
    }
}
