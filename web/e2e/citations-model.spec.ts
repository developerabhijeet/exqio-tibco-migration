import { expect, test } from "@playwright/test";
import { codePointLength, segments, sliceCodePoints, type CiteRef, type CitedText } from "../lib/citations";

// The shared cited-text model, without a browser: span offsets from the server are code points
// (Python indices), so an emoji or other astral character before a span must not shift it.

function ref(id: string, phase: CiteRef["phase"] = "validate"): CiteRef {
  return { id, phase, kind: "rule", label: `Rule ${id}`, preview: "", target: { kind: "step", step: phase } };
}

/** Code-point offsets of `part` in `text`, as engine/citations computes them. */
function cp(text: string, part: string): [number, number] {
  const start = codePointLength(text.slice(0, text.indexOf(part)));
  return [start, start + codePointLength(part)];
}

test("sliceCodePoints slices like Python, not by UTF-16 units", () => {
  const text = "😀 Dealer must be ACTIVE.";
  expect(text.length).toBe(25); // the emoji is two UTF-16 units
  expect(codePointLength(text)).toBe(24);
  expect(sliceCodePoints(text, 2, 8)).toBe("Dealer");
  expect(sliceCodePoints(text, 0, 1)).toBe("😀");
  expect(sliceCodePoints("𠀀𠀁 漢字", 1, 2)).toBe("𠀁");
});

test("an emoji before a cited sentence leaves its span on that sentence", () => {
  const text = "🚗 Dealer must be ACTIVE. 👍🏽 Then fund the loan.";
  const [s1, e1] = cp(text, "Dealer must be ACTIVE");
  const [s2, e2] = cp(text, "Then fund the loan");
  // A UTF-16 slice at the same offsets would be off by one, then by three.
  expect(text.slice(s2, e2)).not.toBe("Then fund the loan");
  const ct: CitedText = {
    text,
    spans: [
      { start: s1, end: e1, refs: ["c1"] },
      { start: s2, end: e2, refs: ["c2", "c1"] },
    ],
    refs: [ref("c1"), ref("c2", "build")],
  };
  const parts = segments(ct);
  expect(parts.map((p) => p.text)).toEqual(["🚗 ", "Dealer must be ACTIVE", ". 👍🏽 ", "Then fund the loan", "."]);
  expect(parts.map((p) => p.text).join("")).toBe(text);
  expect(parts[1].refs.map((r) => r.id)).toEqual(["c1"]);
  expect(parts[3].refs.map((r) => r.id)).toEqual(["c2", "c1"]);
});

test("spans exactly as engine/citations from_markers emits them land on their sentences", () => {
  // from_markers("🚗 Dealer must be ACTIVE [S1]. 👍🏽 Then fund the loan [S2].") in Python; 👍🏽 is
  // two code points (four UTF-16 units).
  const ct: CitedText = {
    text: "🚗 Dealer must be ACTIVE. 👍🏽 Then fund the loan.",
    spans: [
      { start: 0, end: 23, refs: ["c1"] },
      { start: 25, end: 46, refs: ["c2"] },
    ],
    refs: [ref("c1"), ref("c2")],
  };
  expect(segments(ct).map((p) => [p.text, p.number])).toEqual([
    ["🚗 Dealer must be ACTIVE", [1]],
    [". ", []],
    ["👍🏽 Then fund the loan", [2]],
    [".", []],
  ]);
});

test("chip numbers follow first appearance, not ref id order", () => {
  const text = "Alpha. Beta. Gamma.";
  const ct: CitedText = {
    text,
    spans: [
      { start: 0, end: 5, refs: ["c3"] },
      { start: 7, end: 11, refs: ["c1", "c3"] },
      { start: 13, end: 18, refs: ["c2"] },
    ],
    refs: [ref("c1"), ref("c2"), ref("c3")],
  };
  const cited = segments(ct).filter((p) => p.refs.length > 0);
  expect(cited.map((p) => p.number)).toEqual([[1], [2, 1], [3]]);
});

test("a malformed value still renders all its text", () => {
  const text = "One. Two. Three.";
  const ct: CitedText = {
    text,
    spans: [
      { start: 5, end: 8, refs: ["c1"] },
      { start: 0, end: 3, refs: ["missing"] }, // unknown ref: plain
      { start: 6, end: 99, refs: ["c2"] }, // overlaps and runs past the end: clamped
    ],
    refs: [ref("c1"), ref("c2")],
  };
  const parts = segments(ct);
  expect(parts.map((p) => p.text).join("")).toBe(text);
  expect(parts.map((p) => [p.text, p.refs.map((r) => r.id)])).toEqual([
    ["One. ", []],
    ["Two", ["c1"]],
    [". Three.", ["c2"]],
  ]);
});

test("plain text is one segment", () => {
  expect(segments({ text: "No sources 🙂", spans: [], refs: [] })).toEqual([
    { text: "No sources 🙂", refs: [], number: [] },
  ]);
});
