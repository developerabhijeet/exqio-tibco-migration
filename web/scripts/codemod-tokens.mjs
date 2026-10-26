#!/usr/bin/env node
// Codemod to the design tokens (spec section 3; plan Task 6). Rewrites the class names that
// scripts/check-tokens.mjs rejects, and the legacy colour aliases, in every .ts/.tsx/.css file
// under components/, app/ and lib/ (or the paths given as arguments). Idempotent: a second run
// changes nothing and prints zero for every rule. Re-run it at integration over components other
// branches add (cross ruling X5).
//
//   node scripts/codemod-tokens.mjs            rewrite in place, print a count per rule
//   node scripts/codemod-tokens.mjs --dry-run  print the counts only
//   node scripts/codemod-tokens.mjs app/x      limit to some files or folders (relative to web/)
//
// What it cannot do is left to check-tokens: hex colours in style objects and chart or canvas
// code, and class names assembled from fragments at run time.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIRS = ["components", "app", "lib"];
const EXTS = /\.(tsx?|css)$/;

// The type scale (tailwind.config.ts fontSize) in px, smallest first.
const SCALE = [
  ["2xs", 11],
  ["xs", 12],
  ["sm", 13],
  ["md", 14],
  ["base", 15],
  ["lg", 17],
  ["xl", 19],
  ["2xl", 22],
  ["3xl", 26],
  ["4xl", 30],
];

/** The nearest step to `px`; a tie goes to the larger step; nothing is smaller than the first. */
export function sizeStep(px) {
  let best = SCALE[0];
  for (const step of SCALE) {
    const d = Math.abs(step[1] - px);
    const bd = Math.abs(best[1] - px);
    if (d < bd || (d === bd && step[1] > best[1])) best = step;
  }
  return best[0];
}

// A class boundary: not preceded by a word character or a dash, so `hover:text-plum` and
// `!text-plum` match while `xtext-plum` does not.
const B = "(?<![\\w-])";
const E = "(?![\\w-])";
const UTIL = "(bg|border|ring|ring-offset|fill|stroke|outline|divide|from|via|to|decoration|caret|accent|shadow|placeholder)";

const THEME_CSS = "lib/theme.css";
const LEGACY_VAR = { plum: "heading", purple: "accent", "purple-lt": "accent-soft", warm: "sunken", "line-dk": "line-strong" };

const RULES = [
  { name: "text-[<n>px] -> type scale", re: new RegExp(`${B}text-\\[(\\d+(?:\\.\\d+)?)px\\]`, "g"), to: (_, n) => `text-${sizeStep(Number(n))}` },
  { name: "bg-white -> bg-surface", re: new RegExp(`${B}bg-white(?![\\w/-])`, "g"), to: () => "bg-surface" },
  { name: "shadow-xl/2xl -> shadow-e3", re: new RegExp(`${B}shadow-(?:2xl|xl)${E}`, "g"), to: () => "shadow-e3" },
  { name: "text-plum -> text-heading", re: new RegExp(`${B}text-plum${E}`, "g"), to: () => "text-heading" },
  { name: "text-purple(-dk) -> text-accent-ink", re: new RegExp(`${B}text-purple(?:-dk)?${E}`, "g"), to: () => "text-accent-ink" },
  { name: "*-purple-lt -> *-accent-soft", re: new RegExp(`${B}(text|${UTIL.slice(1, -1)})-purple-lt${E}`, "g"), to: (_, u) => `${u}-accent-soft` },
  { name: "*-purple-dk -> *-accent-ink", re: new RegExp(`${B}${UTIL}-purple-dk${E}`, "g"), to: (_, u) => `${u}-accent-ink` },
  { name: "*-purple -> *-accent", re: new RegExp(`${B}${UTIL}-purple${E}`, "g"), to: (_, u) => `${u}-accent` },
  { name: "*-warm -> *-sunken (bg-warm)", re: new RegExp(`${B}(text|${UTIL.slice(1, -1)})-warm${E}`, "g"), to: (_, u) => `${u}-sunken` },
  { name: "*-line-dk -> *-line-strong", re: new RegExp(`${B}(text|${UTIL.slice(1, -1)})-line-dk${E}`, "g"), to: (_, u) => `${u}-line-strong` },
  // Plum stays the brand bar's fill (`bg-plum`, `bg-plum/NN` scrims); plum as ink or outline is
  // the heading colour, which turns light on a dark page.
  { name: "fill/border/ring/stroke-plum -> *-heading", re: new RegExp(`${B}(fill|border|ring|stroke|divide)-plum${E}`, "g"), to: (_, u) => `${u}-heading` },
  // White as a drawn surface (SVG boxes, a ring cut out of a card) is the surface colour.
  { name: "fill-white / ring-white -> *-surface", re: new RegExp(`${B}(fill|ring|ring-offset)-white(?![\\w/-])`, "g"), to: (_, u) => `${u}-surface` },
  // Inline SVG and style colours over the legacy full-colour variables (not in lib/theme.css,
  // which defines them): plum ink becomes the heading colour, as with `text-plum`.
  { name: "var(--plum|purple|purple-lt|warm|line-dk) -> rgb(var(--c-…))", skip: THEME_CSS, re: /var\(--(plum|purple-lt|purple|warm|line-dk)\)/g, to: (_, v) => `rgb(var(--c-${LEGACY_VAR[v]}))` },
  { name: 'SVG fill="white" -> surface', re: /\bfill="white"/g, to: () => 'fill="rgb(var(--c-surface))"' },
];

function walk(p) {
  if (!statSync(p).isDirectory()) return EXTS.test(p) ? [p] : [];
  return readdirSync(p).flatMap((name) => walk(join(p, name)));
}

const args = process.argv.slice(2);
const dry = args.includes("--dry-run");
const targets = args.filter((a) => !a.startsWith("--"));
const files = (targets.length ? targets : DIRS).flatMap((t) => walk(join(ROOT, t)));

const counts = RULES.map(() => 0);
let changedFiles = 0;
for (const file of files) {
  const before = readFileSync(file, "utf8");
  const rel = relative(ROOT, file).split("\\").join("/");
  let text = before;
  RULES.forEach((rule, i) => {
    if (rule.skip === rel) return;
    text = text.replace(rule.re, (...m) => {
      counts[i] += 1;
      return rule.to(...m);
    });
  });
  if (text !== before) {
    changedFiles += 1;
    if (!dry) writeFileSync(file, text);
    if (dry) console.log(`would change ${relative(ROOT, file)}`);
  }
}

RULES.forEach((rule, i) => console.log(`${String(counts[i]).padStart(5)}  ${rule.name}`));
const total = counts.reduce((a, b) => a + b, 0);
console.log(`codemod-tokens: ${total} replacement(s) in ${changedFiles} file(s) of ${files.length}${dry ? " (dry run)" : ""}`);
