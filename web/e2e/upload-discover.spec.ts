import { expect, test } from "@playwright/test";
import { join } from "node:path";

const SAMPLE_ZIP = join(__dirname, "..", "..", "samples", "synthetic-gmf.zip");

test("upload, scan, discover a domain and trace a step to its XML", async ({ page }) => {
  await page.goto("/");

  // A fresh project so the run does not depend on earlier data.
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill(`e2e ${Date.now()}`);
  await page.getByRole("button", { name: "Create" }).click();

  // Upload tab: zip, file tree, scan, streamed progress, parse report.
  await page.getByTestId("zip-input").setInputFiles(SAMPLE_ZIP);
  await expect(page.getByTestId("file-tree")).toContainText(".bwp");
  await page.getByRole("button", { name: "Scan" }).click();
  await expect(page.getByTestId("scan-progress")).toContainText("stored 2 applications");
  await expect(page.getByTestId("counter-processes")).toHaveText("11");
  await expect(page.getByTestId("counter-unknown-types")).toHaveText("0");
  await expect(page.getByTestId("synthetic-banner")).toBeVisible();
  await expect(page.getByTestId("parse-report").getByRole("row")).toHaveCount(12); // header + 11

  // Discover tab: domain, process, step.
  await page.getByRole("button", { name: /^Discover/ }).click();
  await page.getByTestId("domain-select").click();
  await page.getByRole("option", { name: /Dealer Funding/ }).click();
  await expect(page.getByTestId("domain-select")).toContainText("Dealer Funding");
  await page.getByTestId("process-list").getByRole("button", { name: /DealerFundingRequest/ }).click();

  const node = page.locator('.react-flow__node[data-id="LookupDealer"]');
  await expect(node).toBeVisible();
  await expect(node).toContainText("bw.jdbc.JDBCQuery");
  await node.click();

  await page.getByRole("tab", { name: "XML" }).click();
  const viewer = page.getByTestId("xml-viewer");
  await expect(viewer.locator(".xml-hl-line").first()).toBeVisible();

  // Scroll through the highlighted element until a highlighted line containing "like" is rendered.
  await viewer.locator(".monaco-editor").hover();
  let found = false;
  for (let i = 0; i < 30 && !found; i++) {
    found = await viewer.evaluate((el) => {
      const lit = new Set(
        Array.from(el.querySelectorAll(".view-overlays .xml-hl-line")).map(
          (d) => (d.parentElement as HTMLElement).style.top,
        ),
      );
      return Array.from(el.querySelectorAll<HTMLElement>(".view-lines .view-line")).some(
        (l) => /\blike\b/.test(l.textContent ?? "") && lit.has(l.style.top),
      );
    });
    if (!found) await page.mouse.wheel(0, 120);
  }
  expect(found).toBe(true);
});
