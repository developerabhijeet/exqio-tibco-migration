import { expect, test } from "@playwright/test";
import { apiUrl } from "../scripts/root-env";
import { actAs } from "./helpers";

// Help & Support: the install guide, the Deploy-step secrets guide and the runbook render inside
// the app from files bundled at build time, and "About this install" is for admins only (in dev
// mode that is ops.sam, the one persona that also holds the admin role).
test.describe("help and support", () => {
  test("the headphone link opens the install guide with its eleven sections", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Help & Support" }).click();
    await expect(page).toHaveURL(/\/help\/install$/);
    const sections = page.getByTestId("guide").locator("h2");
    await expect(sections).toHaveCount(11);
    await expect(sections.first()).toContainText("Prerequisites checklist");
    await expect(sections.last()).toContainText("Troubleshooting");
  });

  test("/help redirects to the install guide", async ({ page }) => {
    await page.goto("/help");
    await expect(page).toHaveURL(/\/help\/install$/);
  });

  test("a markdown table renders as a table and code blocks have a copy button", async ({ page }) => {
    await page.goto("/help/install");
    await expect(page.getByTestId("guide").locator("table").first()).toBeVisible();
    await expect(page.getByTestId("guide").getByRole("button", { name: "Copy" }).first()).toBeVisible();
  });

  test("the other guides and the troubleshooting extract open from the left nav", async ({ page }) => {
    await page.goto("/help/install");
    const nav = page.getByTestId("help-nav");
    await nav.getByRole("link", { name: "Configure Deploy-step secrets" }).click();
    await expect(page).toHaveURL(/\/help\/deploy-step-secrets$/);
    await expect(page.getByTestId("guide").locator("h1")).toBeVisible();
    await nav.getByRole("link", { name: "Runbook" }).click();
    await expect(page).toHaveURL(/\/help\/runbook$/);
    await nav.getByRole("link", { name: "Troubleshooting" }).click();
    await expect(page).toHaveURL(/\/help\/troubleshooting$/);
    await expect(page.getByTestId("guide").locator("h2, h1").first()).toContainText("Troubleshooting");
  });

  test("About this install is hidden for a non-admin persona", async ({ page }) => {
    await page.goto("/help/install");
    await actAs(page, "sme.vicky");
    await expect(page.getByTestId("help-nav").getByRole("link", { name: "Install on GMF" })).toBeVisible();
    await expect(page.getByTestId("help-nav").getByRole("link", { name: "About this install" })).toHaveCount(0);
  });

  test("About this install shows the status for the admin persona", async ({ page }) => {
    await page.goto("/help/install");
    await actAs(page, "ops.sam");
    await page.getByTestId("help-nav").getByRole("link", { name: "About this install" }).click();
    await expect(page).toHaveURL(/\/help\/about$/);
    const about = page.getByTestId("about-install");
    await expect(about).toContainText("Version");
    await expect(about).toContainText("dev");
    await expect(about.getByTestId("check-postgres")).toHaveText("ok");
  });

  test("nothing leaves the web origin or the API while browsing help", async ({ page, baseURL }) => {
    // In development the API is its own origin; in the image it is /api on the web origin.
    const foreign: string[] = [];
    page.on("request", (r) => {
      const u = r.url();
      if (u.startsWith("data:") || u.startsWith("blob:")) return;
      if (u.startsWith(baseURL!) || u.startsWith(apiUrl)) return;
      foreign.push(u);
    });
    await page.goto("/help/install");
    await page.goto("/help/deploy-step-secrets");
    await page.goto("/help/runbook");
    await page.goto("/help/troubleshooting");
    await page.waitForLoadState("networkidle");
    expect(foreign).toEqual([]);
  });
});
