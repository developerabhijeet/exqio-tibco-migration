import { expect, test } from "@playwright/test";
import {
  API_ORIGIN,
  SAMPLE_ACTIVITY_NAME,
  SAMPLE_PROCESS_NAME,
  WEB_ORIGIN,
  actAs,
  openNode,
  throughBuild,
  uploadAndAnalyse,
  uploadSample,
} from "./helpers";

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
  // Exercise everything the explorer loads: search, the cytoscape canvas, the drawer and the
  // elkjs lineage layout (each a lazily loaded chunk).
  await uploadSample(page);
  await openNode(page, SAMPLE_PROCESS_NAME);
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  await page.getByRole("radio", { name: "Lineage" }).click();
  // Laid out as a lineage scene: the elkjs chunk has loaded and placed it.
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-laid-out", "lineage");
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  await page.waitForLoadState("networkidle");
  expect(outside).toEqual([]);
});

test("Clear resets every filter in one go", async ({ page }) => {
  await uploadSample(page);
  await page.goto("/memory?labels=Process,Activity&side=old&phase=discover");
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/, { timeout: 60_000 });
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page).not.toHaveURL(/labels=|side=|phase=/);
  await expect(page.getByRole("radio", { name: "All" })).toHaveAttribute("aria-checked", "true");
});

test("the drawer keeps its tab, closes on Escape and gives focus back", async ({ page }) => {
  await uploadSample(page);
  await openNode(page, SAMPLE_PROCESS_NAME);
  await expect(page.getByTestId("node-drawer")).toBeFocused();
  await page.getByRole("tab", { name: "History" }).click();
  // Another result (one of the process's activities): the drawer follows, on the same tab.
  await page.getByTestId("memory-results").getByRole("button").nth(1).click();
  await expect(page).toHaveURL(/uid=activity%3A/);
  await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("node-drawer")).toHaveCount(0);
  await expect(page.getByTestId("memory-results").getByRole("button").first()).toBeFocused();
});

test("timeline as-of and diff highlight", async ({ page }) => {
  test.setTimeout(240_000);
  await uploadAndAnalyse(page);
  await page.goto("/memory");
  // The analysis lands as revision 2 (or later): wait for the timeline to hold it.
  await expect(page.getByTestId("rev-2")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  // Revision 1 is the seed (memory items only): the graph is re-read as of it, and the domains
  // and applications the scan added later are not there.
  const asOf1 = page.waitForResponse((r) => /\/graph\?/.test(r.url()) && r.url().includes("as_of=1") && r.ok());
  await page.getByTestId("rev-1").click();
  await asOf1;
  await expect(page.getByText(/Viewing revision 1 of \d+/)).toBeVisible();
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", "0");
  await page.getByTestId("rev-2").click();
  await expect(page.getByText(/Viewing revision 2 of \d+/)).toBeVisible();
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  await page.getByRole("button", { name: "Show changes" }).click();
  await expect(page.getByTestId("memory-diff")).toContainText("Added");
  await page.getByRole("button", { name: "Back to current" }).click();
  await expect(page.getByText(/Viewing revision/)).toHaveCount(0);
});

test("lineage after a build highlights MIGRATED_TO", async ({ page }) => {
  test.setTimeout(480_000);
  await throughBuild(page);
  await openNode(page, SAMPLE_ACTIVITY_NAME);
  await page.getByRole("tab", { name: "Lineage" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText("MIGRATED_TO", { timeout: 60_000 });
  await expect(page.getByTestId("node-drawer")).toContainText("method:dotnet:");

  // Reject the mapping (engineer.ravi curates): once applied, the lineage edge reads rejected.
  await page.getByRole("tab", { name: "Curate" }).click();
  await page.getByRole("button", { name: /^Reject mapping The generated code/ }).click();
  await page.getByLabel("Reason").fill("the generated method does not query the funding status");
  await page.getByRole("button", { name: "Reject mapping", exact: true }).click();
  await expect(page.getByTestId("curate-state")).toHaveText(/Applied in revision \d+/, { timeout: 60_000 });
  await page.getByRole("tab", { name: "Lineage" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText("rejected", { timeout: 30_000 });
});

test("an SME note goes pending then applied", async ({ page }) => {
  await uploadSample(page);
  await actAs(page, "sme.vicky");
  await openNode(page, SAMPLE_PROCESS_NAME);
  await page.getByRole("tab", { name: "Curate" }).click();
  await page.getByLabel("Note").fill("Owned by the payoff team");
  // The reason is required: nothing is sent without one.
  await page.getByRole("button", { name: "Pin note" }).click();
  await expect(page.getByText("A reason is required.")).toBeVisible();
  await expect(page.getByTestId("curate-state")).toHaveCount(0);
  await page.getByLabel("Reason").fill("context for agents");
  await page.getByRole("button", { name: "Pin note" }).click();
  await expect(page.getByTestId("curate-state")).toHaveText(/Pending|Applied in revision \d+/);
  await expect(page.getByTestId("curate-state")).toHaveText(/Applied in revision \d+/, { timeout: 60_000 });
  // It is in the memory: the node re-reads as curated and its Knowledge holds the note.
  await expect(page.getByTestId("node-drawer").getByText("curated", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "See it under Knowledge" }).click();
  await expect(page.getByRole("tab", { name: "Knowledge" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("node-drawer")).toContainText("Owned by the payoff team");
});

test("the diff lists a changed item with its changed keys", async ({ page }) => {
  // No sample flow changes a node in place, so the diff of the latest revision is stubbed with one.
  await uploadSample(page);
  await page.goto("/memory");
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/, { timeout: 60_000 });
  await page.route("**/memory/*/revisions/*/diff", async (r) => {
    const real = await (await r.fetch()).json();
    const changed = { uid: "process:x/Stubbed", kind: "Process", name: "StubbedProcess", content_hash: "h", props: {}, changed_keys: ["status", "text"] };
    await r.fulfill({ json: { ...real, nodes: { ...real.nodes, changed: [changed] } } });
  });
  await page.getByRole("button", { name: "Show changes" }).click();
  const diff = page.getByTestId("memory-diff");
  await expect(diff).toContainText("Changed");
  await expect(diff).toContainText("StubbedProcess");
  await expect(diff).toContainText("status, text");
});

test("a viewer sees no Curate tab", async ({ page }) => {
  await uploadSample(page);
  await actAs(page, "viewer.val");
  await openNode(page, SAMPLE_PROCESS_NAME);
  await expect(page.getByRole("tab", { name: "Curate" })).toHaveCount(0);
});

test("unavailable state when the API reports Neo4j down", async ({ page }) => {
  await page.route("**/memory/*/status", (r) =>
    r.fulfill({ json: { configured: true, available: false, revision: null, counts: {}, pending_ingests: 0 } }),
  );
  await page.goto("/memory");
  await expect(page.getByTestId("memory-unavailable")).toContainText("Memory is unavailable");
  await expect(page.getByTestId("memory-canvas")).toHaveCount(0);
});
