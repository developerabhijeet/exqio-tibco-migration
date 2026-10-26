import { expect, test } from "@playwright/test";
import { actAs } from "./helpers";

// Settings → Connectors against the local fakes the e2e stack starts (playwright.config.ts): the
// install enables GitHub and Atlassian (Data Center) and leaves Figma off, so both install states
// are on screen. Credentials come from token files only; the page shows whether they are mounted.

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
  test("admin enables GitHub against the fake and tests it", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    await expect(page.getByTestId("connector-github-install")).toContainText("Enabled by the install");
    await page.getByTestId("connector-github-toggle").check();
    await page.getByTestId("connector-github-save").click();
    await expect(page.getByTestId("connector-github")).toContainText("Saved");
    await page.getByTestId("connector-github-test").click();
    await expect(page.getByTestId("connector-github-result")).toContainText("exiqo-bot");
    await expect(page.getByTestId("connector-github-result")).toContainText("Connected");
    await expect(page.getByTestId("connector-github-secret-token")).toContainText("present");
    await expect(page.getByTestId("connector-github-secret-token")).toContainText("GITHUB_TOKEN_FILE");
    await expect(page.getByTestId("connector-github-state")).toContainText("Ready");

    // The last test is kept: a reload shows it with who ran it.
    await page.reload();
    await expect(page.getByTestId("connector-github-result")).toContainText("exiqo-bot");
    await expect(page.getByTestId("connector-github-result")).toContainText("ops.sam");
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
    await expect(card).toContainText("Saved");
    await expect(page.getByTestId("connector-atlassian-field-space_key")).toHaveValue("GMF");
    await page.getByTestId("connector-atlassian-test").click();
    await expect(page.getByTestId("connector-atlassian-result")).toContainText("Connected");
    await expect(page.getByTestId("connector-atlassian-secret-token")).toContainText("present");
  });

  test("a URL outside the allowlist is refused with the reason", async ({ page }) => {
    await page.goto("/settings?tab=connectors");
    await actAs(page, "ops.sam");
    await page.getByTestId("connector-github-field-api_url").fill("https://elsewhere.example/api/v3");
    await page.getByTestId("connector-github-save").click();
    await expect(page.getByTestId("connector-github").getByRole("alert")).toContainText("not in allowedHosts");
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
});
