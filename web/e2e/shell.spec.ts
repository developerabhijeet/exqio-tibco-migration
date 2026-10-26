import { expect, test } from "@playwright/test";

// The application shell (spec section 4): a centred content column up to 1600 px, the secondary
// links folded into "More" below 1280 px, the project selector on a second bar row below 768 px,
// and no horizontal page scroll down to 390 px.
test.describe("app shell", () => {
  test("the content column is centred and capped at 1600 px on a wide window", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1000 });
    await page.goto("/settings");
    const header = page.getByTestId("page-header");
    await expect(header.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    const box = (await header.boundingBox())!;
    // 1600 px column less the 40 px gutters either side, centred in the window.
    expect(box.width).toBeLessThanOrEqual(1600 - 80);
    expect(Math.abs(box.x - (1920 - box.width) / 2)).toBeLessThanOrEqual(1);
  });

  test("below 1280 px the secondary links live in the More menu, Memory among them", async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Secondary" })).toBeHidden();
    const more = page.getByTestId("topbar-more");
    await expect(more).toHaveAccessibleName("More");
    await more.click();
    await expect(more).toHaveAttribute("aria-expanded", "true");
    const menu = page.locator("#topbar-more-menu");
    for (const name of ["Settings", "History", "Memory", "Tokenomics", "Readiness", "Help & Support"]) {
      await expect(menu.getByRole("link", { name })).toBeVisible();
    }
    // Escape closes it and hands focus back to the button.
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(more).toBeFocused();
    // ArrowDown opens it on the first link; following a link closes it.
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("link", { name: "Settings" })).toBeFocused();
    await menu.getByRole("link", { name: "Readiness" }).click();
    await expect(page).toHaveURL(/\/readiness$/);
    await expect(menu).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1, name: "Readiness" })).toBeVisible();
  });

  test("at 1440 px the links sit in the bar and More is hidden", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Secondary" }).getByRole("link", { name: "Memory" })).toBeVisible();
    await expect(page.getByTestId("topbar-more")).toBeHidden();
  });

  for (const path of ["/", "/settings", "/history", "/readiness", "/help/install"]) {
    test(`390 px: ${path} has no horizontal page scroll and the project selector has its own row`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(path);
      await expect(page.getByTestId("project-select")).toBeVisible();
      const firstRow = (await page.getByTestId("user-menu").boundingBox())!;
      const select = (await page.getByTestId("project-select").boundingBox())!;
      expect(select.y).toBeGreaterThanOrEqual(firstRow.y + firstRow.height);
      const overflow = await page.evaluate(() => {
        const el = document.scrollingElement!;
        const main = document.querySelector("main")!;
        return { page: el.scrollWidth - el.clientWidth, main: main.scrollWidth - main.clientWidth };
      });
      expect(overflow.page).toBeLessThanOrEqual(0);
      expect(overflow.main).toBeLessThanOrEqual(0);
    });
  }
});
