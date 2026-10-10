import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

export const SAMPLE_ZIP = join(__dirname, "..", "..", "samples", "synthetic-gmf.zip");

export default function globalSetup(): void {
  if (!existsSync(SAMPLE_ZIP)) {
    execSync("make samples-zip", { cwd: join(__dirname, "..", ".."), stdio: "inherit" });
  }
}
