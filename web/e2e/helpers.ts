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
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`e2e ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
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
