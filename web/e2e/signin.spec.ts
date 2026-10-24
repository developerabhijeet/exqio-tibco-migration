import { expect, test } from "@playwright/test";
import { newProjectAndScan } from "./helpers";

// The sign-in route: the provider hand-off, the account chooser and the claims panel, and the two
// properties that matter more than any of it — that it issues nothing, and that it blocks nothing.

test.describe("sign-in", () => {
  test("the flow runs provider to claims and lands in the application as the chosen persona", async ({ page }) => {
    await page.goto("/settings");
    await page.getByTestId("identity-instance").fill("https://login.microsoftonline.com/");
    await page.getByTestId("identity-audience").fill("api://gmf-credit");
    await page.getByTestId("identity-save").click();

    await page.goto("/signin");
    const card = page.getByTestId("signin-card");
    await expect(card).toHaveAttribute("data-stage", "provider");
    await expect(card).toContainText("Sign in with Microsoft");
    // The application chrome is not drawn over a sign-in screen.
    await expect(page.getByTestId("user-menu")).toHaveCount(0);

    await page.getByTestId("signin-continue").click();
    const accounts = page.getByTestId("signin-accounts");
    await expect(accounts).toBeVisible();
    // The accounts sit on a domain that cannot be mistaken for a directory.
    await expect(accounts).toContainText("ravi.engineer@gmf.example");

    await page.getByTestId("signin-account-engineer.ravi").click();
    const done = page.getByTestId("signin-done");
    await expect(done).toBeVisible({ timeout: 10_000 });
    await expect(done).toContainText("ravi.engineer@gmf.example");
    await expect(done).toContainText("Engineer");
    // Nothing on the card looks like a credential.
    await expect(card).not.toContainText("Bearer");

    await page.getByTestId("signin-enter").click();
    // In the application, acting as the account that was chosen.
    await expect(page.getByTestId("user-menu")).toContainText("engineer.ravi");
    await page.getByTestId("user-menu").click();
    await expect(page.getByTestId("user-menu-session")).toContainText("ravi.engineer@gmf.example");
  });

  test("no token or secret is written to storage by signing in", async ({ page }) => {
    await page.goto("/signin");
    await page.getByTestId("signin-continue").click();
    await page.getByTestId("signin-account-qa.priya").click();
    await expect(page.getByTestId("signin-done")).toBeVisible({ timeout: 10_000 });

    const stored = await page.evaluate(() => window.localStorage.getItem("exiqo.session") ?? "");
    expect(stored).toContain("priya.qa@gmf.example");
    // A record of a walkthrough, not a credential.
    expect(stored.toLowerCase()).not.toContain("bearer");
    expect(stored.toLowerCase()).not.toContain("token");
    expect(stored.toLowerCase()).not.toContain("secret");
  });

  test("the application is reachable without signing in, and signing out returns to this screen", async ({ page }) => {
    // Straight to the wizard, with no session: nothing redirects to /signin.
    await page.goto("/");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("step-upload")).toBeVisible();

    await page.getByTestId("user-menu").click();
    await expect(page.getByTestId("user-menu-session")).toContainText("Not signed in");
    await page.getByTestId("user-menu-signin").click();
    await expect(page).toHaveURL(/\/signin$/);

    // Sign in, and signing out comes straight back here rather than leaving you in the
    // application with the menu saying you are not signed in.
    await page.getByTestId("signin-continue").click();
    await page.getByTestId("signin-account-ops.sam").click();
    await expect(page.getByTestId("signin-done")).toBeVisible({ timeout: 10_000 });
    await page.getByTestId("signin-enter").click();
    await page.getByTestId("user-menu").click();
    await page.getByTestId("user-menu-signout").click();
    await expect(page).toHaveURL(/\/signin$/);
    await expect(page.getByTestId("signin-card")).toHaveAttribute("data-stage", "provider");
    await expect(page.getByTestId("signin-existing")).toHaveCount(0);

    // And it is a return, not a lock: the wizard is still reachable with no session.
    await page.goto("/");
    await expect(page.getByTestId("step-upload")).toBeVisible();
  });

  test("signing in lands on Upload, whatever step the browser was last left on", async ({ page }) => {
    // A scanned project would otherwise open on Discover: the wizard starts at step 1 after a
    // sign-in, so a walkthrough never begins half way along.
    await page.goto("/");
    await newProjectAndScan(page);
    await page.getByTestId("step-verify").click();
    await expect(page.getByTestId("step-verify")).toHaveAttribute("aria-current", "step");

    await page.goto("/signin");
    await page.getByTestId("signin-continue").click();
    await page.getByTestId("signin-account-sme.vicky").click();
    await expect(page.getByTestId("signin-done")).toBeVisible({ timeout: 10_000 });
    await page.getByTestId("signin-enter").click();

    await expect(page.getByTestId("step-upload")).toHaveAttribute("aria-current", "step");
    await expect(page).toHaveURL(/\/$/);
  });

  test("the configured provider is the one the screen signs in with", async ({ page }) => {
    await page.goto("/settings");
    await page.getByTestId("identity-provider-okta").click();
    await page.getByTestId("identity-instance").fill("https://gmfinancial.okta.com/oauth2/default");
    await page.getByTestId("identity-save").click();

    await page.goto("/signin");
    await expect(page.getByTestId("signin-card")).toContainText("Sign in with Okta");
    await page.getByTestId("signin-continue").click();
    await expect(page.getByTestId("signin-card")).toContainText("Okta");
    await page.getByTestId("signin-account-architect.lee").click();
    await expect(page.getByTestId("signin-done")).toContainText("lee.architect@gmf.example", { timeout: 10_000 });
  });
});
