import { rmSync } from "node:fs";

/** Remove the connector fakes' token directory when playwright.config.ts created it for this run
 * (not one passed in as E2E_FAKE_TOKENS). */
export default function globalTeardown(): void {
  const dir = process.env.E2E_FAKE_TOKENS;
  if (dir && process.env.E2E_FAKE_TOKENS_CREATED === "1") rmSync(dir, { recursive: true, force: true });
}
