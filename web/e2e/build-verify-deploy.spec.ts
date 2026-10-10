import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess } from "./helpers";

// The API runs with PROVIDER=fake. Whether `dotnet restore` can reach NuGet depends on the machine,
// so the flow probes the finished build once (GET /builds/{id}) and branches on the restore step:
// - restore failed (no NuGet, no offline cache): the honest failure states are asserted, from the
//   job card to the strips at the top of Verify and Deploy;
// - restore passed: the build passes, the service starts, Swagger answers and the dev token helper
//   returns a token, and verify runs to the end.
// Neither branch is skipped; the one that ran is recorded as a test annotation. The parity table is
// asserted against a committed fixture in a separate test with its own project, so it is covered on
// every machine even when the flow fails.

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const PARITY = readFileSync(join(__dirname, "fixtures", "parity.json"), "utf8");

/** The routed build the fixture test shows; no request for it reaches the API. */
const FIXTURE_BUILD = "fixture-build";

async function gate(page: Page, name: string): Promise<Locator> {
  const g = page.getByTestId(`gate-${name}`);
  await expect(g).toBeVisible();
  return g;
}

async function approveG1toG3(page: Page): Promise<void> {
  await page.getByTestId("step-validate").click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByTestId("analysis-log")).toContainText("citations verified", { timeout: 60_000 });
  const rules = page.getByTestId("rule-card");
  await expect(rules.first()).toBeVisible();
  const count = await rules.count();
  for (let i = 0; i < count; i++) {
    const card = rules.nth(i);
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(card).toHaveAttribute("data-status", "approved");
  }
  const g1 = await gate(page, "G1");
  await g1.getByRole("button", { name: "Approve G1" }).click();
  await expect(g1).toHaveAttribute("data-state", "approved");
  const findings = page.getByTestId("finding-card");
  const total = await findings.count();
  for (let i = 0; i < total; i++) {
    const card = findings.nth(i);
    await card.getByRole("button", { name: "Ask business" }).click();
    await expect(card).toHaveAttribute("data-decision", "ask");
  }
  const g2 = await gate(page, "G2");
  await g2.getByRole("button", { name: "Approve G2" }).click();
  await expect(g2).toHaveAttribute("data-state", "approved");

  await actAs(page, "architect.lee");
  await page.getByTestId("step-design").click();
  await page.getByRole("button", { name: "Generate design" }).click();
  await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/, { timeout: 60_000 });
  const g3 = await gate(page, "G3");
  await g3.getByRole("button", { name: "Approve G3" }).click();
  await expect(g3).toHaveAttribute("data-state", "approved");
}

test("FundingStatusLookup as far as this machine allows, branching on the restore probe", async ({ page }) => {
  test.setTimeout(480_000);
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, "FundingStatusLookup");
  await approveG1toG3(page);

  // -------------------------------------------------------------- Build
  await actAs(page, "engineer.ravi");
  await page.getByTestId("step-build").click();
  await expect(page.getByTestId("build-design-hash")).toContainText(/[0-9a-f]{12}/);
  await expect(page.getByTestId("build-g3-state")).toContainText("approved");
  const start = page.getByRole("button", { name: "Generate and build" });
  const posted = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/processes\/[^/]+\/build$/.test(new URL(r.url()).pathname),
  );
  await start.click();
  const startResponse = await posted;
  const { build_id: buildId } = (await startResponse.json()) as { build_id: string };

  const log = page.getByTestId("build-log");
  await expect(log.getByText("code", { exact: true }).first()).toBeVisible();
  await expect(log.getByText("ai", { exact: true }).first()).toBeVisible();
  const steps = page.getByTestId("build-steps");
  const step = (name: string) => steps.locator(`[data-step="${name}"]`);
  await expect(step("metrics")).toHaveAttribute("data-status", "passed", { timeout: 180_000 });
  await expect(start).toBeEnabled();

  // The probe: the finished build record decides the branch.
  let record: { status: string; finished_at: string | null; steps: { name: string; ok: boolean }[] } | null = null;
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`${API}/builds/${buildId}`, { headers: { "X-User": "engineer.ravi" } });
        record = res.ok() ? await res.json() : null;
        return record?.finished_at ?? null;
      },
      { timeout: 30_000 },
    )
    .not.toBeNull();
  const restoreOk = record!.steps.find((s) => s.name === "restore")?.ok === true;
  const branch = restoreOk ? "restore passed: success path" : "restore failed: honest failure path";
  test.info().annotations.push({ type: "branch", description: branch });
  // The list reporter does not print annotations; this line records the branch in the run log.
  console.log(`build-verify-deploy branch: ${branch}`);

  for (const ok of ["generate", "tests", "infra"]) await expect(step(ok)).toHaveAttribute("data-status", "passed");
  if (restoreOk) {
    await expect(step("restore")).toHaveAttribute("data-status", "passed");
    await expect(step("build")).toHaveAttribute("data-status", "passed");
    expect(record!.status).toBe("passed");
    await expect(page.getByTestId("build-status")).toContainText("passed");
    await expect(log.getByTestId("build-log-outcome")).toHaveCount(0);
  } else {
    await expect(step("restore")).toHaveAttribute("data-status", "failed");
    await expect(step("build")).toHaveAttribute("data-status", "skipped");
    await expect(step("validate")).toHaveAttribute("data-status", "failed");
    await expect(step("format")).toContainText("passed (reduced check)");
    await step("restore").getByRole("button").first().click();
    await expect(step("restore").getByTestId("step-log")).toContainText("NuGet unreachable");
    // The job ended, the build did not pass: the card says so and the chips follow the step rows.
    await expect(log.getByTestId("build-log-outcome")).toHaveText("job finished, build failed");
    await expect(log.locator('[data-stage="restore"]')).toHaveAttribute("data-tone", "failed");
    await expect(log.locator('[data-stage="build"]')).toHaveAttribute("data-tone", "skipped");
    await expect(log.locator('[data-stage="test"]')).toHaveAttribute("data-tone", "skipped");
  }

  // Source and target side by side: the TIBCO XML beside the C#, at the step it implements.
  const target = page.getByTestId("target-tree");
  await expect(target).toContainText("GmfCredit.Core");
  const explorer = page.getByTestId("source-target-explorer");
  const viewer = page.getByTestId("code-viewer");
  const source = explorer.getByTestId("tibco-source");
  const xml = source.getByTestId("tibco-xml");
  // The service class implements no single step: its process source opens without a highlight.
  await target.getByRole("button", { name: "FundingStatusLookupService.cs" }).click();
  await expect(viewer.getByTestId("code-path")).toHaveText("src/GmfCredit.Core/Services/FundingStatusLookupService.cs");
  await expect(viewer.getByTestId("origin-pill")).toHaveText("code");
  await expect(explorer.getByTestId("tibco-ref")).toContainText("FundingStatusLookup.bwp");
  await expect(source.getByTestId("tibco-source-path")).toContainText("FundingStatusLookup.bwp");
  await expect(xml).not.toHaveAttribute("data-hl-start", /./);
  // The repository implements QueryFundingStatus: its element is highlighted, landing on the SQL.
  await target.getByRole("button", { name: "FundingStatusLookupRepository.cs" }).click();
  await expect(source.getByTestId("tibco-source-span")).toContainText("QueryFundingStatus");
  await expect(xml).toHaveAttribute("data-hl-start", /^\d+$/);
  await expect(xml).toHaveAttribute("data-hl-focus-text", /select/i);
  // The controller implements three steps; a chip moves the highlight to that step's element.
  await target.getByRole("button", { name: "FundingStatusLookupController.cs" }).click();
  const chips = explorer.getByTestId("step-chip");
  await expect(chips).toHaveCount(3);
  const firstStart = await xml.getAttribute("data-hl-start");
  const last = chips.last();
  const name = ((await last.locator("span.font-medium").textContent()) ?? "").trim();
  await last.click();
  await expect(last).toHaveAttribute("aria-pressed", "true");
  // The chip and the pane name the same band and landing line.
  const chipLines = ((await last.getByTestId("step-chip-lines").textContent()) ?? "").trim();
  expect(chipLines).toMatch(/^line \d+ of \d+ to \d+$/);
  await expect(source.getByTestId("tibco-source-span")).toHaveText(`${name}: ${chipLines}`);
  await expect(xml).not.toHaveAttribute("data-hl-start", firstStart ?? "");
  // A file of no TIBCO step empties the source pane; the process file row opens it again beside its code.
  await target.getByRole("button", { name: "Program.cs" }).click();
  await expect(source.getByTestId("tibco-source-path")).toHaveCount(0);
  await page.getByTestId("source-tree").getByRole("button", { name: /^FundingStatusLookup\.bwp/ }).click();
  await expect(source.getByTestId("tibco-source-path")).toContainText("FundingStatusLookup.bwp");
  await expect(viewer.getByTestId("code-path")).toHaveText(
    "src/GmfCredit.Infrastructure/Repositories/FundingStatusLookupRepository.cs",
  );
  await expect(xml).toHaveAttribute("data-hl-start", /^\d+$/);
  // A TIBCO process of another application has no generated file here: the C# pane says so.
  await page.getByTestId("source-tree").getByRole("button", { name: /^LegacyAddressStandardize\.process/ }).click();
  await expect(source.getByTestId("tibco-source-path")).toContainText("LegacyAddressStandardize.process");
  await expect(page.getByTestId("code-viewer-empty")).toHaveText("No generated file implements this source in this build.");

  // Tests are listed with their outcome; the metrics radar has five axes.
  const tests = page.getByTestId("test-results");
  await expect(tests.getByTestId("test-row").first()).toBeVisible();
  if (!restoreOk) await expect(tests).toContainText("not run");
  const radar = page.getByTestId("metrics-radar");
  await expect(radar.getByTestId("radar-axis")).toHaveCount(5);
  await radar.getByRole("button", { name: "Rule carry-forward" }).click();
  await expect(page.getByTestId("metric-drawer")).toContainText("approved rules with a passing test");
  await page.getByTestId("metric-drawer").getByRole("button", { name: "Close" }).click();
  await expect(radar.getByTestId("compile-pill")).toContainText(restoreOk ? "compile passed" : "compile failed");

  // Infra: the generated live config is listed.
  const infra = page.getByTestId("infra-panel");
  await expect(infra).toContainText("infra/live/dev");
  if (!restoreOk) {
    // No registry here either: the init rows carry the reason.
    await expect(infra.locator('[data-check="init"][data-ok="false"]').first()).toContainText("registry");
    // G4 is closed by the server, with the reason and the remedy.
    await expect(page.getByTestId("g4-blocked")).toContainText("G4 blocked: restore failed");
    await expect(page.getByTestId("g4-remedy")).toContainText("make nuget-cache");
    const g4 = await gate(page, "G4");
    await expect(g4.getByRole("button", { name: "Approve G4" })).toBeDisabled();
    await expect(g4).toContainText("restore failed");
    // The stepper reads the failed build, not "not started".
    await expect(page.getByTestId("step-build")).toContainText("failed");
  }

  // -------------------------------------------------------------- Verify
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();
  const strip = page.getByTestId("failed-build-strip");
  if (restoreOk) {
    await expect(strip).toHaveCount(0);
  } else {
    await expect(strip).toContainText("Latest build failed");
    await expect(strip.getByTestId("failed-build-consequence")).toContainText("Verify cannot start the generated service");
    await expect(strip.getByTestId("failed-build-reason")).toContainText("NuGet unreachable");
    await expect(strip.getByTestId("failed-build-remedy")).toContainText("make nuget-cache");
  }
  const startService = page.getByRole("button", { name: "Start service" });
  const service = page.getByTestId("service-status");
  if (restoreOk) {
    await startService.click();
    await expect(service).toHaveAttribute("data-state", "running", { timeout: 120_000 });
    const frame = page.getByTestId("swagger-frame");
    await expect(frame).toHaveAttribute("src", /\/swagger$/);
    const swagger = await page.request.get((await frame.getAttribute("src"))!);
    expect(swagger.status()).toBe(200);
    await page.getByRole("button", { name: "Get dev token" }).click();
    await expect(page.getByTestId("dev-token-value")).toHaveText(/^[\w-]+\.[\w-]+\.[\w-]+$/);
  } else {
    // A failed build cannot be started: the button is disabled and says why.
    await expect(startService).toBeDisabled();
    await expect(startService).toHaveAttribute("title", "build failed; restore the build first");
    await expect(service).toHaveAttribute("data-state", "idle");
    await expect(page.getByTestId("swagger-placeholder")).toContainText("Service not running");
  }

  const scenarios = page.getByTestId("scenario-runner");
  await scenarios.getByRole("button", { name: "Generate scenarios" }).click();
  const happy = scenarios.getByTestId("scenario-row").filter({ hasText: "happy path" });
  await expect(happy).toBeVisible({ timeout: 60_000 });
  await expect(happy.getByTestId("kind-pill")).toHaveText("happy");
  await expect(scenarios).toContainText("dealId");

  const runVerify = scenarios.getByRole("button", { name: "Run verify" });
  if (restoreOk) {
    await runVerify.click();
    const vlog = page.getByTestId("verify-log");
    await expect(vlog).toHaveAttribute("data-status", "done", { timeout: 180_000 });
    const parity = page.getByTestId("parity-table");
    await expect(parity.getByTestId("parity-label")).toContainText("derived from the TIBCO model");
    await expect(parity.getByTestId("parity-overall")).toHaveText(/^\d+(\.\d)?%$/);
    await expect(parity.getByTestId("parity-row").first()).toBeVisible();
  } else {
    await expect(runVerify).toBeDisabled();
  }

  const testData = page.getByTestId("test-data-panel");
  await expect(testData).toContainText("Production test data is a separate workstream");
  await expect(testData).toContainText("SSN keeps the last four digits");
  await expect(testData.getByTestId("test-data-table").first()).toContainText("funding");

  const g5 = await gate(page, "G5");
  if (!restoreOk) await expect(g5.getByRole("button", { name: "Approve G5" })).toBeDisabled();

  // -------------------------------------------------------------- Deploy
  await actAs(page, "ops.sam");
  await page.getByTestId("step-deploy").click();
  if (restoreOk) {
    await expect(strip).toHaveCount(0);
  } else {
    await expect(strip.getByTestId("failed-build-consequence")).toContainText("Deploy cannot plan an image");
    await expect(strip.getByTestId("failed-build-remedy")).toContainText("make nuget-cache");
  }
  await expect(page.getByTestId("azure-form")).toContainText("Service principal");
  const plan = page.getByTestId("plan-view");
  await plan.getByRole("button", { name: "Run plan" }).click();
  await expect(plan).toHaveAttribute("data-status", "connect_later");
  await expect(plan).toContainText("Azure is not connected");
  if (!restoreOk) await expect(plan.getByTestId("last-validation")).toContainText("registry");
  await page.getByLabel("Connect-later note").fill("Subscription access is pending with the platform team.");
  await page.getByRole("button", { name: "Connect later" }).click();
  await expect(plan).toHaveAttribute("data-status", "acknowledged");
  await expect(plan).toContainText("ops.sam");
  // An acknowledged connect-later with G6 pending needs review.
  await expect(page.getByTestId("step-deploy")).toContainText("needs review");
  const apply = plan.getByRole("button", { name: "Apply" });
  await expect(apply).toBeDisabled();
  await expect(plan).toContainText("Apply is not part of this build");
  const g6 = await gate(page, "G6");
  await expect(g6.getByRole("button", { name: "Approve G6" })).toBeDisabled();
  await expect(g6).toContainText("G5");

  // -------------------------------------------------------------- Settings and readiness
  await page.getByRole("link", { name: "Settings" }).click();
  const providers = page.getByTestId("providers");
  await expect(providers.locator('[data-provider="fake"]')).toContainText("configured");
  await expect(providers.locator('[data-provider="claude"]')).toContainText("missing");
  await expect(page.getByTestId("toolchains").locator('[data-tool="dotnet"]')).toContainText("present");

  await page.getByRole("link", { name: "Readiness" }).click();
  const rows = page.getByTestId("readiness-row");
  await expect(rows).toHaveCount(13);
  await expect(rows.filter({ hasText: "Compilable .NET" })).toHaveAttribute("data-status", restoreOk ? /^(met|partial)$/ : "partial");
  await expect(rows.filter({ hasText: "Synthetic estate" })).toHaveAttribute("data-status", "met");
  await expect(rows.first().getByTestId("evidence-link").first()).toBeVisible();

  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("history-timeline")).toContainText("build");
});

test("the parity table shows a committed fixture: 70 %, one defect, one control-only defect, one unconfirmed intended, one noise", async ({ page }) => {
  test.setTimeout(180_000);
  // Its own project and process; the latest build and its parity are routed, so the flow test can
  // fail without taking this coverage with it.
  const now = "2026-10-10T15:00:00Z";
  const fulfill = (body: unknown) => (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: typeof body === "string" ? body : JSON.stringify(body),
    });
  await page.route(
    "**/processes/*/builds",
    fulfill([
      { id: FIXTURE_BUILD, status: "passed", runtime: "container_app", build_hash: "fixture", job_id: null, created_at: now, finished_at: now },
    ]),
  );
  await page.route(
    `**/builds/${FIXTURE_BUILD}`,
    fulfill({
      id: FIXTURE_BUILD,
      process_id: "fixture",
      job_id: null,
      status: "passed",
      runtime: "container_app",
      build_hash: "fixture",
      spec_hash: null,
      design_hash: null,
      created_at: now,
      finished_at: now,
      error: null,
      metrics: null,
      steps: ["restore", "build", "format", "test"].map((name) => ({ name, ok: true, log: `${name} passed` })),
      repair: null,
      builder: null,
      infra_validation: null,
    }),
  );
  await page.route(
    `**/builds/${FIXTURE_BUILD}/run`,
    fulfill({ running: false, url: null, port: null, pid: null, swagger_url: null, started_at: null, exit_code: null }),
  );
  await page.route(`**/builds/${FIXTURE_BUILD}/parity`, fulfill(PARITY));

  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, "FundingStatusLookup");
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();
  const table = page.getByTestId("parity-table");
  // The API's math: parity counts only `match` fields (intended and defects are not matched; noise is
  // not compared), 7 of 10 overall and 6 of 9 in the happy-path scenario.
  await expect(table.getByTestId("parity-overall")).toHaveText("70%");
  await expect(table).toContainText("7 of 10 fields");
  await expect(table.getByTestId("parity-label")).toHaveText("Expected output derived from the TIBCO model");
  const banner = table.getByTestId("unexplained-banner");
  await expect(banner).toHaveText("Unexplained differences: 2");
  await expect(banner).toHaveClass(/text-error/);
  await expect(table.getByTestId("unconfirmed-intended")).toHaveText(
    "1 intended difference(s) not confirmed: no TIBCO sandbox run; derived from the TIBCO model",
  );
  await expect(table.getByTestId("control-only-defects")).toHaveText(
    "1 of the unexplained differences are touched only through a branch decision by a Fix finding",
  );
  const scenarios = table.getByTestId("parity-scenario");
  await expect(scenarios).toHaveCount(2);
  await expect(scenarios.first()).toContainText("66.7%");
  await expect(scenarios.first()).toContainText("2 unexplained");
  const rows = table.getByTestId("parity-row");
  await expect(rows).toHaveCount(11);
  await expect(rows.and(page.locator('[data-status="match"]'))).toHaveCount(7);
  const defects = rows.and(page.locator('[data-status="defect"]'));
  await expect(defects).toHaveCount(2);
  const defect = defects.filter({ hasText: "output.body.status" });
  await expect(defect).toHaveCount(1);
  await expect(defect.getByTestId("finding-pill")).toHaveCount(0);
  const controlOnly = defects.filter({ hasText: "output.body.dealerRegion" });
  await expect(controlOnly.getByTestId("finding-pill")).toHaveText("S2");
  await expect(controlOnly.getByTestId("finding-pill")).toHaveAttribute(
    "title",
    "touched only through a branch decision by this finding; not accepted as intended without a TIBCO sandbox run",
  );
  const intended = rows.and(page.locator('[data-status="intended"]'));
  await expect(intended).toHaveCount(1);
  await expect(intended.getByTestId("unconfirmed-pill")).toHaveText("not confirmed");
  await expect(intended).toContainText("d10021");
  await expect(intended).toContainText("S2");
  await expect(rows.and(page.locator('[data-status="noise"]'))).toHaveCount(1);
});
