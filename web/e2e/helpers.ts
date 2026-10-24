import { expect, type Page } from "@playwright/test";
import { SAMPLE_ZIP } from "./global-setup";

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
