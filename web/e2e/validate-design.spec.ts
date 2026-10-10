import { expect, test, type Locator, type Page } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess } from "./helpers";

// The API runs with PROVIDER=fake (playwright.config.ts), so the analyst, risk reviewer and
// challenger answers are scripted and the flow is deterministic.

async function gate(page: Page, name: string): Promise<Locator> {
  const g = page.getByTestId(`gate-${name}`);
  await expect(g).toBeVisible();
  return g;
}

test("validate rules and findings, approve G1 to G3, then an edit reopens G1", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, "DealerFundingRequest");

  // Validate: run the analysis and watch the job log.
  await page.getByTestId("step-validate").click();
  const run = page.getByRole("button", { name: "Run analysis" });
  await run.click();
  const log = page.getByTestId("analysis-log");
  await expect(log).toContainText("citations verified");
  await expect(log.getByText("ai", { exact: true }).first()).toBeVisible();
  await expect(log.getByText("code", { exact: true }).first()).toBeVisible();
  await expect(run).toBeEnabled();

  // Both banners green.
  const citations = page.getByTestId("banner-citations");
  await expect(citations).toHaveAttribute("data-state", "ok");
  await expect(citations).toContainText(/Citations verified (\d+) of \1/);
  const coverage = page.getByTestId("banner-coverage");
  await expect(coverage).toHaveAttribute("data-state", "ok");
  await expect(coverage).toContainText(/Coverage (\d+) of \1 steps, branches and mappings/);

  // G1 is closed until every rule is decided; the server says why.
  const g1 = await gate(page, "G1");
  await expect(g1.getByRole("button", { name: "Approve G1" })).toBeDisabled();
  await expect(g1).toContainText("undecided");

  // A citation chip jumps to the canvas step and its XML lines, and back.
  const firstRule = page.getByTestId("rule-card").first();
  const chip = firstRule.getByTestId("citation-chip").first();
  const cited = await chip.getAttribute("data-activity");
  expect(cited).toBeTruthy();
  await chip.click();
  await expect(page.getByTestId("process-view")).toBeVisible();
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(1);
  await expect(page.locator(".react-flow__node.selected")).toHaveAttribute("data-id", cited!);
  await expect(page.getByTestId("xml-viewer").locator(".xml-hl-line").first()).toBeVisible();
  await page.getByRole("button", { name: "Back to Validate" }).click();

  // Approve every rule, then G1 as sme.vicky.
  const rules = page.getByTestId("rule-card");
  const count = await rules.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) {
    const card = rules.nth(i);
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(card).toHaveAttribute("data-status", "approved");
  }
  await g1.getByRole("button", { name: "Approve G1" }).click();
  await expect(g1).toHaveAttribute("data-state", "approved");
  await expect(g1).toContainText("sme.vicky");

  // The owner may reject an approved gate; the reason is required and shown; then approve again.
  await g1.getByRole("button", { name: "Reject G1" }).click();
  await expect(g1.getByRole("button", { name: "Confirm rejection" })).toBeDisabled();
  await g1.getByLabel(/Reason for rejecting G1/).fill("R2 boundary needs a finance check first.");
  await g1.getByRole("button", { name: "Confirm rejection" }).click();
  await expect(g1).toHaveAttribute("data-state", "rejected");
  await expect(g1.getByTestId("gate-G1-rejection")).toContainText("R2 boundary needs a finance check first.");
  await g1.getByRole("button", { name: "Approve G1" }).click();
  await expect(g1).toHaveAttribute("data-state", "approved");

  // Findings: fix the read-then-write, keep the LIKE match with a note, ask about the rest.
  const findings = page.getByTestId("finding-card");
  const s1 = findings.filter({ hasText: "Read-then-write" });
  await s1.getByRole("button", { name: "Fix" }).click();
  await expect(s1).toHaveAttribute("data-decision", "fix");
  const s2 = findings.filter({ hasText: "LIKE" });
  await s2.getByLabel("Note").fill("Dealer ids are exact today; keep the TIBCO match until the data is cleaned.");
  await s2.getByRole("button", { name: "Keep behavior" }).click();
  await expect(s2).toHaveAttribute("data-decision", "keep");
  const total = await findings.count();
  for (let i = 0; i < total; i++) {
    const card = findings.nth(i);
    if ((await card.getAttribute("data-decision")) === "") {
      await card.getByRole("button", { name: "Ask business" }).click();
      await expect(card).toHaveAttribute("data-decision", "ask");
    }
  }
  const g2 = await gate(page, "G2");
  await g2.getByRole("button", { name: "Approve G2" }).click();
  await expect(g2).toHaveAttribute("data-state", "approved");

  // Ask AI panel answers with numbered citations.
  await page.getByRole("button", { name: "Ask AI" }).click();
  await page.getByRole("button", { name: "Which workflows read the funding table?" }).click();
  await expect(page.getByTestId("ask-answer")).toBeVisible();
  await expect(page.getByTestId("ask-citation").first()).toContainText("[1]");
  // A citation that names a step opens it on the canvas.
  const askCite = page.locator('[data-testid="ask-citation"]:not([data-step=""])').first();
  const askStep = await askCite.getAttribute("data-step");
  await askCite.click();
  await expect(page.locator(".react-flow__node.selected")).toHaveAttribute("data-id", askStep!);
  await page.getByRole("button", { name: "Close Ask AI" }).click();
  await page.getByRole("button", { name: "Back to Validate" }).click();

  // Design: generate the record as the architect and approve G3.
  await actAs(page, "architect.lee");
  await page.getByTestId("step-design").click();
  await page.getByRole("button", { name: "Generate design" }).click();
  const row = page.locator('[data-testid="mapping-row"][data-activity="LookupDealer"]').filter({
    hasText: "DealerFundingRequestRepository",
  });
  await expect(row).toHaveCount(1, { timeout: 60_000 });
  await expect(row).toContainText("LookupDealerAsync");
  await expect(row.getByTestId("finding-badge")).toContainText("S2");
  await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/);
  // The e2e API runs the fake provider: the narrative says its prose is scripted.
  await expect(page.getByTestId("design-narrative")).toContainText("Designer narrative, fake provider (scripted demo text, no model)");
  await expect(page.getByTestId("sequence-diagram").locator("svg")).toBeVisible();
  await expect(page.getByTestId("sequence-alt").first()).toBeVisible();
  const g3 = await gate(page, "G3");
  await g3.getByRole("button", { name: "Approve G3" }).click();
  await expect(g3).toHaveAttribute("data-state", "approved");
  await expect(g3).toContainText("architect.lee");
  await expect(g3.getByTestId("gate-G3-design-hash")).toContainText(/design [0-9a-f]{12}/);

  // Export PDF: in print the sequence scales to the page instead of being clipped.
  await page.setViewportSize({ width: 720, height: 1000 });
  await page.emulateMedia({ media: "print" });
  const fit = await page.getByTestId("sequence-diagram").evaluate((el) => {
    const box = el.querySelector(".sequence-scroll") as HTMLElement;
    const svg = el.querySelector("svg") as SVGSVGElement;
    return { scroll: box.scrollWidth, client: box.clientWidth, svg: svg.getBoundingClientRect().width };
  });
  expect(fit.scroll).toBeLessThanOrEqual(fit.client + 1);
  expect(fit.svg).toBeLessThanOrEqual(fit.client + 1);
  const pdf = (await page.pdf({ format: "A4", printBackground: true })).toString("latin1");
  expect((pdf.match(/\/Type\s*\/Page\b/g) ?? []).length).toBeGreaterThan(0);
  expect(pdf).toMatch(/\/MediaBox\s*\[\s*0 0 59[56]\.\d+ 84[23]\.\d+\s*\]/); // A4 portrait
  await page.emulateMedia({ media: "screen" });
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Editing an approved rule reopens G1 and is on the audit trail.
  await actAs(page, "sme.vicky");
  await page.getByTestId("step-validate").click();
  const edited = page.getByTestId("rule-card").first();
  await edited.getByRole("button", { name: "Edit" }).click();
  await edited.getByRole("textbox").fill(
    "A funding request proceeds only when the dealer's status is ACTIVE; any other status raises DealerInactiveFault, nothing is funded and the request is answered with the fault.",
  );
  await edited.getByRole("button", { name: "Save" }).click();
  await expect(edited).toHaveAttribute("data-status", "edited");
  await expect(g1).toHaveAttribute("data-state", "needs_review");
  await expect(g1).toContainText("needs review");
  await expect(await gate(page, "G2")).toHaveAttribute("data-state", "needs_review");
  await page.getByRole("button", { name: "Audit trail" }).click();
  const drawer = page.getByTestId("audit-drawer");
  await expect(drawer).toContainText("rule edited");
  await expect(drawer).toContainText("gate reset");
  await expect(drawer).toContainText("gate rejected");
  await page.getByRole("button", { name: "Close", exact: true }).click();

  // The design gate reopened too.
  await page.getByTestId("step-design").click();
  await expect(await gate(page, "G3")).toHaveAttribute("data-state", "needs_review");
});
