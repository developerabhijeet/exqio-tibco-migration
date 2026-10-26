import { expect, test, type Page, type Response } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess, SAMPLE_PROCESS_NAME } from "./helpers";
import type { CitedText } from "../lib/citations-model";

// Inline citations (SP4 Task 9): the Discover domain rationale and the Validate rule and finding
// texts carry their chips inline; the separate evidence row and "Cites" row are gone. The API runs
// with PROVIDER=fake (playwright.config.ts), so the analysis is scripted and deterministic.

interface DomainJson {
  id: string;
  name: string;
  evidence: { kind: string; text: string }[];
  cited?: CitedText | null;
}

interface AnalysisJson {
  rules: { cited?: CitedText | null }[];
  findings: { cited?: CitedText | null }[];
}

const isGet = (r: Response, path: RegExp) => r.request().method() === "GET" && r.ok() && path.test(new URL(r.url()).pathname);

/** Scan the sample as sme.vicky, open DealerFundingRequest and run its analysis in Validate. */
async function analyse(page: Page): Promise<void> {
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, SAMPLE_PROCESS_NAME);
  await page.getByTestId("step-validate").click();
  const run = page.getByRole("button", { name: "Run analysis" });
  await run.click();
  await expect(page.getByTestId("analysis-log")).toContainText("citations verified", { timeout: 90_000 });
  await expect(run).toBeEnabled();
  await expect(page.getByTestId("rule-card").first()).toBeVisible();
}

test("Validate shows citations inside the rule text and they open the canvas", async ({ page }) => {
  test.setTimeout(240_000);
  await analyse(page);

  const rule = page.getByTestId("rule-card").first();
  await expect(rule.getByText("Cites", { exact: true })).toHaveCount(0);
  // The chips sit inside the rule's text paragraph, after the sentence they vouch for.
  const text = rule.getByTestId("rule-text");
  await expect(text.getByTestId("cited-segment").first()).toBeVisible();
  const chip = text.getByTestId("cite-chip").first();
  await expect(chip).toHaveAttribute("data-phase", "validate");
  await expect(chip).toHaveAttribute("data-target-kind", "activity");
  await expect(chip).toHaveText(/^Validate \d+$/);
  const step = await chip.getAttribute("data-step");
  expect(step).toBeTruthy();

  // Findings too: chips in the text, no separate evidence row.
  const finding = page.getByTestId("finding-card").first();
  await expect(finding.getByText("Evidence", { exact: true })).toHaveCount(0);
  await expect(finding.getByTestId("finding-text").getByTestId("cite-chip").first()).toHaveAttribute("data-phase", "validate");

  await chip.click();
  await expect(page.getByTestId("process-view")).toBeVisible();
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(1);
  await expect(page.locator(".react-flow__node.selected")).toHaveAttribute("data-id", step!);
  await expect(page.getByRole("button", { name: "Back to Validate" })).toBeVisible();
});

test("the preview shows the memory revision and the not-verified reason", async ({ page }) => {
  test.setTimeout(240_000);
  const reason = "LookupGhost is not in the parsed model";
  let badId = "";
  // Every fake citation verifies, so the first rule's first ref is marked unverified in the
  // analysis payload the page reads: the preview must surface the server's reason as given.
  await page.route(/\/processes\/[^/]+\/analysis$/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const body = (await res.json()) as AnalysisJson;
    const ref = body.rules?.[0]?.cited?.refs?.[0];
    if (ref) {
      Object.assign(ref, { verified: false, reason });
      badId = ref.id;
    }
    await route.fulfill({ response: res, json: body });
  });
  await analyse(page);

  const text = page.getByTestId("rule-card").first().getByTestId("rule-text");
  const preview = page.getByTestId("cite-preview");

  // Keyboard: ArrowDown on the chip opens the card at once; Escape closes it with focus kept on
  // the chip; tabbing onto the chip opens it again (keyboard focus, after its short delay).
  expect(badId).toBeTruthy();
  const bad = text.locator(`[data-testid="cite-chip"][data-ref="${badId}"]`);
  await expect(bad).toHaveAttribute("data-verified", "false");
  await bad.focus();
  await page.keyboard.press("ArrowDown");
  await expect(preview).toBeVisible();
  await expect(preview.getByTestId("cite-unverified")).toHaveText(`Not verified: ${reason}`);
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  await expect(bad).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(bad).toBeFocused();
  await expect(preview).toHaveCount(1);
  await expect(preview.getByTestId("cite-unverified")).toHaveText(`Not verified: ${reason}`);
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);

  // Hover: a verified chip opens after the delay; its memory node is read lazily for the revision.
  const good = page.locator('[data-testid="cite-chip"][data-verified="true"]').first();
  await good.hover();
  await expect(preview).toBeVisible();
  await expect(preview.getByTestId("cite-unverified")).toHaveCount(0);
  await expect(preview.getByTestId("cite-open-memory")).toBeVisible();
  const revision = preview.getByTestId("cite-revision");
  const missing = preview.getByTestId("cite-memory-state").filter({ hasText: "Not in memory yet" });
  await expect(revision.or(missing)).toBeVisible();
  if (await revision.isVisible()) await expect(revision).toHaveText(/^r\d+$/);
  await page.mouse.move(0, 0);
  await expect(preview).toHaveCount(0);
});

test("Discover shows the domain rationale with a chip per evidence item", async ({ page }) => {
  test.setTimeout(180_000);
  let domains: DomainJson[] = [];
  page.on("response", async (r) => {
    if (isGet(r, /\/projects\/[^/]+\/domains$/)) domains = (await r.json().catch(() => domains)) as DomainJson[];
  });
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await page.getByTestId("step-discover").click();
  await page.getByTestId("domain-select").click();
  await page.getByRole("option", { name: /Dealer Funding/ }).click();

  await expect(page.locator('[aria-label="Domain evidence"]')).toHaveCount(0);
  const rationale = page.getByTestId("domain-rationale");
  await expect(rationale).toBeVisible();
  await expect.poll(() => domains.find((d) => /Dealer Funding/.test(d.name))?.evidence.length ?? 0).toBeGreaterThan(0);
  const domain = domains.find((d) => /Dealer Funding/.test(d.name))!;
  expect(domain.cited?.refs.length).toBe(domain.evidence.length);

  const chips = rationale.getByTestId("cite-chip");
  await expect(chips).toHaveCount(domain.evidence.length);
  await expect(chips.first()).toHaveAttribute("data-phase", "discover");
  await expect(chips.first()).toHaveText(/^Discover 1$/);
  // The rationale is the server's cited text (its sentences, not the raw evidence list).
  const firstSegment = (await rationale.getByTestId("cited-segment").first().textContent()) ?? "";
  expect(firstSegment.length).toBeGreaterThan(0);
  expect(domain.cited!.text).toContain(firstSegment);

  // The chip's preview names the evidence item it stands for.
  await chips.first().hover();
  const preview = page.getByTestId("cite-preview");
  await expect(preview).toBeVisible();
  await expect(preview).toContainText(domain.cited!.refs[0].preview);
});
