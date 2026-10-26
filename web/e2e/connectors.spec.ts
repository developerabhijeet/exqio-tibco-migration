import { expect, test, type APIRequestContext } from "@playwright/test";
import { actAs } from "./helpers";
import { apiUrl } from "../scripts/root-env";

// Settings → Connectors against the local fakes the e2e stack starts (playwright.config.ts): the
// install enables GitHub and Atlassian (Data Center) and leaves Figma off, so both install states
// are on screen. Credentials come from token files only; the page shows whether they are mounted.

/** The fakes' tokens (api/tests/fakes defaults): they must never reach the page. */
const FAKE_TOKENS = ["ghs-fake-token", "atlassian-fake-token", "figma-fake-token"];

/** Put the admin layer back to "off, no overrides", so every run starts from the same state. */
async function resetConnectors(request: APIRequestContext): Promise<void> {
  const res = await request.put(`${apiUrl}/settings/connectors`, {
    headers: { "X-User": "ops.sam" },
    data: {
      github: { enabled: false, base_url: "", api_url: "", default_owner: "" },
      atlassian: { enabled: false, confluence_url: "", jira_url: "", space_key: "", project_key: "" },
    },
  });
  expect(res.status(), await res.text()).toBe(200);
}

test.describe("settings tabs", () => {
  test("the connectors tab has its own URL and General keeps the existing panels", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByTestId("settings-tab-general")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("providers")).toBeVisible();

    await page.getByTestId("settings-tab-connectors").click();
    await expect(page).toHaveURL(/\?tab=connectors$/);
    await expect(page.getByTestId("settings-tab-connectors")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("connector-github")).toBeVisible();
    await expect(page.getByTestId("providers")).toBeHidden();

    // Arrow keys move between the tabs (the WAI-ARIA tabs pattern).
    await page.getByTestId("settings-tab-connectors").press("ArrowLeft");
    await expect(page.getByTestId("settings-tab-general")).toBeFocused();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByTestId("providers")).toBeVisible();
  });
});

test.describe("connectors", () => {
  // Before each: every test starts with the connectors off and no overrides (a refused save or a
  // failed test of one test does not leak into the next).
  test.beforeEach(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    await resetConnectors(request);
    await request.dispose();
  });
  test.afterAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    await resetConnectors(request);
    await request.dispose();
  });

  test("admin enables GitHub against the fake and tests it", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    const card = page.getByTestId("connector-github");
    const save = page.getByTestId("connector-github-save");
    await expect(page.getByTestId("connector-github-install")).toContainText("Enabled by the install");
    await expect(save).toBeDisabled(); // nothing to save yet
    await page.getByTestId("connector-github-toggle").check();
    await save.click();
    await expect(card).toContainText("Saved.");
    await expect(save).toBeDisabled();
    // An edit clears "Saved." and enables Save again; undoing it disables Save.
    await page.getByTestId("connector-github-field-default_owner").fill("gmf");
    await expect(card).not.toContainText("Saved.");
    await expect(save).toBeEnabled();
    await page.getByTestId("connector-github-field-default_owner").fill("");
    await expect(save).toBeDisabled();

    await page.getByTestId("connector-github-test").click();
    const result = page.getByTestId("connector-github-result");
    await expect(result).toHaveAttribute("aria-live", "polite");
    await expect(result).toHaveAttribute("aria-atomic", "true");
    await expect(result).toContainText("exiqo-bot");
    await expect(result).toContainText("Connected");
    await expect(page.getByTestId("connector-github-secret-token")).toContainText("present");
    await expect(page.getByTestId("connector-github-secret-token")).toContainText("GITHUB_TOKEN_FILE");
    await expect(page.getByTestId("connector-github-state")).toContainText("Ready");

    // The last test is kept: a reload shows it with who ran it.
    await page.reload();
    await expect(result).toContainText("exiqo-bot");
    await expect(result).toContainText("ops.sam");
    const html = await page.content();
    for (const token of FAKE_TOKENS) expect(html).not.toContain(token);
  });

  test("a failed test shows the API's reason, and a URL change since a passing test is called out", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    const base = await page.getByTestId("connector-github-url").getAttribute("placeholder");
    expect(base).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    await page.getByTestId("connector-github-toggle").check();
    await page.getByTestId("connector-github-save").click();
    await page.getByTestId("connector-github-test").click();
    await expect(page.getByTestId("connector-github-state")).toContainText("Ready");

    // Same allowed host, a path the fake does not serve: valid here, refused by the server it calls.
    await page.getByTestId("connector-github-field-api_url").fill(`${base}/missing/api/v3`);
    await page.getByTestId("connector-github-save").click();
    await expect(page.getByTestId("connector-github")).toContainText("Saved.");
    await expect(page.getByTestId("connector-github-state")).toContainText("Settings changed since last test");

    await page.getByTestId("connector-github-test").click();
    const result = page.getByTestId("connector-github-result");
    await expect(result).toContainText("Connection failed");
    await expect(result).toContainText("Reason");
    await expect(result).toContainText("404");
    await expect(page.getByTestId("connector-github-state")).toContainText("Last test failed");
    const html = await page.content();
    for (const token of FAKE_TOKENS) expect(html).not.toContain(token);
  });

  test("admin enables Atlassian and tests Confluence and Jira together", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    const card = page.getByTestId("connector-atlassian");
    await expect(card).toContainText("Confluence");
    await expect(card).toContainText("Jira");
    await expect(page.getByTestId("connector-atlassian-url")).toHaveAttribute("placeholder", /http:\/\/127\.0\.0\.1:\d+/);
    await page.getByTestId("connector-atlassian-toggle").check();
    await page.getByTestId("connector-atlassian-field-space_key").fill("GMF");
    await page.getByTestId("connector-atlassian-save").click();
    await expect(card).toContainText("Saved.");
    await expect(page.getByTestId("connector-atlassian-field-space_key")).toHaveValue("GMF");
    await page.getByTestId("connector-atlassian-test").click();
    await expect(page.getByTestId("connector-atlassian-result")).toContainText("Connected");
    await expect(page.getByTestId("connector-atlassian-secret-token")).toContainText("present");
    const html = await page.content();
    for (const token of FAKE_TOKENS) expect(html).not.toContain(token);
  });

  test("invalid fields are flagged at the field and nothing is sent", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    const puts: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "PUT" && r.url().includes("/settings/connectors")) puts.push(r.url());
    });

    const apiField = page.getByTestId("connector-github-field-api_url");
    await apiField.fill("https://elsewhere.example/api/v3");
    await expect(apiField).toHaveAttribute("aria-invalid", "true");
    const errorId = await apiField.getAttribute("aria-errormessage");
    await expect(page.locator(`[id="${errorId}"]`)).toContainText("host elsewhere.example not in allowedHosts");
    await page.getByTestId("connector-github-save").click();
    await expect(apiField).toBeFocused();

    const key = page.getByTestId("connector-atlassian-field-project_key");
    await key.fill("gmf-credit");
    await expect(key).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByTestId("connector-atlassian-error-project_key")).toContainText("not a Jira project key");
    await key.fill("GMF");
    await expect(key).not.toHaveAttribute("aria-invalid", "true");

    await page.getByTestId("connector-atlassian-url").fill("https://bot:pw@confluence.example");
    await expect(page.getByTestId("connector-atlassian-error-confluence_url")).toContainText(
      "credentials in the URL are not allowed",
    );
    expect(puts).toEqual([]);
  });

  test("figma is off in this install and cannot be toggled", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    await expect(page.getByTestId("connector-figma-install")).toContainText("Off in this install: set connectors.figma.enabled");
    await expect(page.getByTestId("connector-figma-toggle")).toBeDisabled();
    await expect(page.getByTestId("connector-figma-test")).toBeDisabled();
    await expect(page.getByTestId("connector-figma-state")).toContainText("Off in this install");
  });

  test("non-admin sees read-only cards", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "architect.lee");
    await expect(page.getByTestId("connector-github-install")).toContainText("Enabled by the install");
    await expect(page.getByTestId("connector-github-test")).toBeDisabled();
    await expect(page.getByTestId("connector-github-toggle")).toBeDisabled();
    await expect(page.getByTestId("connector-github-save")).toHaveCount(0);
    await expect(page.getByTestId("connector-github")).toContainText("administrators");
  });

  test("the API refuses a non-admin's writes and tests, whatever the screen shows", async ({ request }) => {
    const headers = { "X-User": "architect.lee" };
    const put = await request.put(`${apiUrl}/settings/connectors`, { headers, data: { github: { enabled: true } } });
    expect(put.status()).toBe(403);
    const run = await request.post(`${apiUrl}/connectors/github/test`, { headers });
    expect(run.status()).toBe(403);
    // And the read gives a non-admin the flags only: no URLs, secret names or last test.
    const view = await (await request.get(`${apiUrl}/settings/connectors`, { headers })).json();
    expect(Object.keys(view.github).sort()).toEqual(["enabled", "install_enabled"]);
  });
});
