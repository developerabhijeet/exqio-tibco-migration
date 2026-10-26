import { expect, test, type Locator, type Page } from "@playwright/test";
import { SAMPLE_ZIP } from "./global-setup";
import { actAs, gotoStep, SAMPLE_PROCESS_NAME } from "./helpers";

// Visual baselines (spec section 3, Task 11): the main screens in both themes at a desktop and a
// phone width, against the screenshots committed beside this file. Opt in: tagged @visual, which
// playwright.config.ts leaves out unless E2E_VISUAL=1 (`make e2e-visual`; UPDATE=1 rewrites the
// baselines). Screenshots are machine-sensitive (fonts, GPU), so the baselines are per platform.
//
// One walk through the flow per width; at each screen both themes are shot (the theme is the
// system one, `exiqo.theme` = system, so emulateMedia switches it). The ExiQoian panel is shot open
// over Design (its subtitle carries the project name, so it is masked).
//
// What varies between runs is masked: times, the job logs, anything marked data-mask, and the
// project name (each run creates its own project), the design hash and the domain graph (a force
// layout, different on each run).

const SIZES = [
  [1440, 1000],
  [390, 844],
] as const;
const THEMES = ["light", "dark"] as const;

function masks(page: Page): Locator[] {
  return [
    page.locator("time, [data-testid=job-log], [data-mask]"),
    // The project name on the main screen (each run makes its own project).
    page.getByRole("heading", { level: 1 }).first(),
    page.getByTestId("project-select"),
    page.getByTestId("design-hash"),
    // Cytoscape's force layout places the domain graph's nodes differently on each run.
    page.getByTestId("domain-graph"),
    // The ExiQoian panel's subtitle names the project.
    page.getByTestId("exiqoian-panel").locator("h2 + p"),
  ];
}

async function shoot(page: Page, screen: string, width: number, mask: Locator[] = masks(page)): Promise<void> {
  await page.mouse.move(0, 0);
  for (const theme of THEMES) {
    await page.emulateMedia({ colorScheme: theme });
    // System theme: no data-theme, the media query decides; the page must actually follow it.
    expect(await page.evaluate(() => window.matchMedia("(prefers-color-scheme: dark)").matches)).toBe(theme === "dark");
    await expect(page).toHaveScreenshot(`${screen}-${theme}-${width}.png`, { mask });
  }
}

test.describe("@visual", () => {
  for (const [width, height] of SIZES) {
    test(`main screens at ${width} px`, async ({ page }) => {
      test.setTimeout(420_000);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await actAs(page, "sme.vicky");

      // Upload, on an empty project.
      const name = `visual ${Date.now()}`;
      await page.getByRole("button", { name: "New project" }).click();
      await page.getByLabel("Project name").fill(name);
      await page.getByRole("button", { name: "Create" }).click();
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
      await expect(page.getByTestId("zip-input")).toBeAttached();
      await shoot(page, "upload", width);

      // Discover, on the Dealer Funding domain with its sample process open.
      await page.getByTestId("zip-input").setInputFiles(SAMPLE_ZIP);
      await expect(page.getByTestId("file-tree")).toContainText(".bwp");
      await page.getByRole("button", { name: "Scan" }).click();
      await expect(page.getByTestId("counter-processes")).toHaveText("11", { timeout: 60_000 });
      await gotoStep(page, "discover");
      await page.getByTestId("domain-select").click();
      await page.getByRole("option", { name: /Dealer Funding/ }).click();
      await page.getByTestId("process-list").getByRole("button", { name: new RegExp(SAMPLE_PROCESS_NAME) }).click();
      await expect(page.getByTestId("process-view")).toContainText(SAMPLE_PROCESS_NAME);
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, "discover", width);

      // Validate, at the first rule card.
      await gotoStep(page, "validate");
      const run = page.getByRole("button", { name: "Run analysis" });
      await run.click();
      await expect(page.getByTestId("analysis-log")).toContainText("citations verified", { timeout: 90_000 });
      await expect(run).toBeEnabled();
      const rules = page.getByTestId("rule-card");
      await rules.first().scrollIntoViewIfNeeded();
      await shoot(page, "validate", width);

      // Design, at its decisions (G1 and G2 approved first, the design generated as the architect).
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
      await actAs(page, "architect.lee");
      await gotoStep(page, "design");
      await page.getByRole("button", { name: "Generate design" }).click();
      await expect(page.getByTestId("design-hash")).toContainText(/[0-9a-f]{12}/, { timeout: 60_000 });
      await page.getByTestId("design-decision").first().scrollIntoViewIfNeeded();
      await shoot(page, "design", width);

      // The ExiQoian panel, open over Design with its first-visit intro and suggestions.
      await page.getByTestId("exiqoian-launcher").click();
      await expect(page.getByTestId("exiqoian-panel")).toBeVisible();
      // At 390 px the panel is a full sheet: the page's masks underneath would paint over it.
      const panelMasks = [page.getByTestId("exiqoian-panel").locator("h2 + p, time, [data-mask]")];
      await shoot(page, "exiqoian", width, width < 768 ? panelMasks : masks(page));
      await page.getByRole("button", { name: "Close ExiQoian" }).click();
      await expect(page.getByTestId("exiqoian-panel")).toBeHidden();

      // Help.
      await page.goto("/help");
      await expect(page.getByTestId("page-header").getByRole("heading", { level: 1 })).toBeVisible();
      await shoot(page, "help", width);
    });
  }
});
