// Copies Monaco's AMD build into public/ so the editor loads from this app, not a CDN.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "node_modules", "monaco-editor", "min", "vs");
const dest = join(root, "public", "monaco", "vs");
if (existsSync(src)) {
  rmSync(dest, { recursive: true, force: true });
  cpSync(src, dest, { recursive: true });
  console.log("monaco copied to public/monaco/vs");
} else {
  console.warn("monaco-editor not installed; XML viewer will not load");
}
