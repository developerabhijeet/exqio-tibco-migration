import { execSync } from "node:child_process";
import { join } from "node:path";

export const SAMPLE_ZIP = join(__dirname, "..", "..", "samples", "synthetic-gmf.zip");

/** Rebuild the sample zip from samples/synthetic-gmf on every run so the tests never upload a stale estate. */
export default function globalSetup(): void {
  execSync("make samples-zip", { cwd: join(__dirname, "..", ".."), stdio: "inherit" });
}
