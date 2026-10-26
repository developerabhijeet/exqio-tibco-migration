import { expect, test } from "@playwright/test";
import { actAs, newProjectAndScan } from "./helpers";

// The persona model on the stepper. No project is needed: the bar renders for every persona, and
// what changes with the persona is which steps are theirs to act in. Each persona joins the
// pipeline at one step and owns it and everything after it; the steps before stay open to read and
// are marked view only.
const STEPS = ["upload", "discover", "validate", "design", "build", "verify", "deploy"] as const;

const ENTRY: Record<string, number> = {
  "sme.vicky": 0,
  "architect.lee": 3,
  "engineer.ravi": 4,
  "qa.priya": 5,
  "ops.sam": 6,
};

test.describe("personas", () => {
  for (const [user, entry] of Object.entries(ENTRY)) {
    test(`${user} may act from ${STEPS[entry]} onwards and reads the steps before it`, async ({ page }) => {
      await page.goto("/");
      await actAs(page, user);

      for (const [i, key] of STEPS.entries()) {
        const step = page.getByTestId(`step-${key}`);
        if (i < entry) {
          await expect(step, `${key} should be view only for ${user}`).toHaveAttribute("data-view-only", "true");
          await expect(step).toContainText("view only");
          // Read-only, not unreachable: the persona can still open it.
          await expect(step).toBeEnabled();
        } else {
          await expect(step, `${key} should be actionable for ${user}`).not.toHaveAttribute("data-view-only", "true");
          await expect(step).not.toContainText("view only");
        }
      }
    });
  }

  test("viewer.val reads every step and may act in none", async ({ page }) => {
    await page.goto("/");
    await actAs(page, "viewer.val");
    for (const key of STEPS) {
      await expect(page.getByTestId(`step-${key}`), `${key} should be view only`).toHaveAttribute("data-view-only", "true");
    }
    await page.getByTestId("user-menu").click();
    await expect(page.getByTestId("persona-viewer.val")).toContainText("read only");
    await expect(page.getByRole("group", { name: "Act as" }).getByRole("menuitemradio")).toHaveCount(6);
  });

  test("the menu says what each persona owns, and switching changes what is view only", async ({ page }) => {
    await page.goto("/");

    await actAs(page, "ops.sam");
    await expect(page.getByTestId("step-build")).toHaveAttribute("data-view-only", "true");

    await page.getByTestId("user-menu").click();
    // Every persona's own gates are named where the persona is chosen, so the one rule that decides
    // who may approve what is visible at the point of switching.
    for (const [persona, gates] of [
      ["sme.vicky", "G1, G2"],
      ["architect.lee", "G3"],
      ["engineer.ravi", "G4"],
      ["qa.priya", "G5"],
      ["ops.sam", "G6"],
    ] as const) {
      await expect(page.getByTestId(`persona-${persona}`)).toContainText(gates);
    }
    await page.getByTestId("persona-engineer.ravi").click();

    await expect(page.getByTestId("user-menu")).toContainText("engineer.ravi");
    await expect(page.getByTestId("step-build")).not.toHaveAttribute("data-view-only", "true");
    await expect(page.getByTestId("step-design")).toHaveAttribute("data-view-only", "true");
  });
});

// The theme switch in the user menu: System by default (follows the OS), and an explicit choice is
// kept over a reload, applied before first paint.
test("theme follows the system and the choice persists", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(15, 11, 20)");
  await page.getByTestId("user-menu").click();
  await page.getByRole("menuitemradio", { name: "Light" }).click();
  await page.reload();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(250, 249, 247)");
  await page.getByTestId("user-menu").click();
  await expect(page.getByTestId("theme-light")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("theme-system")).toHaveAttribute("aria-checked", "false");
});

test("the stepper shows one status pill and works from the keyboard", async ({ page }) => {
  await page.goto("/");
  await newProjectAndScan(page);
  await expect(page.getByRole("navigation", { name: "Migration steps" }).getByTestId("step-status-pill")).toHaveCount(1);
  await page.getByTestId("step-discover").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("step-validate")).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByTestId("step-deploy")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(page.getByTestId("step-upload")).toBeFocused();
  await page.setViewportSize({ width: 900, height: 900 });
  await page.getByTestId("stepper-compact").click();
  await page.getByTestId("step-option-design").click();
  await expect(page.getByTestId("step-design")).toHaveAttribute("aria-current", "step");
  await expect(page.getByTestId("stepper-compact")).toContainText("Step 4 of 7 · Design");
});

test("the stepper never overflows at 1024 px or 1280 px, and the header compacts on scroll", async ({ page }) => {
  await page.goto("/");
  await newProjectAndScan(page);
  for (const width of [1024, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    const tablist = page.getByRole("navigation", { name: "Migration steps" });
    await expect(tablist).toBeVisible();
    expect(await tablist.evaluate((n) => n.scrollWidth <= n.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  const header = page.getByTestId("pinned-header");
  await expect(header).not.toHaveAttribute("data-compact", "true");
  await page.evaluate(() => {
    const filler = document.createElement("div");
    filler.style.height = "3000px";
    document.querySelector("main")?.appendChild(filler);
  });
  await page.mouse.move(600, 500);
  await page.mouse.wheel(0, 400);
  await expect(header).toHaveAttribute("data-compact", "true");
  await page.mouse.wheel(0, -2000);
  await expect(header).not.toHaveAttribute("data-compact", "true");
});
