import { expect, test, type Page, type Route } from "@playwright/test";
import { actAs, editedZipFixture, openFailedBuild, openFinishedBuild } from "./helpers";

// The iteration loop (sub-project 6) on the real API: re-entering Design from a failed build, an
// edited copy of a build's download uploaded and previewed, and what a viewer and an SME may do.
// Run with BUILD_TOOLCHAIN=false (passed to the e2e API by playwright.config.ts): every build is
// then generated, filled and stored, records restore as skipped and fails. Without it the tests
// that need a failed build are skipped with that reason.
// The repository panel (Tasks 9-10) is driven through routed answers, so it is covered whether or
// not this API has the repository routes yet.

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

/** The tests that need a failed build: the API must run with the toolchain off. */
const TOOLCHAIN_OFF = process.env.BUILD_TOOLCHAIN === "false";
const NEEDS_OFF = "needs BUILD_TOOLCHAIN=false (the e2e API then records every build as failed)";

test("architect re-enters design from a failed build", async ({ page }) => {
  test.skip(!TOOLCHAIN_OFF, NEEDS_OFF);
  test.setTimeout(420_000);
  await actAs(page, "architect.lee"); await openFailedBuild(page);
  await page.getByTestId("reenter-design").click();
  await expect(page.getByTestId("reenter-dialog")).toContainText("build");
  await page.getByTestId("requirements").fill("Funding over 50k needs a second approver.");
  await page.getByTestId("open-iteration").click();
  await expect(page.getByTestId("iteration-strip")).toContainText("Iteration 2");
  await expect(page.getByTestId("gate-G3")).toContainText("pending");
});

test("engineer uploads an edited zip and sees exactly the edits", async ({ page }) => {
  test.setTimeout(420_000);
  await actAs(page, "engineer.ravi"); await openFinishedBuild(page);
  await page.getByTestId("edit-drop").setInputFiles(await editedZipFixture());
  await expect(page.locator("[data-testid^=edit-row-]")).toHaveCount(2);
  await expect(page.getByTestId("resume-edits")).toContainText("Resume from build");
  // A row opens the baseline and the edited file side by side.
  await page.locator("[data-testid^=edit-row-]").first().click();
  await expect(page.getByTestId("edit-diff")).toBeVisible();
  // The edit set opened iteration 2 (kind edits); nothing is built until it is resumed.
  await expect(page.getByTestId("iteration-strip")).toContainText("Iteration 2");
});

test("viewer sees iteration controls disabled", async ({ page }) => {
  test.skip(!TOOLCHAIN_OFF, NEEDS_OFF);
  test.setTimeout(420_000);
  await actAs(page, "viewer.val"); await openFailedBuild(page);
  await expect(page.getByTestId("reenter-design")).toBeDisabled();
  await expect(page.getByTestId("reenter-design-reason")).toContainText("Viewers are read only");
  await expect(page.getByTestId("edit-drop")).toBeDisabled();
});

test("an SME re-enters design with requirements only, as JSON", async ({ page }) => {
  test.skip(!TOOLCHAIN_OFF, NEEDS_OFF);
  test.setTimeout(420_000);
  await actAs(page, "sme.vicky"); await openFailedBuild(page);
  await page.getByTestId("reenter-design").click();
  const dialog = page.getByTestId("reenter-dialog");
  // The failures are listed for context, but an SME's iteration carries requirements only.
  await expect(dialog.locator("[data-testid^=failure-]").first()).toBeVisible();
  await expect(dialog.locator("[data-testid^=failure-] input[type=checkbox]").first()).toBeDisabled();
  await expect(page.getByTestId("log-drop")).toBeDisabled();
  await expect(page.getByTestId("open-iteration")).toBeDisabled();
  await page.getByTestId("requirements").fill("Funding over 50k needs a second approver.");
  const posted = page.waitForRequest(
    (r) => r.method() === "POST" && /\/processes\/[^/]+\/iterations$/.test(new URL(r.url()).pathname),
  );
  await page.getByTestId("open-iteration").click();
  const req = await posted;
  expect(req.headers()["content-type"]).toContain("application/json");
  expect(JSON.parse(req.postData() ?? "{}")).toEqual({ requirements: "Funding over 50k needs a second approver." });
  await expect(page.getByTestId("iteration-strip")).toContainText("Iteration 2");
});

// ------------------------------------------------------------------ repository panel (routed)

const REPO: Record<string, string | null> = {
  id: "repo-1",
  project_id: "p",
  repo: "gmf/estate",
  branch: "main",
  purpose: "estate",
  process_id: null,
  last_commit: "0123456789abcdef0123",
  connected_by: "sme.vicky",
  connected_at: "2026-10-26T10:00:00Z",
  job_id: null,
};

/** GitHub on in this answer of GET /connectors (the other connectors off). */
async function githubOn(page: Page): Promise<void> {
  await page.route("**/connectors", (route: Route) =>
    route.request().method() !== "GET"
      ? route.fallback()
      : route.fulfill({
          json: { github: { enabled: true }, atlassian: { enabled: false, confluence: false, jira: false }, figma: { enabled: false } },
        }),
  );
}

async function newProject(page: Page): Promise<void> {
  const name = `e2e repo ${Date.now()}`;
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
}

test("the estate repository panel connects and pulls a branch", async ({ page }) => {
  await githubOn(page);
  const repos: (typeof REPO)[] = [];
  let posted: Record<string, unknown> | null = null;
  await page.route("**/projects/*/repos", async (route) => {
    if (route.request().method() === "POST") {
      posted = route.request().postDataJSON();
      const row = { ...REPO, last_commit: null, job_id: "job-fetch" };
      repos.push(row);
      return route.fulfill({ status: 201, json: row });
    }
    return route.fulfill({ json: repos });
  });
  await page.route("**/repos/repo-1/pull", (route) => route.fulfill({ json: { changed: false } }));
  await page.route("**/jobs/job-fetch", (route) =>
    route.fulfill({ json: { id: "job-fetch", kind: "source.fetch", status: "done", result: { kind: "upload", id: "u1", commit: "0123456789abcdef" } } }),
  );
  await actAs(page, "sme.vicky");
  await newProject(page);
  const panel = page.getByTestId("repo-panel");
  await expect(panel).toBeVisible();
  await panel.getByLabel("Repository (owner/name)").fill("gmf/estate");
  await panel.getByLabel("Branch").fill("main");
  await panel.getByRole("button", { name: "Connect" }).click();
  await expect(panel).toContainText("gmf/estate");
  expect(posted).toEqual({ repo: "gmf/estate", branch: "main", purpose: "estate" });
  await panel.getByRole("button", { name: "Pull" }).click();
  await expect(panel).toContainText("no new commit");
});

test("the repository panel is hidden when the API has no repository routes", async ({ page }) => {
  await githubOn(page);
  await page.route("**/projects/*/repos", (route) => route.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await actAs(page, "sme.vicky");
  await newProject(page);
  await expect(page.getByTestId("zip-input")).toBeAttached();
  await expect(page.getByTestId("repo-panel")).toHaveCount(0);
});

test("a viewer sees the repository panel but cannot connect", async ({ page }) => {
  await githubOn(page);
  await page.route("**/projects/*/repos", (route) => route.fulfill({ json: [REPO] }));
  await actAs(page, "sme.vicky");
  await newProject(page);
  await actAs(page, "viewer.val");
  const panel = page.getByTestId("repo-panel");
  await expect(panel).toContainText("gmf/estate");
  await expect(panel.getByRole("button", { name: "Connect" })).toBeDisabled();
  await expect(panel.getByRole("button", { name: "Pull" })).toBeDisabled();
  await expect(panel).toContainText("Viewers are read only");
});
