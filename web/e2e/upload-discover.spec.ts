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
  // Machine-derived steps read "complete"; green "approved" is kept for gate records.
  await expect(page.getByTestId("step-upload")).not.toContainText("approved");
  await expect(page.getByTestId("step-discover")).toContainText("complete");
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

  // The viewer lands on the line holding the step's SQL, inside the highlighted element.
  await expect(viewer).toHaveAttribute("data-hl-focus-text", /\blike\b/);
  const [start, end, focus] = await Promise.all(
    ["data-hl-start", "data-hl-end", "data-hl-focus"].map(async (a) => Number(await viewer.getAttribute(a))),
  );
  expect(focus).toBeGreaterThan(start);
  expect(focus).toBeLessThanOrEqual(end);
  // That line is revealed and carries the focus decoration.
  const focusOverlay = viewer.locator(".view-overlays .xml-hl-focus");
  await expect(focusOverlay).toBeVisible();
  const focusedText = await viewer.evaluate((el) => {
    const top = (el.querySelector(".view-overlays .xml-hl-focus")?.parentElement as HTMLElement | null)?.style.top;
    const line = Array.from(el.querySelectorAll<HTMLElement>(".view-lines .view-line")).find((l) => l.style.top === top);
    return line?.textContent ?? "";
  });
  expect(focusedText).toMatch(/\blike\b/);
  await expect(page.getByTestId("inspector")).toContainText(`lines ${start} to ${end}`);
});
