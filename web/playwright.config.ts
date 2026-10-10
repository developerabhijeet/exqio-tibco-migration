import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

// Chromium is preinstalled under PLAYWRIGHT_BROWSERS_PATH; use its binary when the pinned
// @playwright/test expects a different build.
const preinstalled = "/opt/pw-browsers/chromium";
const executablePath = process.env.PW_CHROMIUM ?? (existsSync(preinstalled) ? preinstalled : undefined);

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const webPort = Number(process.env.E2E_WEB_PORT ?? 3000);

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${webPort}`,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      // The API with the fake provider; DATABASE_URL must point at a migrated database (make db).
      command: ".venv/bin/uvicorn api.app.main:app --port 8000",
      cwd: "..",
      url: `${apiUrl}/health`,
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        PROVIDER: process.env.PROVIDER ?? "fake",
        // Offline and deterministic: fastembed would try to download its model on first use.
        EMBEDDINGS_BACKEND: process.env.EMBEDDINGS_BACKEND ?? "hash",
        DATABASE_URL: process.env.DATABASE_URL ?? "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo",
      },
    },
    {
      command: process.env.E2E_WEB_CMD ?? `npx next dev -p ${webPort}`,
      url: `http://localhost:${webPort}`,
      reuseExistingServer: true,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_URL: apiUrl },
    },
  ],
});
