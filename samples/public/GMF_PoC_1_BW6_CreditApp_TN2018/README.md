# PoC sample 1: TIBCO BusinessWorks 6 credit application (TN2018)

Source: github.com/TIBCOSoftware/bw-samples, folder TN2018/Apps (TIBCO NOW 2018 demo).
Licence: TIBCO "Samples Internal End User License" (see LICENSE_TIBCO_Samples.md). Internal business use only; confirm with legal before showing these files to a customer.

## Why this sample
A complete four-service credit-decision system with seed data: directly relevant to an auto lender, and runnable end to end once converted.

## What is inside
| Folder | Process files | What it does |
|---|---|---|
| ExperianDemoService | ExperianService.module/Processes/experianservice/module/Process.bwp | HTTP receive, parse JSON, JDBC query, render JSON, respond |
| CreditCheckBackendService | CreditCheckService/Processes/creditcheckservice/Process.bwp, LookupDatabase.bwp | REST service calls a subprocess: query by SSN; if a rating exists, increment the inquiry count and return; otherwise throw a fault |
| CreditAppService | CreditApp.module/Processes/creditapp/module/MainProcess.bwp, EquifaxScore.bwp, ExperianScore.bwp | /creditdetails calls both scoring services in parallel and merges the results |
| LoggingService | Processes/loggingservice/LogProcess.bwp | Render XML, write to file |
| Database | create.sql, DBDump.csv | Postgres table creditscore with 4 seeded applicants |

Note: CreditCheckBackendService/CreditCheckService.application/ contains packaged copies of the same two processes; parse the module copies.

## Suggested PoC order
1. ExperianDemoService (warm-up, one-to-one conversion)
2. CreditCheckService LookupDatabase (rule extraction, branches, fault path; known findings below)
3. CreditApp orchestrator (parallel calls, HTTP client resilience settings)
4. LoggingService (optional)

## Findings already identified in LookupDatabase.bwp
- R2: every successful lookup increments numofpulls (bureau inquiry count)
- F1: read then write without a transaction group (concurrent lookups can lose a count)
- F2: SSN matched with "like" on a raw column
- R4: End returns the count read before the increment
- CreditApp response schema defines TransUnionResponse, which no process populates

## Configuration to look at
- CreditApp.module/Resources/creditapp/module/*.httpClientResource: 1000 ms timeout, circuit breaker at 50% errors over 20 requests, thread pool 10
- */META-INF/*.substvar: environment properties (hosts, ports)
- Service Descriptors/*.json: Swagger contracts for the REST endpoints
