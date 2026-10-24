import { expect, test } from "@playwright/test";
import { actAs } from "./helpers";

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
