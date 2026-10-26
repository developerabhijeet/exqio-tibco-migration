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

/** The longest preview a ref carries (engine/citations PREVIEW_MAX). */
export const PREVIEW_MAX = 280;

/** `text` cut to at most `limit` code points, an ellipsis marking the cut (engine/citations `cut`). */
export function cut(text: string, limit: number): string {
  const cps = codePoints(text);
  return cps.length <= limit ? text : cps.slice(0, limit - 1).join("").trimEnd() + "…";
}

// The web twin of engine/agents/provider.mask_sensitive, for text the client builds refs from
// itself (Deploy evidence): SSNs, and the values of ssn / account number / card number fields as
// `key: value`, `key=value`, JSON `"key": "value"` or `<key>value</key>`. A key holds the field name
// as whole words (borrower_ssn, customerSsn, ns:accountNumber; not classname).
const FIELD = "(?:[Ss][Ss][Nn]|[Aa]ccount[_\\-.]?[Nn]umber|ACCOUNT[_\\-.]?NUMBER|[Cc]ard[_\\-.]?[Nn]umber|CARD[_\\-.]?NUMBER)";
const KEY = `(?:[A-Za-z0-9]*[_\\-.])*(?:[a-z0-9]+(?=[A-Z]))?${FIELD}(?:[_\\-.][A-Za-z0-9_\\-.]*|[A-Z][A-Za-z0-9]*)?`;
const PAIR = new RegExp(`(["']?)(?<![A-Za-z0-9])(${KEY})\\1(\\s*[:=]\\s*)("[^"]*"|'[^']*'|[^\\s,;}\\]&<"']+)`, "g");
const ELEMENT = new RegExp(`(<((?:[A-Za-z0-9_.\\-]+:)?${KEY})(?:\\s[^<>]*)?>)[^<]*(</\\2\\s*>)`, "g");
const SSN = /\b\d{3}-\d{2}-\d{4}\b/g;
const MASK = "***";

/** Whether a parity path's leaf names a sensitive field (`output.body.borrowerSsn`), as the API
 * decides it: such a value shows as the mask whole, whatever its shape. */
export function sensitiveField(path: string): boolean {
  const trimmed = path.replace(/^[./[\]@]+|[./[\]@]+$/g, "");
  const leaf = trimmed ? trimmed.split(/[./[\]@]+/).pop() ?? "" : "";
  return !!leaf && maskSensitive(`${leaf}: x`) !== `${leaf}: x`;
}

/** `text` with SSNs and sensitive field values masked, as the server masks `cited.text`. */
export function maskSensitive(text: string): string {
  if (!text) return text;
  return text
    .replace(ELEMENT, (_m, open: string, _name: string, close: string) => `${open}${MASK}${close}`)
    .replace(PAIR, (_m, q: string, key: string, sep: string, val: string) => {
      const quoted = (val[0] === '"' || val[0] === "'") && val.length >= 2;
      return `${q}${key}${q}${sep}${quoted ? `${val[0]}${MASK}${val[0]}` : MASK}`;
    })
    .replace(SSN, "***-**-****");
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

const CLOSING = ".!?";
const isSpace = (c: string) => /\s/u.test(c);

/**
 * Sentence bounds in code points, trimmed of surrounding whitespace and including the closing
 * punctuation: engine/citations/spans.py `sentence_bounds` (a sentence ends at a run of `.!?`
 * followed by whitespace or the end, or at a newline).
 */
export function sentenceBounds(text: string | readonly string[]): [number, number][] {
  const chars = typeof text === "string" ? codePoints(text) : text;
  const out: [number, number][] = [];
  const add = (a: number, b: number) => {
    while (a < b && isSpace(chars[a])) a++;
    while (b > a && isSpace(chars[b - 1])) b--;
    if (a < b) out.push([a, b]);
  };
  let start = 0;
  for (let i = 0; i < chars.length; i++) {
    if (chars[i] === "\n") {
      add(start, i);
      start = i + 1;
    } else if (CLOSING.includes(chars[i])) {
      let j = i;
      while (j < chars.length && CLOSING.includes(chars[j])) j++;
      if (j === chars.length || isSpace(chars[j])) {
        add(start, j);
        start = j;
      }
      i = j - 1;
    }
  }
  add(start, chars.length);
  return out;
}

/**
 * Every ref on the last sentence, its closing punctuation left out of the span, ids renumbered
 * `c1..cn`: engine/citations/claims.py `tail`, for claims the web assembles itself (the Deploy
 * security checks). No refs gives plain text; refs on text with no sentence throw, as in Python.
 */
export function tail(text: string, refs: CiteRef[]): CitedText {
  if (refs.length === 0) return { text, spans: [], refs: [] };
  const chars = codePoints(text);
  const bounds = sentenceBounds(chars);
  if (bounds.length === 0) throw new Error("cannot anchor citations on empty text");
  const [a, b] = bounds[bounds.length - 1];
  let e = b;
  while (e > a && CLOSING.includes(chars[e - 1])) e--;
  while (e > a && isSpace(chars[e - 1])) e--;
  const renumbered = refs.map((r, i) => ({ ...r, id: `c${i + 1}` }));
  return { text, spans: [{ start: a, end: e > a ? e : b, refs: renumbered.map((r) => r.id) }], refs: renumbered };
}
