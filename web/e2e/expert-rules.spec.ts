import { expect, test } from "@playwright/test";
import { actAs, newProject } from "./helpers";

// Expert rules: loaded at Upload, listed on /rules (reached from the top bar), one switched off.
test("an SME loads the GMF expert rules at Upload, sees them on Rules and turns one off", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProject(page, `expert rules ${Date.now()}`);
  const card = page.getByTestId("expert-rules-card");
  await expect(card.getByTestId("expert-rules-count")).toHaveText(/^0 rules/);
  await card.getByTestId("expert-rules-defaults").click();
  await expect(card.getByTestId("expert-rules-done")).toContainText("125 imported");
  await expect(card.getByTestId("expert-rules-count")).toHaveText(/^125 rules/);

  await page.getByRole("navigation", { name: "Secondary" }).getByRole("link", { name: "Rules" }).click();
  await expect(page).toHaveURL(/\/rules$/);
  await expect(page.getByRole("heading", { level: 1, name: "Rules" })).toBeVisible();
  await expect(page.getByTestId("rules-shown")).toHaveText("125 of 125 rules");
  await page.getByTestId("rules-severity").selectOption("Critical");
  await expect(page.getByTestId("rules-shown")).toHaveText("35 of 125 rules");
  await page.getByTestId("rules-search").fill("TIB-001");
  await page.getByTestId("rule-row-TIB-001").click();
  await expect(page.getByTestId("rule-detail-TIB-001")).toContainText("Pass criteria");
  const toggle = page.getByTestId("rule-toggle-TIB-001");
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await page.reload();
  await page.getByTestId("rules-search").fill("TIB-001");
  await expect(page.getByTestId("rule-toggle-TIB-001")).not.toBeChecked();
});

test("a viewer reads the rules but cannot import or toggle", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "sme.vicky");
  await newProject(page, `expert rules viewer ${Date.now()}`);
  await page.getByTestId("expert-rules-defaults").click();
  await expect(page.getByTestId("expert-rules-done")).toBeVisible();
  await actAs(page, "viewer.val");
  await expect(page.getByTestId("expert-rules-defaults")).toBeDisabled();
  await page.goto("/rules");
  await expect(page.getByTestId("rule-toggle-TIB-001")).toBeDisabled();
});
