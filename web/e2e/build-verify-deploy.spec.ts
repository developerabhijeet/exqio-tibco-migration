import { expect, test, type Locator, type Page } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess } from "./helpers";

// The API runs with PROVIDER=fake. In this container NuGet and the Terraform registry are not
// reachable, so the build ends with a failed restore and G4 closed, and infra validation cannot
// run `terraform init`. Where the environment makes a step impossible, the test asserts the honest
// failure state the screens show instead of skipping it.

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

test("build, verify and deploy FundingStatusLookup as far as this machine allows", async ({ page }) => {
  test.setTimeout(480_000);
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, "FundingStatusLookup");
  await approveG1toG3(page);

  // ---------------------------------------------------------------- Build
  await actAs(page, "engineer.ravi");
  await page.getByTestId("step-build").click();
  await expect(page.getByTestId("build-design-hash")).toContainText(/[0-9a-f]{12}/);
  await expect(page.getByTestId("build-g3-state")).toContainText("approved");
  const start = page.getByRole("button", { name: "Generate and build" });
  await start.click();
  const log = page.getByTestId("build-log");
  await expect(log.getByText("code", { exact: true }).first()).toBeVisible();
  await expect(log.getByText("ai", { exact: true }).first()).toBeVisible();

  const steps = page.getByTestId("build-steps");
  const step = (name: string) => steps.locator(`[data-step="${name}"]`);
  await expect(step("metrics")).toHaveAttribute("data-status", "passed", { timeout: 180_000 });
  await expect(start).toBeEnabled();
  for (const ok of ["generate", "tests", "infra"]) await expect(step(ok)).toHaveAttribute("data-status", "passed");
  await expect(step("restore")).toHaveAttribute("data-status", "failed");
  await expect(step("build")).toHaveAttribute("data-status", "skipped");
  await expect(step("validate")).toHaveAttribute("data-status", "failed");
  await step("restore").getByRole("button").first().click();
  await expect(step("restore").getByTestId("step-log")).toContainText("NuGet unreachable");

  // Source and target side by side; the generated solution lists the Core project.
  const target = page.getByTestId("target-tree");
  await expect(target).toContainText("GmfCredit.Core");
  await target.getByRole("button", { name: "FundingStatusLookupService.cs" }).click();
  const viewer = page.getByTestId("code-viewer");
  await expect(viewer.getByTestId("code-path")).toHaveText("src/GmfCredit.Core/Services/FundingStatusLookupService.cs");
  await expect(viewer.getByTestId("tibco-ref")).toContainText("FundingStatusLookup.bwp");
  await expect(viewer.getByTestId("origin-pill")).toHaveText("code");
  await expect(page.getByTestId("source-tree")).toContainText(".bwp");

  // Tests are listed as files with their outcome; metrics radar has five axes.
  const tests = page.getByTestId("test-results");
  await expect(tests).toContainText("not run");
  await expect(tests.getByTestId("test-row").first()).toBeVisible();
  const radar = page.getByTestId("metrics-radar");
  await expect(radar.getByTestId("radar-axis")).toHaveCount(5);
  await radar.getByRole("button", { name: "Rule carry-forward" }).click();
  await expect(page.getByTestId("metric-drawer")).toContainText("approved rules with a passing test");
  await page.getByTestId("metric-drawer").getByRole("button", { name: "Close" }).click();
  await expect(radar.getByTestId("compile-pill")).toContainText("compile failed");

  // Infra: the generated live config and the validation rows with the registry reason.
  const infra = page.getByTestId("infra-panel");
  await expect(infra).toContainText("infra/live/dev");
  await expect(infra.locator('[data-check="init"][data-ok="false"]').first()).toContainText("registry");

  // G4 is closed by the server, with the reason.
  await expect(page.getByTestId("g4-blocked")).toContainText("G4 blocked: restore failed");
  const g4 = await gate(page, "G4");
  await expect(g4.getByRole("button", { name: "Approve G4" })).toBeDisabled();
  await expect(g4).toContainText("restore failed");

  // ---------------------------------------------------------------- Verify
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();
  await page.getByRole("button", { name: "Start service" }).click();
  const service = page.getByTestId("service-status");
  await expect(service).toHaveAttribute("data-state", /^(running|stopped)$/, { timeout: 120_000 });
  if ((await service.getAttribute("data-state")) === "running") {
    await expect(page.getByTestId("swagger-frame")).toHaveAttribute("src", /\/swagger$/);
  } else {
    await expect(page.getByTestId("swagger-placeholder")).toContainText("Service not running");
    await expect(page.getByTestId("service-reason")).not.toBeEmpty();
  }

  const scenarios = page.getByTestId("scenario-runner");
  await scenarios.getByRole("button", { name: "Generate scenarios" }).click();
  const happy = scenarios.getByTestId("scenario-row").filter({ hasText: "happy path" });
  await expect(happy).toBeVisible({ timeout: 60_000 });
  await expect(happy.getByTestId("kind-pill")).toHaveText("happy");
  await expect(scenarios).toContainText("dealId");

  const runVerify = scenarios.getByRole("button", { name: "Run verify" });
  if ((await service.getAttribute("data-state")) === "running") {
    await runVerify.click();
    const vlog = page.getByTestId("verify-log");
    await expect(vlog).toHaveAttribute("data-status", /^(done|failed)$/, { timeout: 180_000 });
    if ((await vlog.getAttribute("data-status")) === "done") {
      await expect(page.getByTestId("parity-table")).toContainText("Expected output derived from the TIBCO model");
    } else {
      await expect(vlog).toContainText("failed");
    }
  } else {
    await expect(runVerify).toBeDisabled();
  }

  const testData = page.getByTestId("test-data-panel");
  await expect(testData).toContainText("Production test data is a separate workstream");
  await expect(testData).toContainText("SSN keeps the last four digits");
  await expect(testData.getByTestId("test-data-table").first()).toContainText("funding");

  const g5 = await gate(page, "G5");
  await expect(g5.getByRole("button", { name: "Approve G5" })).toBeDisabled();

  // ---------------------------------------------------------------- Deploy
  await actAs(page, "ops.sam");
  await page.getByTestId("step-deploy").click();
  await expect(page.getByTestId("azure-form")).toContainText("Service principal");
  const plan = page.getByTestId("plan-view");
  await plan.getByRole("button", { name: "Run plan" }).click();
  await expect(plan).toHaveAttribute("data-status", "connect_later");
  await expect(plan).toContainText("Azure is not connected");
  await expect(plan.getByTestId("last-validation")).toContainText("registry");
  await page.getByLabel("Connect-later note").fill("Subscription access is pending with the platform team.");
  await page.getByRole("button", { name: "Connect later" }).click();
  await expect(plan).toHaveAttribute("data-status", "acknowledged");
  await expect(plan).toContainText("ops.sam");
  const apply = plan.getByRole("button", { name: "Apply" });
  await expect(apply).toBeDisabled();
  await expect(plan).toContainText("Apply is not part of this build");
  const g6 = await gate(page, "G6");
  await expect(g6.getByRole("button", { name: "Approve G6" })).toBeDisabled();
  await expect(g6).toContainText("G5");

  // ---------------------------------------------------------------- Settings and readiness
  await page.getByRole("link", { name: "Settings" }).click();
  const providers = page.getByTestId("providers");
  await expect(providers.locator('[data-provider="fake"]')).toContainText("configured");
  await expect(providers.locator('[data-provider="claude"]')).toContainText("missing");
  await expect(page.getByTestId("toolchains").locator('[data-tool="dotnet"]')).toContainText("present");
  await expect(page.getByTestId("toolchains").locator('[data-tool="az"]')).toContainText("missing");

  await page.getByRole("link", { name: "Readiness" }).click();
  const rows = page.getByTestId("readiness-row");
  await expect(rows).toHaveCount(13);
  await expect(rows.filter({ hasText: "Compilable .NET" })).toHaveAttribute("data-status", "partial");
  await expect(rows.filter({ hasText: "Synthetic estate" })).toHaveAttribute("data-status", "met");
  await expect(rows.first().getByTestId("evidence-link").first()).toBeVisible();

  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByTestId("history-timeline")).toContainText("build");
});
