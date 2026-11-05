// Unit tests for the token codemod: `node --test scripts/` (npm run test:scripts). Importing the
// module must not rewrite anything; only `transform` and `sizeStep` are exercised.
import assert from "node:assert/strict";
import { test } from "node:test";
import { sizeStep, transform } from "./codemod-tokens.mjs";

test("sizeStep: nearest step, ties upward, floor 11 px", () => {
  assert.equal(sizeStep(9.5), "2xs");
  assert.equal(sizeStep(11.5), "xs");
  assert.equal(sizeStep(12.5), "sm");
  assert.equal(sizeStep(13.5), "md");
  assert.equal(sizeStep(16), "lg");
  assert.equal(sizeStep(18), "xl");
  assert.equal(sizeStep(24), "3xl");
  assert.equal(sizeStep(40), "4xl");
});

test("arbitrary sizes, bg-white and legacy text colours move to tokens, variants kept", () => {
  const { text } = transform('className="text-[13.5px] hover:bg-white bg-white/10 text-plum text-purple-dk shadow-2xl"');
  assert.equal(text, 'className="text-md hover:bg-surface bg-white/10 text-heading text-accent-ink shadow-e3"');
});

test("legacy aliases: purple-lt, warm, line-dk on fills and borders; plum stays a brand fill", () => {
  const { text } = transform('className="bg-purple-lt border-warm ring-line-dk/40 bg-plum border-plum"');
  assert.equal(text, 'className="bg-accent-soft border-sunken ring-line-strong/40 bg-plum border-heading"');
});

test("text-purple-lt and text-warm are left for check-tokens to reject", () => {
  const src = 'className="text-purple-lt text-warm"';
  assert.equal(transform(src).text, src);
});

test("legacy CSS variables, except in lib/theme.css which defines them", () => {
  const src = "stroke: var(--purple); fill: var(--warm);";
  assert.equal(transform(src, "components/x.tsx").text, "stroke: rgb(var(--c-accent)); fill: rgb(var(--c-sunken));");
  assert.equal(transform(src, "lib/theme.css").text, src);
});

test("idempotent: a second pass changes nothing", () => {
  const once = transform('className="text-[12px] bg-white text-purple"').text;
  const again = transform(once);
  assert.equal(again.text, once);
  assert.equal(again.counts.reduce((a, b) => a + b, 0), 0);
});
