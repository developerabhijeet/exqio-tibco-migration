import { expect, test } from "@playwright/test";
import { actAs, gotoStep, newProject, openDealerProcess, SAMPLE_PROCESS_NAME, uploadSample } from "./helpers";

test("architect picks Java at upload with the mouse and the keyboard; Design and Build show it", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "architect.lee");
  await newProject(page, `stacks pick ${Date.now()}`);
  await page.getByTestId("stack-java").click();
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-checked", "true");
  // One tab stop; the arrow keys move and select.
  await expect(page.getByTestId("stack-java")).toHaveAttribute("tabindex", "0");
  await expect(page.getByTestId("stack-dotnet")).toHaveAttribute("tabindex", "-1");
  await page.getByTestId("stack-java").focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("stack-dotnet")).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-checked", "true");
  await gotoStep(page, "design");
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
  await gotoStep(page, "build");
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
});

test("a viewer sees the picker disabled with the reason as text", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "architect.lee");
  await newProject(page, `stacks viewer ${Date.now()}`);
  await actAs(page, "viewer.val");
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByTestId("stack-picker-reason")).toContainText("needs the architect or admin role");
});

test("the server's refusal of a stack change is shown", async ({ page }) => {
  await page.goto("/");
  await actAs(page, "architect.lee");
  await newProject(page, `stacks refuse ${Date.now()}`);
  await page.route("**/projects/*", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "java is not available: not enabled on this install (stacks.enabled)" }) });
  });
  await page.getByTestId("stack-java").click();
  await expect(page.getByText("java is not available: not enabled on this install")).toBeVisible();
  await expect(page.getByTestId("stack-dotnet")).toHaveAttribute("aria-checked", "true");
});

test("only an admin sets the Settings default", async ({ page }) => {
  await page.goto("/settings");
  await actAs(page, "architect.lee");
  await expect(page.getByTestId("default-stack")).toBeDisabled();
  await actAs(page, "ops.sam");
  await expect(page.getByTestId("default-stack")).toBeEnabled();
  await page.getByTestId("default-stack").selectOption("java");
  await expect(page.locator('[data-stack="java"]')).toContainText("default");
  await page.getByTestId("default-stack").selectOption("dotnet");
  await expect(page.locator('[data-stack="dotnet"]')).toContainText("default");
  await expect(page.locator('[data-stack="java"]')).not.toContainText("default");
});

test("badges follow the record, and a stack change says the design is for the old stack", async ({ page }) => {
  await uploadSample(page);
  await actAs(page, "sme.vicky");
  await actAs(page, "architect.lee");
  await page.getByTestId("step-upload").click();
  await page.getByTestId("stack-java").click();
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-checked", "true");
  await openDealerProcess(page, SAMPLE_PROCESS_NAME);
  await page.getByTestId("step-design").click();
  await page.getByRole("button", { name: "Generate design" }).click();
  await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/, { timeout: 60_000 });
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
  await expect(page.getByTestId("stack-note")).toHaveCount(0);

  await page.getByTestId("step-upload").click();
  await page.getByTestId("stack-dotnet").click();
  await expect(page.getByTestId("stack-dotnet")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("stack-change-note")).toContainText("Re-run Design");
  await page.getByTestId("step-design").click();
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
  await expect(page.getByTestId("stack-note")).toContainText("Designed for Java 21; the project now targets C#");
});
