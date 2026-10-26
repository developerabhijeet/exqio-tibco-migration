import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page, type Response, type Route } from "@playwright/test";
import { actAs, gotoStep, newProjectAndScan, openDealerProcess, SAMPLE_PROCESS_NAME } from "./helpers";
import { apiUrl } from "../scripts/root-env";
import type { CitedText } from "../lib/citations-model";

// Inline citations (SP4 Tasks 9 and 10): every phase's claims carry their chips inline; the
// separate evidence rows, "Cites" rows, file:line labels, finding links and evidence blocks are
// gone. The API runs with PROVIDER=fake (playwright.config.ts), so the analysis and the design are
// scripted and deterministic. Build, Verify and Deploy read a routed build (no dotnet needed): its
// reviews and parity are fixtures made by the API's own citation builders (cite_review,
// cite_parity), so their `cited` has the shape the API serves.

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

  await expect(page.getByLabel("Domain evidence", { exact: true })).toHaveCount(0);
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

interface ReadinessJson {
  rows: { key: string; evidence: unknown[]; cited?: CitedText | null }[];
}

/** G1 to G3 prerequisites: analyse, approve every rule and G1, ask business on every finding and
 * approve G2, then generate the design as the architect. */
async function design(page: Page): Promise<void> {
  await analyse(page);
  const rules = page.getByTestId("rule-card");
  for (let i = 0; i < (await rules.count()); i++) {
    await rules.nth(i).getByRole("button", { name: "Approve", exact: true }).click();
    await expect(rules.nth(i)).toHaveAttribute("data-status", "approved");
  }
  await page.getByTestId("gate-G1").getByRole("button", { name: "Approve G1" }).click();
  await expect(page.getByTestId("gate-G1")).toHaveAttribute("data-state", "approved");
  const findings = page.getByTestId("finding-card");
  for (let i = 0; i < (await findings.count()); i++) {
    await findings.nth(i).getByRole("button", { name: "Ask business" }).click();
    await expect(findings.nth(i)).toHaveAttribute("data-decision", "ask");
  }
  await page.getByTestId("gate-G2").getByRole("button", { name: "Approve G2" }).click();
  await expect(page.getByTestId("gate-G2")).toHaveAttribute("data-state", "approved");
  await actAs(page, "architect.lee");
  await page.getByTestId("step-design").click();
  await page.getByRole("button", { name: "Generate design" }).click();
  await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/, { timeout: 60_000 });
}

const FIXTURE_BUILD = "fixture-build";
const PROGRAM = "src/GmfCredit.Api/Program.cs";
const MASKING = "src/GmfCredit.Core/Masking.cs";
const SERVICE = "src/GmfCredit.Core/Services/FundingStatusLookupService.cs";
const PROGRAM_CS = [
  "using Azure.Identity;",
  "",
  "var builder = WebApplication.CreateBuilder(args);",
  "if (!builder.Environment.IsDevelopment())",
  "    builder.Configuration.AddAzureKeyVault(new Uri(builder.Configuration[\"KeyVault:Uri\"]!), new DefaultAzureCredential());",
  "builder.Services.AddAuthentication().AddJwtBearer();",
  "builder.Services.AddAuthorization();",
  "builder.Services.AddControllers();",
  "builder.Services.AddHealthChecks();",
  "",
  "var app = builder.Build();",
  "app.UseAuthentication();",
  "app.UseAuthorization();",
  "app.MapControllers().RequireAuthorization();",
  'app.MapHealthChecks("/health").AllowAnonymous();',
  "app.Run();",
].join("\n");
const SOURCES: Record<string, string> = {
  [PROGRAM]: PROGRAM_CS,
  [MASKING]: "namespace GmfCredit.Core;\n\npublic static class Masking\n{\n}\n",
  [SERVICE]: Array.from({ length: 300 }, (_, i) => `// FundingStatusLookupService line ${i + 1}`).join("\n"),
};
const fixture = (name: string) => readFileSync(join(__dirname, "fixtures", name), "utf8");

/**
 * Route the latest build of whichever process is opened to a finished fixture build with three
 * generated files, the review fixture and the parity fixture. Returns the opened process's id
 * (read from the builds request), which the review fixture's file targets are scoped to.
 */
async function routeFixtureBuild(page: Page): Promise<() => string> {
  let processId = "";
  const now = "2026-10-10T15:00:00Z";
  const json = (route: Route, body: unknown) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: typeof body === "string" ? body : JSON.stringify(body),
    });
  await page.route("**/processes/*/builds", (route) => {
    processId = new URL(route.request().url()).pathname.split("/")[2];
    return json(route, [
      { id: FIXTURE_BUILD, status: "passed", runtime: "container_app", build_hash: "fixture", job_id: null, created_at: now, finished_at: now },
    ]);
  });
  await page.route(`**/builds/${FIXTURE_BUILD}`, (route) =>
    json(route, {
      id: FIXTURE_BUILD, process_id: processId, job_id: null, status: "passed", runtime: "container_app",
      build_hash: "fixture", spec_hash: null, design_hash: null, created_at: now, finished_at: now, error: null,
      metrics: null, steps: ["restore", "build", "format", "test"].map((name) => ({ name, ok: true, log: `${name} passed` })),
      repair: null, builder: null, infra_validation: null,
    }),
  );
  await page.route(`**/builds/${FIXTURE_BUILD}/run`, (route) =>
    json(route, { running: false, url: null, port: null, pid: null, swagger_url: null, started_at: null, exit_code: null }),
  );
  const entry = (path: string) => ({
    path, origin: "code", process_id: null, size: SOURCES[path].length, sha256: "fixture", tibco_ref: null,
  });
  await page.route(`**/builds/${FIXTURE_BUILD}/files`, (route) =>
    json(route, { build_id: FIXTURE_BUILD, files: Object.keys(SOURCES).map(entry), counts: { code: 3 } }),
  );
  await page.route(`**/builds/${FIXTURE_BUILD}/files/**`, (route) => {
    const path = decodeURIComponent(new URL(route.request().url()).pathname.split(`/builds/${FIXTURE_BUILD}/files/`)[1]);
    return SOURCES[path] === undefined
      ? route.fulfill({ status: 404, body: "{}" })
      : json(route, { ...entry(path), content: SOURCES[path], language: "csharp" });
  });
  await page.route(`**/builds/${FIXTURE_BUILD}/reviews`, (route) =>
    route.request().method() === "GET" ? json(route, fixture("reviews.json").replaceAll("__PROCESS__", processId)) : route.continue(),
  );
  await page.route(`**/builds/${FIXTURE_BUILD}/parity`, (route) => json(route, fixture("parity.json")));
  return () => processId;
}

/** A scanned project with FundingStatusLookup open on a routed build. */
async function openOnFixtureBuild(page: Page): Promise<() => string> {
  const processId = await routeFixtureBuild(page);
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, "FundingStatusLookup");
  return processId;
}

test("a design decision's chip opens the rule it names", async ({ page }) => {
  test.setTimeout(300_000);
  let designJson: { decisions: { cited?: CitedText | null }[]; narrative_cited?: CitedText | null } | null = null;
  page.on("response", async (r) => {
    if (isGet(r, /\/processes\/[^/]+\/design$/)) designJson = (await r.json().catch(() => designJson)) as typeof designJson;
  });
  await design(page);

  // The narrative names a step: a design chip on that sentence that opens the step on the canvas.
  const narrative = page.getByTestId("narrative-text");
  const step = narrative.locator('[data-testid="cite-chip"][data-target-kind="activity"]');
  await expect(step).toHaveCount(1);
  await expect(step).toHaveAttribute("data-phase", "design");
  await expect(step).toHaveText(/^Design \d+$/);

  // The decision's choice and reason are one cited run; the Reason label is gone, Choice and
  // Alternatives stay. Its rule chip sits after the sentence naming the rule.
  await expect.poll(() => designJson?.decisions.some((d) => (d.cited?.refs.length ?? 0) > 0)).toBe(true);
  const decision = page.getByTestId("design-decision").filter({ has: page.getByTestId("cite-chip") }).first();
  await expect(decision.getByText("Choice", { exact: true })).toBeVisible();
  await expect(decision.getByText("Alternatives", { exact: true })).toBeVisible();
  await expect(decision.getByText("Reason", { exact: true })).toHaveCount(0);
  const text = decision.getByTestId("decision-text");
  await expect(text).toContainText("Verbatim SQL keeps behavior identical");
  const rule = text.locator('[data-testid="cite-chip"][data-phase="validate"]');
  await expect(rule).toHaveCount(1);
  await expect(text.getByTestId("cited-segment")).toHaveText(/^Rule R\d+ is tested against the same statement$/);
  const key = (((await text.getByTestId("cited-segment").textContent()) ?? "").match(/R\d+/) ?? [""])[0];
  await rule.hover();
  const preview = page.getByTestId("cite-preview");
  await expect(preview).toBeVisible();
  await expect(preview).toContainText(key);
  await page.mouse.move(0, 0);

  await rule.click();
  await expect(page.getByTestId("step-validate")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("rule-card").first()).toBeVisible();
});

test("no horizontal scroll at 390 px", async ({ page }) => {
  test.setTimeout(300_000);
  // Every step with its cited content in place: rules, findings, the design and its decisions.
  await design(page);
  await page.setViewportSize({ width: 390, height: 844 });
  // Wide content (tables, the canvas, code) scrolls inside its own region, never the page.
  for (const step of ["upload", "discover", "validate", "design", "build", "verify", "deploy"]) {
    await gotoStep(page, step);
    await expect(page.locator("main")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${step}: page`).toBe(true);
    expect(
      await page.evaluate(() => document.querySelector("main")!.scrollWidth <= document.querySelector("main")!.clientWidth),
      `${step}: main`,
    ).toBe(true);
  }
});

test("a review finding's chip opens the file at its line", async ({ page }) => {
  test.setTimeout(240_000);
  await openOnFixtureBuild(page);
  await actAs(page, "engineer.ravi");
  await page.getByTestId("step-build").click();

  const findings = page.getByTestId("review-panel").getByTestId("review-finding");
  await expect(findings).toHaveCount(2);
  const cited = findings.first();
  // The file and line are a chip in the text, not a separate label.
  await expect(cited).not.toContainText(`${SERVICE}:12`);
  const chip = cited.getByTestId("review-text").getByTestId("cite-chip");
  await expect(chip).toHaveCount(1);
  await expect(chip).toHaveAttribute("data-target-kind", "file");
  await expect(chip).toHaveAttribute("data-phase", "build");
  await expect(chip).toHaveText("Build 1");
  // A finding that names nothing and has no file is plain text.
  await expect(findings.nth(1).getByTestId("cite-chip")).toHaveCount(0);
  await expect(findings.nth(1)).toContainText("Name the options classes after their configuration sections.");

  await chip.hover();
  await expect(page.getByTestId("cite-preview")).toContainText("FundingStatusLookupService.cs:12");
  await page.mouse.move(0, 0);
  await chip.click();
  const viewer = page.getByTestId("code-viewer");
  await expect(viewer.getByTestId("code-path")).toHaveText(SERVICE);
  await expect(viewer.getByTestId("code-viewer-editor")).toHaveAttribute("data-hl-focus", "12");
  await expect(viewer.getByTestId("code-viewer-editor")).toHaveAttribute(
    "data-hl-focus-text",
    "// FundingStatusLookupService line 12",
  );
  // The chip brings the code pane into view, at the line.
  const line12 = viewer.locator(".view-line", { hasText: /line\s12$/ });
  await expect(line12).toBeInViewport();

  // Scrolled away, a second click on the same chip reveals the line again.
  await line12.click();
  // Monaco reads the platform from the user agent, which the Desktop Chrome device sets to Windows.
  await page.keyboard.press("Control+End");
  await expect(line12).toHaveCount(0);
  await chip.click();
  await expect(line12).toBeVisible();
  await expect(line12).toBeInViewport();

  // Choosing the file by hand drops the citation's highlight.
  const tree = page.getByTestId("target-tree");
  await tree.getByRole("button", { name: "Program.cs" }).click();
  await expect(viewer.getByTestId("code-path")).toHaveText(PROGRAM);
  await tree.getByRole("button", { name: "FundingStatusLookupService.cs" }).click();
  await expect(viewer.getByTestId("code-path")).toHaveText(SERVICE);
  await expect(viewer.getByTestId("code-viewer-editor")).not.toHaveAttribute("data-hl-focus", /.*/);
});

test("a parity difference's chip opens its finding", async ({ page }) => {
  test.setTimeout(240_000);
  await openOnFixtureBuild(page);
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();

  const rows = page.getByTestId("parity-table").getByTestId("parity-row");
  await expect(rows).toHaveCount(11);
  // The finding link column is gone: the finding is a chip in the difference's note.
  await expect(page.getByTestId("finding-pill")).toHaveCount(0);
  const row = rows.filter({ hasText: "output.body.dealerRegion" });
  const note = row.getByTestId("parity-note");
  await expect(note).toContainText('output.body.dealerRegion: expected "MIDWEST", got "UNKNOWN"');
  await expect(note.getByTestId("cited-segment")).toHaveText('output.body.dealerRegion: expected "MIDWEST", got "UNKNOWN"');
  await expect(note.getByTestId("cite-chip")).toHaveText(["Verify 1", "Validate 2"]);
  const finding = note.locator('[data-testid="cite-chip"][data-phase="validate"]');
  await finding.hover();
  await expect(page.getByTestId("cite-preview")).toContainText("Finding S2-trim-dealer, touched through a branch decision");
  await page.mouse.move(0, 0);
  // Matches no finding explains are plain.
  await expect(rows.and(page.locator('[data-status="match"]')).getByTestId("parity-note")).toHaveText(Array(7).fill(""));

  await finding.click();
  await expect(page.getByTestId("step-validate")).toHaveAttribute("aria-current", "step");
});

test("Deploy and Readiness show no evidence blocks", async ({ page }) => {
  test.setTimeout(300_000);
  const processId = await openOnFixtureBuild(page);
  await actAs(page, "ops.sam");
  await page.getByTestId("step-deploy").click();

  // Deploy: no evidence block; each shown check carries exactly one chip on its sentence whose
  // preview is the evidence as read, in mono.
  const panel = page.getByTestId("security-panel");
  const checks = panel.getByTestId("security-check");
  await expect(checks.first()).toBeVisible();
  // Read from the routed Program.cs (3) and Masking.cs, the review fixture's open security finding
  // and the parity fixture; the rest has no evidence in this build and is left out.
  await expect(checks).toHaveCount(6);
  await expect(panel.locator("pre")).toHaveCount(0);
  for (let i = 0; i < 6; i++) {
    await expect(checks.nth(i).getByTestId("security-check-text").getByTestId("cite-chip")).toHaveCount(1);
  }
  const authz = panel.locator('[data-key="authz"]');
  const chip = authz.getByTestId("cite-chip");
  await expect(chip).toHaveAttribute("data-phase", "deploy");
  await expect(chip).toHaveAttribute("data-target-kind", "file");
  await chip.hover();
  const preview = page.getByTestId("cite-preview");
  await expect(preview).toContainText("Program.cs:14");
  await expect(preview.getByTestId("cite-preview-text")).toHaveText("app.MapControllers().RequireAuthorization();");
  await expect(preview.getByTestId("cite-preview-text")).toHaveClass(/font-mono/);
  await page.mouse.move(0, 0);
  await expect(preview).toHaveCount(0);
  // The review check's evidence opens the review panel on Build.
  await expect(panel.locator('[data-key="review"]').getByTestId("cite-chip")).toHaveAttribute("data-target-kind", "step");
  // Opening the chip goes to the line it was read from, without toggling the check's row.
  await expect(authz).not.toHaveAttribute("open", "");
  await chip.click();
  await expect(page.getByTestId("step-build")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("code-viewer").getByTestId("code-path")).toHaveText(PROGRAM);
  await expect(page.getByTestId("code-viewer-editor")).toHaveAttribute("data-hl-focus", "14");

  // Readiness: scenarios give the test-data row evidence on a step panel (#test-data).
  const generated = await page.request.post(`${apiUrl}/processes/${processId()}/scenarios/generate`, {
    headers: { "X-User": "qa.priya" },
  });
  expect(generated.ok()).toBe(true);
  let readiness: ReadinessJson | null = null;
  page.on("response", async (r) => {
    if (isGet(r, /\/projects\/[^/]+\/readiness$/)) readiness = (await r.json().catch(() => readiness)) as ReadinessJson;
  });
  await page.goto("/readiness");
  const rows = page.getByTestId("readiness-row");
  await expect(rows.first()).toBeVisible();
  await expect.poll(() => readiness?.rows.length ?? 0).toBeGreaterThan(0);
  const data = readiness!;
  await expect(rows).toHaveCount(data.rows.length);
  await expect(page.getByText("no evidence yet")).toHaveCount(0);
  await expect(page.getByTestId("evidence-link")).toHaveCount(0);
  for (const r of data.rows) {
    // One chip per evidence item, on the row's status sentence.
    expect(r.cited?.refs.length ?? 0).toBe(r.evidence.length);
    const row = rows.and(page.locator(`[data-key="${r.key}"]`));
    await expect(row.getByTestId("cite-chip")).toHaveCount(r.evidence.length);
    if (r.evidence.length > 0) {
      const segment = (await row.getByTestId("cited-segment").first().textContent()) ?? "";
      expect(segment.length).toBeGreaterThan(0);
      expect(r.cited!.text).toContain(segment);
    }
  }
  expect(data.rows.find((r) => r.key === "test_data")?.evidence.length).toBeGreaterThan(0);

  // A page target opens its page.
  const settingsChip = page.locator('[data-testid="cite-chip"][data-target-kind="page"]').first();
  await settingsChip.click();
  await expect(page).toHaveURL(/\/(settings|history)/);
  await page.goBack();

  // A step target with a hash opens the step and lands on that panel, focused.
  const testData = rows.and(page.locator('[data-key="test_data"]')).getByTestId("cite-chip").first();
  await expect(testData).toHaveAttribute("data-target-kind", "step");
  await testData.click();
  await expect(page.getByTestId("step-verify")).toHaveAttribute("aria-current", "step");
  await expect(page.locator("#test-data")).toBeFocused();
  await expect(page.locator("#test-data")).toBeInViewport();
});
