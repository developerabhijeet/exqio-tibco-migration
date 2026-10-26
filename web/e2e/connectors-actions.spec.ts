import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess } from "./helpers";
import { apiUrl } from "../scripts/root-env";

// Publish actions in the phase tabs against the local fakes (playwright.config.ts starts them;
// scripts/fake_connectors.py seeds the GitHub repository gmf/credit and the Jira project GMF):
// Jira issues from findings (Validate) and open questions (Design), the architecture page to
// Confluence and Figma frames as references (Design), the build to GitHub with its pull request
// (Build), and an unexplained parity difference (Verify: the fixture's run is recorded for the
// build by scripts/e2e_seed_verify.py, as the verify job would store it).
//
// One process is taken through the pipeline once, so the tests run in order on one page.

const PROCESS = "FundingStatusLookup";
const ROOT = join(__dirname, "..", "..");
const PARITY_FIXTURE = join(__dirname, "fixtures", "parity.json");
/** A defect of the parity fixture, by the server's `parity_key(scenario key, field)`. */
const DEFECT = "FundingStatusLookup:happy:output.body.status";

/** The admin layer: both connectors on, with the Confluence space and the Jira project. */
async function configure(request: APIRequestContext, on: boolean): Promise<void> {
  const res = await request.put(`${apiUrl}/settings/connectors`, {
    headers: { "X-User": "ops.sam" },
    data: on
      ? {
          github: { enabled: true },
          atlassian: { enabled: true, space_key: "GMF", project_key: "GMF" },
          figma: { enabled: true, file_keys: ["FILE1"] },
        }
      : {
          github: { enabled: false, base_url: "", api_url: "", default_owner: "" },
          atlassian: { enabled: false, confluence_url: "", jira_url: "", space_key: "", project_key: "" },
          figma: { enabled: false, api_url: "", web_url: "", file_keys: [] },
        },
  });
  expect(res.status(), await res.text()).toBe(200);
}

test.describe.configure({ mode: "serial" });

let page: Page;
let buildId = "";

test.beforeAll(async ({ browser, playwright }) => {
  const request = await playwright.request.newContext();
  await configure(request, true);
  await request.dispose();
  page = await browser.newPage();
});

test.afterAll(async ({ playwright }) => {
  await page?.close();
  const request = await playwright.request.newContext();
  await configure(request, false);
  await request.dispose();
});

test("SME files a Jira issue for a finding in Validate", async () => {
  test.setTimeout(240_000);
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProjectAndScan(page);
  await openDealerProcess(page, PROCESS);
  await page.getByTestId("step-validate").click();
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByTestId("analysis-log")).toContainText("citations verified", { timeout: 90_000 });

  const panel = page.getByTestId("jira-validate");
  await expect(panel.getByTestId("jira-create")).toBeDisabled(); // nothing selected yet
  const finding = panel.locator('[data-testid^="jira-select-finding-"]').first();
  const ref = ((await finding.getAttribute("data-testid")) ?? "").replace("jira-select-finding-", "");
  await finding.check();
  const filed = page.waitForRequest((r) => r.method() === "POST" && /\/jira\/issues$/.test(r.url()));
  await panel.getByTestId("jira-create").click();
  const request = await filed;
  const processId = new URL(request.url()).pathname.split("/")[2];
  expect(request.postDataJSON()).toEqual({ items: [{ type: "finding", ref }], create_again: false });
  await expect(panel.getByTestId("jira-create-status")).toHaveAttribute("data-state", "done", { timeout: 30_000 });
  await expect(panel.getByTestId("jira-create-status")).toContainText("1 created");
  const chip = panel.locator('[data-testid^="link-jira_issue-GMF-"]');
  await expect(chip).toHaveCount(1);
  await expect(chip).toHaveAttribute("href", /\/browse\/GMF-\d+$/);

  // The link is recorded against the finding's stable key.
  const links = await page.request.get(`${apiUrl}/processes/${processId}/links`, { headers: { "X-User": "viewer.val" } });
  const body = (await links.json()) as { object_type: string; local_ref: string }[];
  expect(body.filter((l) => l.object_type === "jira_issue").map((l) => l.local_ref)).toEqual([`finding:${ref}`]);

  // Decide the rest so the design can run.
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
  // Decided "ask", the finding has nothing left to track, but its issue is still shown.
  await expect(panel.locator(`[data-testid="jira-select-finding-${ref}"]`)).toBeDisabled();
  await expect(chip).toBeVisible();
});

test("architect publishes to Confluence and files an open question by its id", async () => {
  test.setTimeout(180_000);
  await actAs(page, "architect.lee");
  await page.getByTestId("step-design").click();
  await page.getByRole("button", { name: "Generate design" }).click();
  await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/, { timeout: 60_000 });
  await page.getByTestId("publish-confluence").click();
  await expect(page.getByTestId("publish-confluence-status")).toHaveAttribute("data-state", "done", { timeout: 30_000 });
  await expect(page.getByTestId("link-confluence_page")).toBeVisible();
  await expect(page.getByTestId("link-confluence_page")).toHaveAttribute("href", /^http:\/\/127\.0\.0\.1:\d+\//);
  await expect(page.getByTestId("publish-confluence")).toContainText("Republish");

  const panel = page.getByTestId("jira-design");
  const question = panel.locator('[data-testid^="jira-select-question-"]').first();
  // Named by the question's stable id, never its position in the list.
  await expect(question).toHaveAttribute("data-testid", /^jira-select-question-[0-9a-f]{16}$/);
  await question.check();
  await panel.getByTestId("jira-create").click();
  await expect(panel.getByTestId("jira-create-status")).toHaveAttribute("data-state", "done", { timeout: 30_000 });
  await expect(panel.locator('[data-testid^="link-jira_issue-GMF-"]')).toHaveCount(1);

  await page.getByTestId("gate-G3").getByRole("button", { name: "Approve G3" }).click();
  await expect(page.getByTestId("gate-G3")).toHaveAttribute("data-state", "approved");
});

test("architect attaches Figma frames as design references", async () => {
  const figma = page.getByTestId("figma-references");
  await expect(figma).toContainText("No frames attached.");
  await page.getByTestId("figma-file").fill("FILE1");
  await page.getByTestId("figma-browse").click();
  await expect(page.getByTestId("figma-frame-1:2")).toBeVisible();
  await expect(figma).toContainText("Payment History");
  await page.getByTestId("figma-frame-1:2").check();
  await page.getByTestId("figma-frame-2:1").check();
  await page.getByTestId("figma-attach").click();
  await expect(page.getByTestId("figma-attach-status")).toHaveAttribute("data-state", "done", { timeout: 30_000 });
  await expect(page.getByTestId("figma-attach-status")).toContainText("2 frame(s) attached, 2 with thumbnails");

  // The attached list refreshes when the job ends; each thumbnail is the stored PNG, as a blob.
  const summary = page.getByTestId("figma-attached-1:2");
  await expect(summary).toContainText("Account Summary");
  await expect(summary.getByRole("link", { name: "Account Summary" })).toHaveAttribute("href", /^https:\/\/www\.figma\.example\//);
  const thumb = summary.getByRole("img", { name: "Figma frame Account Summary" });
  await expect(thumb).toHaveAttribute("src", /^blob:/);
  await expect.poll(() => thumb.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBe(1);
  await expect(page.getByTestId("figma-attached-2:1")).toContainText("Payoff Quote");
  // Frames already attached are marked in the list; attaching again refreshes them.
  const row = page.getByTestId("figma-frame-1:2").locator("xpath=..");
  await expect(row).toContainText("attached: refresh");
  await page.getByTestId("figma-frame-1:2").check();
  await page.getByTestId("figma-attach").click();
  await expect(page.getByTestId("figma-attach-status")).toContainText("1 frame(s) attached");
  await expect(page.getByTestId("figma-attached").locator('[data-testid^="figma-attached-"]')).toHaveCount(2);
});

test("engineer pushes a build and sees the PR link", async () => {
  test.setTimeout(420_000);
  await actAs(page, "engineer.ravi");
  await page.getByTestId("step-build").click();
  const posted = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/processes\/[^/]+\/build$/.test(new URL(r.url()).pathname),
  );
  await page.getByRole("button", { name: "Generate and build" }).click();
  buildId = ((await (await posted).json()) as { build_id: string }).build_id;
  // Passed or failed (NuGet may be out of reach), a finished build has a solution to push.
  const push = page.getByTestId("push-github");
  await expect(push).toBeEnabled({ timeout: 360_000 });

  await push.click();
  const dialog = page.getByTestId("push-github-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("push-github-branch")).toHaveAttribute("placeholder", `exiqo/fundingstatuslookup/build-${buildId.slice(0, 8)}`);
  await expect(page.getByTestId("push-github-repo")).toBeFocused();
  await page.getByTestId("push-github-repo").fill("gmf/credit");
  // Tab stays inside the dialog: from its last control it wraps to the first.
  await page.getByTestId("push-github-submit").focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByTestId("push-github-submit")).toBeFocused();
  await page.getByTestId("push-github-submit").click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("push-github-status")).toHaveAttribute("data-state", "done", { timeout: 60_000 });
  await expect(page.getByTestId("link-pull_request")).toHaveAttribute("href", /\/gmf\/credit\/pull\/1$/);
  await expect(page.getByTestId("link-branch")).toHaveAttribute("href", /\/gmf\/credit\/tree\/exiqo\//);

  // A branch outside exiqo/ is refused at the field, before any request.
  await push.click();
  await page.getByTestId("push-github-repo").fill("gmf/credit");
  await page.getByTestId("push-github-branch").fill("main");
  await expect(page.getByTestId("push-github-submit")).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("push-github-dialog")).toBeHidden();
  await expect(push).toBeFocused(); // focus goes back to the trigger
});

test("QA files an unexplained parity difference in Verify", async () => {
  // The fixture's verify run, recorded for this build as the verify job stores it.
  execFileSync(join(ROOT, ".venv", "bin", "python"), ["scripts/e2e_seed_verify.py", buildId, PARITY_FIXTURE], {
    cwd: ROOT,
    env: { ...process.env, DATABASE_URL: process.env.DATABASE_URL ?? "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo" },
  });
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();
  const panel = page.getByTestId("jira-verify");
  await expect(panel.locator('[data-testid^="jira-select-parity-"]')).toHaveCount(2);
  const filed = page.waitForRequest((r) => r.method() === "POST" && /\/jira\/issues$/.test(r.url()));
  await panel.getByTestId(`jira-select-parity-${DEFECT}`).check();
  await panel.getByTestId("jira-create").click();
  expect((await filed).postDataJSON()).toEqual({ items: [{ type: "parity", ref: DEFECT }], create_again: false });
  await expect(panel.getByTestId("jira-create-status")).toHaveAttribute("data-state", "done", { timeout: 30_000 });
  await expect(panel.getByTestId("jira-create-status")).toContainText("1 created");
  await expect(panel.locator('[data-testid^="link-jira_issue-GMF-"]')).toHaveCount(1);
});

test("a request already queued, or refused by validation, reads as one line", async () => {
  // The API refuses the same issues for the same process while a job for them is still queued
  // (409; api/tests/test_connector_jira.py proves the server side). A queue that has not claimed the
  // first job yet cannot be produced on demand here, so the answers are the API's, routed.
  const panel = page.getByTestId("jira-verify");
  let posts = 0;
  await page.route("**/jira/issues", async (route) => {
    posts += 1;
    await new Promise((r) => setTimeout(r, 400));
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      json: { detail: "the same issues are already queued for this process" },
    });
  });
  await panel.getByTestId(`jira-select-parity-${DEFECT}`).check();
  // A double click sends one request: the button is disabled while it is in flight.
  await panel.getByTestId("jira-create").dblclick();
  await expect(panel.getByTestId("jira-create-error")).toHaveText("the same issues are already queued for this process");
  expect(posts).toBe(1);
  await page.unroute("**/jira/issues");

  await page.route("**/jira/issues", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      json: { detail: [{ type: "string_too_short", loc: ["body", "items", 0, "ref"], msg: "String should have at least 1 character" }] },
    }),
  );
  await panel.getByTestId(`jira-select-parity-${DEFECT}`).check();
  await panel.getByTestId("jira-create").click();
  await expect(panel.getByTestId("jira-create-error")).toHaveText("items.0.ref: String should have at least 1 character");
  await page.unroute("**/jira/issues");
});

test("viewer sees links but no actions", async () => {
  await actAs(page, "viewer.val");
  await page.getByTestId("step-build").click();
  await expect(page.getByTestId("push-github")).toBeDisabled();
  await expect(page.getByTestId("github-publish")).toContainText("Viewers are read only");
  await expect(page.getByTestId("link-pull_request")).toBeVisible();

  await page.getByTestId("step-design").click();
  await expect(page.getByTestId("publish-confluence")).toBeDisabled();
  await expect(page.getByTestId("link-confluence_page")).toBeVisible();
  const questions = page.getByTestId("jira-design");
  await expect(questions.getByTestId("jira-create")).toBeDisabled();
  await expect(questions.locator('[data-testid^="link-jira_issue-GMF-"]')).toHaveCount(1);
  await expect(questions.locator('[data-testid^="jira-select-question-"]').first()).toBeDisabled();
  await expect(page.getByTestId("figma-browse")).toBeDisabled();
  await expect(page.getByTestId("figma-attached-1:2")).toBeVisible();
});
