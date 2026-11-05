package com.gmf.gmfcredit.infrastructure;

import com.fasterxml.jackson.databind.JsonNode;
import com.gmf.gmfcredit.core.tibco.TibValue;
import java.sql.Types;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.SqlParameterValue;
import org.springframework.stereotype.Repository;

/** Database steps of DealerFundingRequest on Spring JDBC: every statement is the TIBCO SQL verbatim, its ? placeholders bound in order. */
@Repository
public class DealerFundingRequestRepository {
    private final DataSources dataSources;

    public DealerFundingRequestRepository(DataSources dataSources) {
        this.dataSources = dataSources;
    }

    private static final String LOOKUP_DEALER_SQL = """
            select dealer_id, dealer_name, status
              from gmf.dealer
             where dealer_id like ?
            """;

    /**
     * TIBCO step LookupDealer (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
     *
     * <p>Rules: R1. Findings: S2-DealerFundingRequest-1 (kept). TIBCO: LookupDealer line 1014
     *
     * <p>Parameters, bound in order to the JDBC ? placeholders: 1 = dealerId (string).
     *
     * <p>TIBCO maxRows 100: rows past 100 are dropped (JdbcTemplate maxRows); the SQL stays verbatim.
     */
    public JsonNode lookupDealer(JsonNode input) {
        JdbcTemplate jdbc = dataSources.jdbc("GMFFundingDB", "connection-strings.gmf-funding-db", 10, 100);
        return TibValue.records(jdbc.queryForList(LOOKUP_DEALER_SQL,
                new SqlParameterValue(Types.VARCHAR, TibValue.jdbc(TibValue.path(input, "dealerId"), "string"))));
    }

    private static final String LOOKUP_DEAL_SQL = """
            select deal_id, dealer_id, approved_amount, status
              from gmf.deal
             where deal_id = ?
            """;

    /**
     * TIBCO step LookupDeal (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
     *
     * <p>Rules: R2, R4. Findings: none. TIBCO: LookupDeal line 1091
     *
     * <p>Parameters, bound in order to the JDBC ? placeholders: 1 = dealId (string).
     *
     * <p>TIBCO maxRows 100: rows past 100 are dropped (JdbcTemplate maxRows); the SQL stays verbatim.
     */
    public JsonNode lookupDeal(JsonNode input) {
        JdbcTemplate jdbc = dataSources.jdbc("GMFFundingDB", "connection-strings.gmf-funding-db", 10, 100);
        return TibValue.records(jdbc.queryForList(LOOKUP_DEAL_SQL,
                new SqlParameterValue(Types.VARCHAR, TibValue.jdbc(TibValue.path(input, "dealId"), "string"))));
    }

    private static final String CHECK_EXISTING_FUNDING_SQL = """
            select funding_id, status
              from gmf.funding
             where deal_id = ?
            """;

    /**
     * TIBCO step CheckExistingFunding (jdbc.query) on GMFFundingDB: the SQL statement is kept verbatim.
     *
     * <p>Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: CheckExistingFunding line 1199
     *
     * <p>Parameters, bound in order to the JDBC ? placeholders: 1 = dealId (string).
     *
     * <p>TIBCO maxRows 100: rows past 100 are dropped (JdbcTemplate maxRows); the SQL stays verbatim.
     */
    public JsonNode checkExistingFunding(JsonNode input) {
        JdbcTemplate jdbc = dataSources.jdbc("GMFFundingDB", "connection-strings.gmf-funding-db", 10, 100);
        return TibValue.records(jdbc.queryForList(CHECK_EXISTING_FUNDING_SQL,
                new SqlParameterValue(Types.VARCHAR, TibValue.jdbc(TibValue.path(input, "dealId"), "string"))));
    }

    private static final String INSERT_FUNDING_SQL = """
            insert into gmf.funding (deal_id, dealer_id, amount, status, funded_at)
            values (?, ?, ?, 'FUNDED', current_timestamp)
            """;

    /**
     * TIBCO step InsertFunding (jdbc.update) on GMFFundingDB: the SQL statement is kept verbatim.
     *
     * <p>Rules: R3. Findings: S1-DealerFundingRequest-1 (kept). TIBCO: InsertFunding line 1292
     *
     * <p>Parameters, bound in order to the JDBC ? placeholders: 1 = dealId (string), 2 = dealerId (string), 3 = amount (decimal).
     */
    public JsonNode insertFunding(JsonNode input) {
        JdbcTemplate jdbc = dataSources.jdbc("GMFFundingDB", "connection-strings.gmf-funding-db", 10, 0);
        return TibValue.updated(jdbc.update(INSERT_FUNDING_SQL,
                new SqlParameterValue(Types.VARCHAR, TibValue.jdbc(TibValue.path(input, "dealId"), "string")),
                new SqlParameterValue(Types.VARCHAR, TibValue.jdbc(TibValue.path(input, "dealerId"), "string")),
                new SqlParameterValue(Types.NUMERIC, TibValue.jdbc(TibValue.path(input, "amount"), "decimal"))));
    }
}
