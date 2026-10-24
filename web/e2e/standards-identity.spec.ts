import { expect, test } from "@playwright/test";

// The two screens an architect and a security reviewer fill in before a migration is signed off:
// the standards the design has to fit, and the identity provider the generated service trusts.
// Neither needs a scanned estate, so these run on an empty project and stay fast.

type Page = import("@playwright/test").Page;

/** A project with nothing uploaded, then the Design step. */
async function designStepOfANewProject(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`e2e standards ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();
  await openDesign(page);
}

/**
 * A new project settles on Upload when its (absent) scan report comes back, which is after the
 * project itself is created, so a step chosen before that lands is put back to Upload. Retrying the
 * click is what a person does too, and it needs no fixed wait for a query this test does not own.
 */
async function openDesign(page: Page): Promise<void> {
  await expect(async () => {
    await page.getByTestId("step-design").click();
    await expect(page.getByTestId("design-tab")).toBeVisible({ timeout: 2_000 });
    // And it has to stay: that query can settle in the gap between the click landing and the next
    // line of the test, which puts the step back to Upload with the assertion already passed.
    await page.waitForTimeout(700);
    await expect(page.getByTestId("standards-panel")).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 30_000 });
}

test.describe("architectural standards", () => {
  test("an attached standard records the document, its kind and its digest", async ({ page }) => {
    await designStepOfANewProject(page);
    const panel = page.getByTestId("standards-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("No standards attached");

    await page.getByTestId("standards-kind").selectOption("blueprint");
    await page.getByTestId("standards-note").fill("Section 4: service layering");
    await page.getByTestId("standards-input").setInputFiles({
      name: "GMF-Reference-Architecture.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.7 reference architecture"),
    });

    const row = page.getByTestId("standard-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("GMF-Reference-Architecture.pdf");
    await expect(row).toContainText("Solution blueprint");
    await expect(row).toContainText("Section 4: service layering");
    // The digest is the point: the entry names a revision of a document, not a filename.
    await expect(row).toContainText(/sha256 [0-9a-f]{32}/);
    // And the panel never implies the design was generated against it.
    await expect(panel).toContainText("the design generator does not read them yet");

    // The same bytes twice is the same document, so it is refused rather than listed again.
    await page.getByTestId("standards-input").setInputFiles({
      name: "a-copy-under-another-name.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.7 reference architecture"),
    });
    await expect(page.getByTestId("standards-rejected")).toContainText("already attached");
    await expect(row).toHaveCount(1);
  });

  test("attached standards survive a reload and can be removed", async ({ page }) => {
    await designStepOfANewProject(page);
    await page.getByTestId("standards-input").setInputFiles({
      name: "api-standard.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# API standard"),
    });
    await expect(page.getByTestId("standard-row")).toHaveCount(1);

    await page.reload();
    await openDesign(page);
    await expect(page.getByTestId("standard-row")).toHaveCount(1);

    await page.getByRole("button", { name: "Remove api-standard.md" }).click();
    await expect(page.getByTestId("standards-panel")).toContainText("No standards attached");
  });
});

test.describe("authentication", () => {
  test("each field names the configuration key the generated service reads", async ({ page }) => {
    await page.goto("/settings");
    const panel = page.getByTestId("identity-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("identity-state")).toContainText("to fill");

    await page.getByTestId("identity-instance").fill("https://login.microsoftonline.com/");
    await page.getByTestId("identity-tenantId").fill("7a9c1f2e-0000-4d1a-b2c3-000000000001");
    await page.getByTestId("identity-clientId").fill("b31d8e40-0000-4a77-9f21-000000000002");
    await page.getByTestId("identity-audience").fill("api://gmf-credit");
    await expect(page.getByTestId("identity-state")).toHaveText("configured");

    // The keys on screen are the ones Program.cs reads, and the preview is what they produce.
    for (const key of ["Auth:EntraId:Instance", "Auth:EntraId:TenantId", "Auth:EntraId:ClientId",
                       "Auth:EntraId:Audience"]) {
      await expect(panel).toContainText(key);
    }
    const json = page.getByTestId("identity-appsettings");
    await expect(json).toContainText('"Mode": "EntraId"');
    await expect(json).toContainText('"Audience": "api://gmf-credit"');
    // No half-written template reaches the preview, where it could be copied into a real config.
    await expect(json).not.toContainText("<client-id>");
  });

  test("a secret is never written to storage", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByTestId("identity-panel")).toBeVisible();
    await page.getByTestId("identity-audience").fill("api://gmf-credit");
    await page.getByTestId("identity-clientSecret").fill("super-secret-value-not-to-be-stored");
    await page.getByTestId("identity-save").click();
    await expect(page.getByTestId("identity-save")).toContainText("Saved");

    const stored = await page.evaluate(() => window.localStorage.getItem("exiqo.identity") ?? "");
    expect(stored).toContain("api://gmf-credit");
    expect(stored).not.toContain("super-secret-value-not-to-be-stored");
    expect(stored).not.toContain("clientSecret");
  });

  test("switching to Okta rewrites the keys and the preview", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByTestId("identity-panel")).toBeVisible();
    await page.getByTestId("identity-provider-okta").click();
    await expect(page.getByTestId("identity-panel")).toContainText("Auth:Okta:Authority");
    await expect(page.getByTestId("identity-appsettings")).toContainText('"Mode": "Okta"');
    await expect(page.getByTestId("identity-panel")).not.toContainText("Auth:EntraId:TenantId");
  });

  test("the sign-in preview says it contacts no provider and issues no token", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByTestId("identity-panel")).toBeVisible();
    await page.getByTestId("identity-instance").fill("https://login.microsoftonline.com/");
    await page.getByTestId("identity-audience").fill("api://gmf-credit");
    await page.getByTestId("identity-preview").click();

    const dialog = page.getByTestId("sso-preview");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Microsoft Entra ID");
    await expect(dialog).toContainText("contacts no identity provider and issues no token");
    await page.getByTestId("sso-continue").click();
    await expect(dialog).toContainText("Signed in");
    await expect(dialog).toContainText("api://gmf-credit");
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toHaveCount(0);
  });
});
