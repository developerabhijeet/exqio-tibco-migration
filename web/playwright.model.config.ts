import { defineConfig } from "@playwright/test";

// The specs that test pure modules (no page, no API): run without the e2e servers or global setup,
// with `npm run test:model`. `make e2e` still runs them with everything else.
export default defineConfig({
  testDir: "./e2e",
  testMatch: /citations-model\.spec\.ts$/,
  reporter: [["list"]],
});
