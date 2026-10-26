import { expect, type Page } from "@playwright/test";
import { SAMPLE_ZIP } from "./global-setup";
import { apiUrl, webPort } from "../scripts/root-env";

/** The two origins a run may talk to: everything else is outbound traffic (global constraints). */
export const WEB_ORIGIN = `http://localhost:${webPort}`;
export const API_ORIGIN = new URL(apiUrl, WEB_ORIGIN).origin;

/** A process of the synthetic estate (Dealer Funding domain). */
export const SAMPLE_PROCESS_NAME = "DealerFundingRequest";

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
