// Bundles the deploy guides into the app so Help & Support renders them offline, from the image
// itself: docs/deploy/*.md is copied to content/help/ (git-ignored) under the slugs the /help
// routes use, and the "Troubleshooting" section of the install guide is also written out as its
// own page. Runs on predev and prebuild. In the web image the docs sit at /docs/deploy, one level
// above the app (see deploy/docker/web.Dockerfile), the same relative path as in the repository.
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "docs", "deploy");
const dest = join(root, "content", "help");

const GUIDES = {
  install: "GMF-INSTALL.md",
  "deploy-step-secrets": "DEPLOY-STEP-SECRETS.md",
  runbook: "RUNBOOK.md",
};

if (!existsSync(src)) {
  // A build without the guides would ship a Help screen with nothing in it: fail instead.
  console.error(`copy-guides: ${src} not found; the help guides cannot be bundled.`);
  process.exit(1);
}

rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });
for (const [slug, file] of Object.entries(GUIDES)) {
  const text = readFileSync(join(src, file), "utf8");
  writeFileSync(join(dest, `${slug}.md`), text);
  if (slug !== "install") continue;
  // From "## <n>. Troubleshooting" up to the next level-two heading (or the end of the file).
  const lines = text.split("\n");
  const start = lines.findIndex((l) => /^## (\d+\.\s*)?Troubleshooting\s*$/.test(l));
  if (start < 0) {
    console.error("copy-guides: GMF-INSTALL.md has no Troubleshooting section.");
    process.exit(1);
  }
  let end = lines.findIndex((l, i) => i > start && l.startsWith("## "));
  if (end < 0) end = lines.length;
  const body = lines.slice(start + 1, end).join("\n").trim();
  writeFileSync(join(dest, "troubleshooting.md"), `# Troubleshooting\n\n${body}\n`);
}
console.log(`help guides copied to content/help (${Object.keys(GUIDES).length} guides + troubleshooting)`);
