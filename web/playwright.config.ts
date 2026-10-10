import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

// Chromium is preinstalled under PLAYWRIGHT_BROWSERS_PATH; use its binary when the pinned
// @playwright/test expects a different build.
const preinstalled = "/opt/pw-browsers/chromium";
const executablePath = process.env.PW_CHROMIUM ?? (existsSync(preinstalled) ? preinstalled : undefined);

// Ports: `make e2e` picks two free ports (E2E_API_PORT, E2E_WEB_PORT) so a server left running on
// 8000 or 3000 by something else is never mistaken for this run's. Playwright starts both servers
// and refuses a busy port instead of reusing whatever answers there; E2E_REUSE_SERVERS=1 reuses
// servers you started yourself (they must run this checkout, the fake provider and a migrated DB).
const apiPort = Number(process.env.E2E_API_PORT ?? 8000);
const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? `http://localhost:${apiPort}`;
const webPort = Number(process.env.E2E_WEB_PORT ?? 3000);
const reuse = process.env.E2E_REUSE_SERVERS === "1";

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
      command: `.venv/bin/uvicorn api.app.main:app --port ${apiPort}`,
      cwd: "..",
      url: `${apiUrl}/health`,
      reuseExistingServer: reuse,
      timeout: 60_000,
      env: {
        PROVIDER: process.env.PROVIDER ?? "fake",
        // Offline and deterministic: fastembed would try to download its model on first use.
        EMBEDDINGS_BACKEND: process.env.EMBEDDINGS_BACKEND ?? "hash",
        DATABASE_URL: process.env.DATABASE_URL ?? "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo",
        // The browser calls the API directly: allow this run's web origin.
        CORS_ORIGINS: process.env.CORS_ORIGINS ?? `http://localhost:${webPort}`,
        // The generated service inherits the API's environment: its Development CORS (default
        // http://localhost:3000) must allow this run's web origin for the Verify tab's dev token.
        Cors__Origins__0: process.env.Cors__Origins__0 ?? `http://localhost:${webPort}`,
      },
    },
    {
      command: process.env.E2E_WEB_CMD ?? `npm run dev -- -p ${webPort}`,
      url: `http://localhost:${webPort}`,
      reuseExistingServer: reuse,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_URL: apiUrl },
    },
  ],
});
