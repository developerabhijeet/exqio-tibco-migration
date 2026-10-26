// The cited-text model shared by ExiQoian answers and every phase's claims, mirroring
// engine/citations/model.py (spec section 5): the types, the phase labels and tones, and the pure
// span arithmetic. No React or Next here, so it is tested without a browser or server.
//
// Span offsets are Unicode code points (Python string indices), not the UTF-16 units JS strings
// index by (ruling X13): every slice goes through `sliceCodePoints`, so an emoji or astral character
// before a cited sentence does not shift its span.

export type UiPhase = "upload" | "discover" | "validate" | "design" | "build" | "verify" | "deploy" | "memory";

export interface CiteTarget {
  /** page: a screen that is no phase (/settings, /history); `path` is its route (ruling X18). */
  kind: "activity" | "step" | "memory" | "file" | "page";
  step?: UiPhase | null;
  /** API process id. */
  process_id?: string | null;
  activity_id?: string | null;
  path?: string | null;
  line?: number | null;
  /** Memory uid. */
  uid?: string | null;
  /** Step or page anchor: "#tests", "#infra", "#metrics", "#parity", "#scenarios", "#test-data". */
  hash?: string | null;
}

export interface CiteRef {
  /** "c1", "c2", ...: unique within one CitedText. */
  id: string;
  phase: UiPhase;
  /** rule|finding|summary|decision|activity|process|code|test|error|gate|pattern|note|evidence */
  kind: string;
  label: string;
  /** Masked by the server. */
  preview: string;
  memory_uid?: string | null;
  revision?: number | null;
  verified?: boolean | null;
  reason?: string | null;
  target: CiteTarget;
}

export interface CiteSpan {
  /** Code-point offsets into `CitedText.text`, end exclusive. */
  start: number;
  end: number;
  refs: string[];
}

export interface CitedText {
  text: string;
  spans: CiteSpan[];
  refs: CiteRef[];
}

export const PHASE_LABEL: Record<UiPhase, string> = {
  upload: "Upload",
  discover: "Discover",
  validate: "Validate",
  design: "Design",
  build: "Build",
  verify: "Verify",
  deploy: "Deploy",
  memory: "Memory",
};

/** Chip colours per phase: a tinted background, the phase colour for text and a faint ring. */
export const PHASE_TONE: Record<UiPhase, string> = {
  upload: "bg-sunken text-slate ring-slate/20",
  discover: "bg-coral/[0.07] text-coral-dk ring-coral-dk/20",
  validate: "bg-ai-bg text-ai ring-ai/20",
  design: "bg-accent-soft text-accent-ink ring-accent/25",
  build: "bg-det-bg text-det ring-det/20",
  verify: "bg-gate-bg text-gate ring-gate/20",
  deploy: "bg-decision-bg text-decision ring-decision/20",
  memory: "bg-heading/[0.06] text-heading ring-heading/20",
};

/** An unverified ref, whatever its phase. */
export const UNVERIFIED_TONE = "bg-error-bg text-error ring-error/25";

/** `text` as code points: one entry per Unicode code point, as Python indexes a string. */
export function codePoints(text: string): string[] {
  return Array.from(text);
}

/** Code-point length of `text` (what Python's `len` gives), not its UTF-16 `length`. */
export function codePointLength(text: string): number {
  return codePoints(text).length;
}

/**
 * `text` from code point `start` up to, not including, code point `end`: the JS equivalent of
 * Python's `text[start:end]`, so a server span lands on the same characters after an emoji or any
 * other astral character, which is two UTF-16 units but one code point. Pass `codePoints(text)`
 * instead of the string to slice one text many times without splitting it again.
 */
export function sliceCodePoints(text: string | readonly string[], start: number, end?: number): string {
  return (typeof text === "string" ? codePoints(text) : text).slice(start, end).join("");
}

export interface Segment {
  text: string;
  /** The refs citing this text, in span order, each once; empty for plain text. */
  refs: CiteRef[];
  /** Each ref's chip number, numbered 1.. by first appearance across the whole text. */
  number: number[];
}

/**
 * The text cut into plain and cited runs. Offsets are code points, so every slice goes through
 * `sliceCodePoints` (the text is split into code points once). Defensive against a malformed
 * value, so all of the text is always rendered:
 * - a span whose offsets are not finite numbers is ignored;
 * - offsets are truncated to integers and clamped to the text (a negative start becomes 0);
 * - a span that overlaps the previous one starts where that one ended;
 * - a ref named twice in one span is cited once;
 * - an empty span, or one naming no known ref, leaves its text plain.
 */
export function segments(ct: CitedText): Segment[] {
  const chars = codePoints(ct.text);
  const total = chars.length;
  const byId = new Map(ct.refs.map((r) => [r.id, r]));
  const numbers = new Map<string, number>();
  const out: Segment[] = [];
  const plain = (from: number, to: number) => {
    if (to <= from) return;
    const text = sliceCodePoints(chars, from, to);
    const last = out[out.length - 1];
    if (last && last.refs.length === 0) last.text += text;
    else out.push({ text, refs: [], number: [] });
  };
  const spans = ct.spans
    .filter((s) => Number.isFinite(s.start) && Number.isFinite(s.end))
    .sort((a, b) => a.start - b.start);
  let pos = 0;
  for (const span of spans) {
    const start = Math.min(Math.max(Math.trunc(span.start), pos), total);
    const end = Math.min(Math.max(Math.trunc(span.end), start), total);
    const refs = [...new Set(span.refs)].map((id) => byId.get(id)).filter((r): r is CiteRef => r !== undefined);
    if (end <= start || refs.length === 0) continue; // its text, if any, stays plain below
    plain(pos, start);
    const number = refs.map((r) => {
      const n = numbers.get(r.id) ?? numbers.size + 1;
      numbers.set(r.id, n);
      return n;
    });
    out.push({ text: sliceCodePoints(chars, start, end), refs, number });
    pos = end;
  }
  plain(pos, total);
  return out;
}
