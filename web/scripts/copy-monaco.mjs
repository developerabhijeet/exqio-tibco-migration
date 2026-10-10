// Copies Monaco's AMD build into public/ so the editor loads from this app, not a CDN.
// Runs on postinstall, predev and prebuild; skips the copy when the version already matches.
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = join(root, "node_modules", "monaco-editor", "package.json");
const src = join(root, "node_modules", "monaco-editor", "min", "vs");
const dest = join(root, "public", "monaco", "vs");
const marker = join(root, "public", "monaco", ".version");

if (!existsSync(src)) {
  console.warn("monaco-editor is not installed; run npm install. The XML viewer will show an error.");
  process.exit(0);
}
const version = JSON.parse(readFileSync(pkg, "utf8")).version;
if (existsSync(marker) && existsSync(join(dest, "loader.js")) && readFileSync(marker, "utf8") === version) {
  process.exit(0);
}
rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
writeFileSync(marker, version);
console.log(`monaco ${version} copied to public/monaco/vs`);
