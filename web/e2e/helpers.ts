import { expect, type Locator, type Page } from "@playwright/test";
import { SAMPLE_ZIP } from "./global-setup";
import { apiUrl, webPort } from "../scripts/root-env";

/** The two origins a run may talk to: everything else is outbound traffic (global constraints). */
export const WEB_ORIGIN = `http://localhost:${webPort}`;
export const API_ORIGIN = new URL(apiUrl, WEB_ORIGIN).origin;

/** A process of the synthetic estate (Dealer Funding domain). */
export const SAMPLE_PROCESS_NAME = "DealerFundingRequest";

/** The process the build flow generates code for, and one of its activities (a JDBC query) that
 * the build maps to a generated method (MIGRATED_TO). */
export const SAMPLE_BUILD_PROCESS_NAME = "FundingStatusLookup";
export const SAMPLE_ACTIVITY_NAME = "QueryFundingStatus";

/** Act as one of the five users through the top bar menu (sent as X-User). */
export async function actAs(page: Page, user: string): Promise<void> {
  const menu = page.getByTestId("user-menu");
  if (((await menu.textContent()) ?? "").includes(user)) return;
  await menu.click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${user.replace(".", "\\.")}`) }).click();
  await expect(menu).toContainText(user);
}

/** A fresh project with the synthetic estate uploaded and scanned (the Task 20 flow). */
export async function newProjectAndScan(page: Page): Promise<void> {
  const name = `e2e ${Date.now()}`;
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  // Wait for the new project to be the open one. Uploading before that sends the estate to whichever
  // project was open before, and the scan then runs against a project with no upload: a 404 that
  // reads as a broken scan. With an empty database there is no earlier project to upload to, so this
  // only ever bites on an estate that already has one.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
  await page.getByTestId("zip-input").setInputFiles(SAMPLE_ZIP);
  await expect(page.getByTestId("file-tree")).toContainText(".bwp");
  await page.getByRole("button", { name: "Scan" }).click();
  await expect(page.getByTestId("scan-progress")).toContainText("stored 2 applications", { timeout: 60_000 });
  await expect(page.getByTestId("counter-processes")).toHaveText("11", { timeout: 60_000 });
}

/** Open a process of the Dealer Funding domain in Discover. */
export async function openDealerProcess(page: Page, name: string): Promise<void> {
  await page.getByTestId("step-discover").click();
  await page.getByTestId("domain-select").click();
  await page.getByRole("option", { name: /Dealer Funding/ }).click();
  await page.getByTestId("process-list").getByRole("button", { name: new RegExp(name) }).click();
  await expect(page.getByTestId("process-view")).toContainText(name);
}

/** From the start page: a fresh project with the synthetic estate uploaded and scanned. */
export async function uploadSample(page: Page): Promise<void> {
  await page.goto("/");
  await newProjectAndScan(page);
}

/** uploadSample, then the analysis of the sample process (Validate), which memory records. */
export async function uploadAndAnalyse(page: Page): Promise<void> {
  await uploadSample(page);
  await openDealerProcess(page, SAMPLE_PROCESS_NAME);
  await page.getByTestId("step-validate").click();
  const run = page.getByRole("button", { name: "Run analysis" });
  await run.click();
  await expect(page.getByTestId("analysis-log")).toContainText("citations verified", { timeout: 90_000 });
  await expect(run).toBeEnabled();
}

/** The memory explorer with the node named `name` found by search, focused and open in the drawer. */
export async function openNode(page: Page, name: string): Promise<void> {
  await page.goto("/memory");
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/, { timeout: 60_000 });
  await page.getByTestId("memory-search").fill(name);
  await page.getByTestId("memory-results").getByText(name).first().click();
  await expect(page.getByTestId("node-drawer")).toBeVisible();
}

/** A gate card, once visible. */
export async function gate(page: Page, name: string): Promise<Locator> {
  const g = page.getByTestId(`gate-${name}`);
  await expect(g).toBeVisible();
  return g;
}

/** In an open process: run the analysis, approve every rule and G1, ask the business on every
 * finding and approve G2, then (as architect.lee) generate the design and approve G3. */
export async function approveG1toG3(page: Page): Promise<void> {
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

/** A fresh project scanned, SAMPLE_BUILD_PROCESS_NAME taken through G1 to G3 and its code
 * generated and built (as engineer.ravi). Whether restore reaches NuGet does not matter here:
 * the generated code (and its MIGRATED_TO links) is recorded either way. */
export async function throughBuild(page: Page): Promise<void> {
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, SAMPLE_BUILD_PROCESS_NAME);
  await approveG1toG3(page);
  await actAs(page, "engineer.ravi");
  await page.getByTestId("step-build").click();
  await expect(page.getByTestId("build-g3-state")).toContainText("approved");
  const start = page.getByRole("button", { name: "Generate and build" });
  await start.click();
  await expect(page.getByTestId("build-steps").locator('[data-step="metrics"]')).toHaveAttribute("data-status", "passed", {
    timeout: 180_000,
  });
  await expect(start).toBeEnabled();
}
