import { expect, test } from "@playwright/test";
import { API_ORIGIN, SAMPLE_PROCESS_NAME, WEB_ORIGIN, openNode, uploadAndAnalyse, uploadSample } from "./helpers";

// The memory explorer (/memory). The API runs with NEO4J_URI set (playwright.config.ts), so the
// scan and the analysis are ingested into the memory graph by the worker; `make neo4j` first.

test("the Memory link opens the explorer for the selected project", async ({ page }) => {
  await uploadSample(page);
  await page.getByRole("link", { name: "Memory" }).click();
  await expect(page).toHaveURL(/\/memory/);
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/, { timeout: 60_000 });
});

test("search finds a process and the canvas focuses it", async ({ page }) => {
  await uploadSample(page);
  await page.goto("/memory");
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/, { timeout: 60_000 });
  await page.getByTestId("memory-search").fill(SAMPLE_PROCESS_NAME);
  await page.getByTestId("memory-results").getByText(SAMPLE_PROCESS_NAME).first().click();
  await expect(page).toHaveURL(/uid=process%3A/);
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  await expect(page.getByTestId("node-drawer")).toContainText("process:");
});

test("drawer shows history and knowledge tabs", async ({ page }) => {
  test.setTimeout(240_000);
  await uploadAndAnalyse(page);
  await openNode(page, SAMPLE_PROCESS_NAME);
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText(/discover/);
  await page.getByRole("tab", { name: "Knowledge" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText(/Summary of|Rule/, { timeout: 60_000 });
});

test("no request leaves the web or API origin on the explorer", async ({ page }) => {
  const outside: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.protocol.startsWith("http") && ![WEB_ORIGIN, API_ORIGIN].includes(u.origin)) outside.push(r.url());
  });
  await page.goto("/memory");
  await page.waitForLoadState("networkidle");
  expect(outside).toEqual([]);
});
