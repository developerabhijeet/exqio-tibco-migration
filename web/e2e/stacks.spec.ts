import { expect, test } from "@playwright/test";
import { actAs, gotoStep, newProject, openProject } from "./helpers";

const NAME = `stacks-e2e ${Date.now()}`;

test("architect picks Java at upload and sees it on Design and Build", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "architect.lee");
  await newProject(page, NAME);
  await page.getByTestId("stack-java").click();
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-checked", "true");
  await gotoStep(page, "design");
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
  await gotoStep(page, "build");
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
});

test("viewer sees the picker disabled", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "viewer.val");
  await openProject(page, NAME);
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-disabled", "true");
});

test("admin sets the Settings default", async ({ page }) => {
  await page.goto("/settings");
  await actAs(page, "ops.sam");
  await page.getByTestId("default-stack").selectOption("dotnet");
  await expect(page.getByTestId("stacks-panel")).toContainText("default");
});
