import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { actAs, newProjectAndScan, openDealerProcess } from "./helpers";
import { apiUrl } from "../scripts/root-env";

// Publish actions in the phase tabs against the local fakes (playwright.config.ts starts them;
// scripts/fake_connectors.py seeds the GitHub repository gmf/credit and the Jira project GMF):
// Jira issues from findings (Validate) and open questions (Design), the architecture page to
// Confluence (Design), the build to GitHub with its pull request (Build), and a parity difference
// the API refuses (Verify). The install leaves Figma off, so its panel is not shown.
//
// One process is taken through the pipeline once, so the tests run in order on one page.

const PROCESS = "FundingStatusLookup";
const PARITY = readFileSync(join(__dirname, "fixtures", "parity.json"), "utf8");

/** The admin layer: both connectors on, with the Confluence space and the Jira project. */
async function configure(request: APIRequestContext, on: boolean): Promise<void> {
  const res = await request.put(`${apiUrl}/settings/connectors`, {
    headers: { "X-User": "ops.sam" },
    data: on
      ? { github: { enabled: true }, atlassian: { enabled: true, space_key: "GMF", project_key: "GMF" } }
      : {
          github: { enabled: false, base_url: "", api_url: "", default_owner: "" },
          atlassian: { enabled: false, confluence_url: "", jira_url: "", space_key: "", project_key: "" },
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
  // Figma is off in this install: no references panel.
  await expect(page.getByTestId("figma-references")).toHaveCount(0);

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
  await page.getByTestId("push-github-repo").fill("gmf/credit");
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
});

test("QA files an unexplained difference and sees the API's refusal", async () => {
  // The parity run is the committed fixture (routed): its differences are not in this build's
  // verify run, so the API refuses them (422) and the panel says why.
  await page.route(`**/builds/${buildId}/parity`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: PARITY }),
  );
  await actAs(page, "qa.priya");
  await page.getByTestId("step-verify").click();
  const panel = page.getByTestId("jira-verify");
  await expect(panel.locator('[data-testid^="jira-select-parity-"]')).toHaveCount(2);
  await panel.getByTestId("jira-select-parity-FundingStatusLookup:happy:output.body.status").check();
  await panel.getByTestId("jira-create").click();
  await expect(panel.getByTestId("jira-create-error")).toContainText("is not in the latest verify run");
  await page.unroute(`**/builds/${buildId}/parity`);
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
});
