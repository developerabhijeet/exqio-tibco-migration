#!/usr/bin/env node
// Design-token guard, run by `npm run lint` after ESLint. Five rules (spec section 5):
//   1. no arbitrary font size class (`text-[13px]`) under components/, app/, lib/
//   2. no opaque `bg-white` (an opacity modifier such as `bg-white/10` is fine), no `shadow-xl`/`shadow-2xl`
//   3. no hex colour inside a className string or a style object (components/ui/BrandIcons.tsx exempt)
//   4. no `text-plum` / `text-purple` (renamed `text-heading` / `text-accent-ink`), and no
//      `text-purple-lt` / `text-warm` (a surface tint used as text)
//   5. every listed token pair reaches WCAG AA 4.5:1 in both themes, read from lib/theme.css (the
//      spec's thirteen plus text on filled, tinted and status surfaces)
// `--rules=1,5` runs a subset. Exits 1 listing `file:line rule n: detail`; prints
// `check-tokens: ok (<n> files)` on success. No dependencies: regular expressions and the WCAG formula.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIRS = ["components", "app", "lib"];
const EXTS = /\.(tsx?|css)$/;
const HEX_EXEMPT = new Set(["components/ui/BrandIcons.tsx"]);
const THEME = "lib/theme.css";
const PAIRS = [
  ["ink", "page"],
  ["ink", "surface"],
  ["heading", "surface"],
  ["slate", "surface"],
  ["slate", "page"],
  ["slate", "sunken"],
  ["accent-ink", "surface"],
  ["ai", "ai-bg"],
  ["gate", "gate-bg"],
  ["decision", "decision-bg"],
  ["error", "error-bg"],
  ["coral-dk", "page"],
  ["det", "det-bg"],
  // Text on filled and tinted surfaces, and the status colours as text on the page and on cards.
  ["white", "accent-strong"],
  ["white", "plum"],
  ["accent-ink", "accent-soft"],
  ["ink", "accent-soft"],
  ["heading", "accent-soft"],
  ["slate", "accent-soft"],
  ["ink", "highlight"],
  ["ink", "highlight-strong"],
  ["accent-ink", "page"],
  ["gate", "page"],
  ["gate", "surface"],
  ["error", "page"],
  ["error", "surface"],
  ["decision", "page"],
  ["decision", "surface"],
  ["ai", "surface"],
  ["coral-dk", "surface"],
  // A status colour filled under surface-coloured text (stepper steps, gate buttons, pills).
  ["surface", "gate"],
  ["surface", "error"],
  ["surface", "coral-dk"],
  // Inverted chips and selected toggles (rule ids, finding keys): page-coloured text on heading.
  ["page", "heading"],
];
// Colours that are not tokens but appear in the pairs.
const FIXED = { white: [255, 255, 255] };

const arg = process.argv.find((a) => a.startsWith("--rules="));
const rules = new Set(arg ? arg.slice("--rules=".length).split(",").map(Number) : [1, 2, 3, 4, 5]);

function walk(dir) {
  let out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out = out.concat(walk(p));
    else if (EXTS.test(name)) out.push(p);
  }
  return out;
}

// Not \b: Tailwind joins arbitrary values with _ (a word character), so `_#E9E6DE_1px` must match.
export const HEX = /#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])/;
const LINE_RULES = [
  { rule: 1, re: /(?<![\w-])text-\[\d+(?:\.\d+)?px\]/g, what: "arbitrary font size" },
  { rule: 2, re: /(?<![\w-])bg-white(?![\w/-])/g, what: "opaque bg-white" },
  { rule: 2, re: /(?<![\w-])shadow-(?:2xl|xl)(?![\w-])/g, what: "shadow-xl/2xl" },
  { rule: 4, re: /(?<![\w-])text-(?:plum|purple)(?![\w-])/g, what: "legacy text colour" },
  // A surface tint as text: no token means that; pick an ink (text-heading, text-slate, ...).
  { rule: 4, re: /(?<![\w-])text-(?:purple-lt|warm)(?![\w-])/g, what: "surface tint as text colour" },
];

/** Rule 3, line based: an arbitrary-value hex class anywhere, or a hex on a className or style line. */
export function hexInClasses(line) {
  if (/-\[#[0-9a-fA-F]{3,8}\]/.test(line)) return line.match(/[\w:-]*-\[#[0-9a-fA-F]{3,8}\]/)[0];
  if (/className\s*=|style\s*=\s*\{\{/.test(line) && HEX.test(line)) return line.match(HEX)[0];
  return null;
}

// Rule 5: the token blocks of theme.css.
function block(css, selector) {
  const at = css.indexOf(selector);
  if (at < 0) return null;
  const open = css.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return { body: css.slice(open + 1, i), start: open };
  }
  return null;
}

function tokens(css, b) {
  const map = new Map();
  if (!b) return map;
  for (const m of b.body.matchAll(/--c-([\w-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    const line = css.slice(0, b.start + m.index).split("\n").length;
    map.set(m[1], { rgb: [Number(m[2]), Number(m[3]), Number(m[4])], line });
  }
  return map;
}

function luminance([r, g, b]) {
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function main() {
  const problems = [];
  const files = DIRS.flatMap((d) => walk(join(ROOT, d)));
  for (const file of files) {
    const rel = relative(ROOT, file).split("\\").join("/");
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      for (const { rule, re, what } of LINE_RULES) {
        if (!rules.has(rule)) continue;
        for (const m of line.matchAll(re)) problems.push(`${rel}:${i + 1} rule ${rule}: ${what} ${m[0]}`);
      }
      if (rules.has(3) && !HEX_EXEMPT.has(rel) && !rel.endsWith(".css")) {
        const hit = hexInClasses(line);
        if (hit) problems.push(`${rel}:${i + 1} rule 3: hex colour in a class or style ${hit}`);
      }
    });
  }

  if (rules.has(5)) {
    const css = readFileSync(join(ROOT, THEME), "utf8");
    const themes = {
      light: tokens(css, block(css, ":root {")),
      dark: tokens(css, block(css, '[data-theme="dark"] {')),
      "system dark": tokens(css, block(css, ':root:not([data-theme="light"]) {')),
    };
    for (const [theme, map] of Object.entries(themes)) {
      for (const [fg, bg] of PAIRS) {
        const a = FIXED[fg] ? { rgb: FIXED[fg], line: 1 } : map.get(fg);
        const b = FIXED[bg] ? { rgb: FIXED[bg], line: 1 } : map.get(bg);
        if (!a || !b) {
          problems.push(`${THEME}:1 rule 5: missing token --c-${a ? bg : fg} (${theme})`);
          continue;
        }
        const ratio = contrast(a.rgb, b.rgb);
        if (ratio < 4.5) problems.push(`${THEME}:${a.line} rule 5: ${fg}/${bg} ${ratio.toFixed(2)}:1 under 4.5:1 (${theme})`);
      }
    }
    // The system-preference block must be the explicit dark theme, value for value.
    for (const [name, { rgb, line }] of themes.dark) {
      const sys = themes["system dark"].get(name);
      if (!sys || sys.rgb.join() !== rgb.join())
        problems.push(`${THEME}:${line} rule 5: --c-${name} differs between [data-theme="dark"] and the prefers-color-scheme block`);
    }
  }

  const unique = [...new Set(problems)];
  if (unique.length) {
    for (const p of unique) console.error(p);
    console.error(`check-tokens: ${unique.length} problem(s)`);
    process.exit(1);
  }
  console.log(`check-tokens: ok (${files.length} files)`);
}

// Run as a script (npm run lint); importing it (the unit tests) checks nothing.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
