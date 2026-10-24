import { expect, test, type Page } from "@playwright/test";
import { apiUrl } from "../scripts/root-env";

// AUTH_MODE=oidc as the web sees it. The e2e API runs in dev mode (it answers /auth/me with 404),
// so these tests stub /auth/me to play an API in oidc mode: a 200 there is how the web knows it is
// signed in for real, and a 401 anywhere afterwards sends the browser to the API's sign-in.

const PAT = { oid: "oid-9", upn: "pat@gmf.example", name: "Pat Lee", roles: ["engineer", "qa"] };

/** Stub /auth/me (200 until `expire()` is called, then 401) and the API's sign-in route. */
async function signedIn(page: Page, principal = PAT) {
  const state = { expired: false };
  const unauthorized = { status: 401, contentType: "application/json", body: JSON.stringify({ detail: "sign in required" }) };
  // Registered first, so it runs last: once expired, every other API call is refused too.
  await page.route(
    (url) => url.href.startsWith(apiUrl),
    (route) => (state.expired ? route.fulfill(unauthorized) : route.fallback()),
  );
  await page.route("**/auth/me", (route) =>
    state.expired
      ? route.fulfill(unauthorized)
      : route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(principal) }),
  );
  await page.route("**/auth/login**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Entra sign-in</h1>" }),
  );
  return { expire: () => (state.expired = true) };
}

test.describe("sign-in (oidc)", () => {
  test("the user menu shows the signed-in name and roles, with no persona switcher", async ({ page }) => {
    await signedIn(page);
    await page.goto("/");
    const menu = page.getByTestId("user-menu");
    await expect(menu).toContainText("Pat Lee");
    await menu.click();
    await expect(page.getByTestId("user-menu-identity")).toContainText("pat@gmf.example");
    await expect(page.getByTestId("user-menu-identity")).toContainText("engineer");
    await expect(page.getByTestId("user-menu-identity")).toContainText("qa");
    await expect(page.getByRole("menuitemradio")).toHaveCount(0);
    await expect(page.getByTestId("persona-ops.sam")).toHaveCount(0);
  });

  test("permissions are the union of the user's roles", async ({ page }) => {
    await signedIn(page);
    await page.goto("/");
    await expect(page.getByTestId("user-menu")).toContainText("Pat Lee");
    // engineer joins at build, qa at verify: the union acts from build onwards
    for (const key of ["upload", "discover", "validate", "design"]) {
      await expect(page.getByTestId(`step-${key}`)).toHaveAttribute("data-view-only", "true");
    }
    for (const key of ["build", "verify", "deploy"]) {
      await expect(page.getByTestId(`step-${key}`)).not.toHaveAttribute("data-view-only", "true");
    }
  });

  test("a role the screens do not know acts nowhere", async ({ page }) => {
    await signedIn(page, { ...PAT, roles: ["viewer"] });
    await page.goto("/");
    await expect(page.getByTestId("user-menu")).toContainText("Pat Lee");
    for (const key of ["upload", "build", "deploy"]) {
      await expect(page.getByTestId(`step-${key}`)).toHaveAttribute("data-view-only", "true");
    }
  });

  test("a 401 mid-session sends the next API call to the sign-in, with the page to return to", async ({ page }) => {
    const session = await signedIn(page);
    await page.goto("/?step=upload");
    await expect(page.getByTestId("user-menu")).toContainText("Pat Lee");
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByLabel("Project name").fill(`auth ${Date.now()}`);
    session.expire(); // the session runs out while the page is open
    await page.getByRole("button", { name: "Create" }).click();
    await page.waitForURL(/\/auth\/login\?return=/, { waitUntil: "commit" });
    const url = new URL(page.url());
    expect(url.pathname.endsWith("/auth/login")).toBe(true);
    expect(url.searchParams.get("return")).toBe("/");
  });

  test("a job stream refused mid-session sends the browser to the sign-in", async ({ page }) => {
    const session = await signedIn(page);
    // a scan this browser was watching when it was last open, re-attached on load
    await page.addInitScript(() => {
      window.localStorage.setItem("exiqo.project", "p-auth");
      window.localStorage.setItem("exiqo.scan.p-auth", "j-auth");
    });
    // the project stays the open one (an unknown project would be swapped for the first listed)
    await page.route("**/projects", (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([{ id: "p-auth", name: "auth", created_at: new Date().toISOString() }]),
          })
        : route.fallback(),
    );
    await page.route("**/jobs/j-auth", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "j-auth", kind: "scan", status: "running", result: null, error: null }),
      }),
    );
    await page.route("**/jobs/j-auth/events", (route) => {
      session.expire(); // the session ran out while the stream was open
      return route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
    });
    await page.goto("/?step=upload");
    await page.waitForURL(/\/auth\/login\?return=/, { waitUntil: "commit" });
  });
});

test.describe("sign-in (dev)", () => {
  test("without /auth/me the persona switcher stays", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("user-menu").click();
    await expect(page.getByRole("menuitemradio")).toHaveCount(5);
    await expect(page.getByTestId("user-menu-identity")).toHaveCount(0);
  });
});
