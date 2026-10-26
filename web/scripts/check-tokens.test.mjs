// Unit tests for the token guard's line rules: `node --test scripts/` (npm run test:scripts).
// Importing check-tokens.mjs checks nothing; only the exported rule helpers run.
import assert from "node:assert/strict";
import { test } from "node:test";
import { hexInClasses } from "./check-tokens.mjs";

test("rule 3 finds a hex in an arbitrary class, also inside a Tailwind _-joined value", () => {
  assert.equal(hexInClasses('className="bg-[#E9E6DE]"'), "bg-[#E9E6DE]");
  assert.equal(
    hexInClasses('className="bg-[radial-gradient(circle,_#E9E6DE_1px,_transparent_1px)] [background-size:18px_18px]"'),
    "#E9E6DE",
  );
  assert.equal(hexInClasses('style={{ color: "#425466" }}'), "#425466");
});

test("rule 3 leaves token colours and non-class lines alone", () => {
  assert.equal(hexInClasses('className="bg-[radial-gradient(circle,_rgb(var(--c-line))_1px,_transparent_1px)]"'), null);
  assert.equal(hexInClasses('const id = "#section-1";'), null);
});
