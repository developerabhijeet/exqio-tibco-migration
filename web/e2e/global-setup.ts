import { execSync } from "node:child_process";
import { join } from "node:path";

export const SAMPLE_ZIP = join(__dirname, "..", "..", "samples", "synthetic-gmf.zip");

/** Regenerate the synthetic estate and rebuild its zip on every run (both are cheap), so the
 * tests can never upload a stale estate. */
export default function globalSetup(): void {
  const root = join(__dirname, "..", "..");
  execSync("make samples", { cwd: root, stdio: "inherit" });
  execSync("make samples-zip", { cwd: root, stdio: "inherit" });
}
