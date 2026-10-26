"use client";

// The cited-text model shared by ExiQoian answers and every phase's claims, mirroring
// engine/citations/model.py (spec section 5). Span offsets are Unicode code points (Python string
// indices), not the UTF-16 units JS strings index by: `segments` slices `Array.from(text)`, so an
// emoji or astral character before a cited sentence does not shift its span.
import { useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { STEP_KEYS, useAppState, type StepKey } from "./state";

export type UiPhase = "upload" | "discover" | "validate" | "design" | "build" | "verify" | "deploy" | "memory";

export interface CiteTarget {
  kind: "activity" | "step" | "memory" | "file";
  step?: UiPhase | null;
  /** API process id. */
  process_id?: string | null;
  activity_id?: string | null;
  path?: string | null;
  line?: number | null;
  /** Memory uid. */
  uid?: string | null;
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
  upload: "bg-warm text-slate ring-slate/20",
  discover: "bg-coral/[0.07] text-coral-dk ring-coral-dk/20",
  validate: "bg-ai-bg text-ai ring-ai/20",
  design: "bg-purple-lt text-purple-dk ring-purple/25",
  build: "bg-det-bg text-det ring-det/20",
  verify: "bg-gate-bg text-gate ring-gate/20",
  deploy: "bg-decision-bg text-decision ring-decision/20",
  memory: "bg-plum/[0.06] text-plum ring-plum/20",
};

/** An unverified ref, whatever its phase. */
export const UNVERIFIED_TONE = "bg-error-bg text-error ring-error/25";

/** Code-point length of `text` (what Python's `len` gives), not its UTF-16 `length`. */
export function codePointLength(text: string): number {
  return Array.from(text).length;
}

/**
 * `text` from code point `start` up to, not including, code point `end`: the JS equivalent of
 * Python's `text[start:end]`, so a server span lands on the same characters after an emoji or any
 * other astral character, which is two UTF-16 units but one code point.
 */
export function sliceCodePoints(text: string, start: number, end?: number): string {
  return Array.from(text).slice(start, end).join("");
}

export interface Segment {
  text: string;
  /** The refs citing this text, in span order; empty for plain text. */
  refs: CiteRef[];
  /** Each ref's chip number, numbered 1.. by first appearance across the whole text. */
  number: number[];
}

/**
 * The text cut into plain and cited runs. Offsets are code points, so every slice goes through
 * `sliceCodePoints`. Defensive against a malformed value: offsets are clamped to the text, a span
 * that overlaps the previous one starts where it ended, and an empty span or one naming no known
 * ref leaves its text plain.
 */
export function segments(ct: CitedText): Segment[] {
  const total = codePointLength(ct.text);
  const byId = new Map(ct.refs.map((r) => [r.id, r]));
  const numbers = new Map<string, number>();
  const out: Segment[] = [];
  const plain = (from: number, to: number) => {
    if (to <= from) return;
    const text = sliceCodePoints(ct.text, from, to);
    const last = out[out.length - 1];
    if (last && last.refs.length === 0) last.text += text;
    else out.push({ text, refs: [], number: [] });
  };
  let pos = 0;
  for (const span of [...ct.spans].sort((a, b) => a.start - b.start)) {
    const start = Math.min(Math.max(span.start, pos), total);
    const end = Math.min(Math.max(span.end, start), total);
    const refs = span.refs.map((id) => byId.get(id)).filter((r): r is CiteRef => r !== undefined);
    if (end <= start || refs.length === 0) continue; // its text, if any, stays plain below
    plain(pos, start);
    const number = refs.map((r) => {
      const n = numbers.get(r.id) ?? numbers.size + 1;
      numbers.set(r.id, n);
      return n;
    });
    out.push({ text: sliceCodePoints(ct.text, start, end), refs, number });
    pos = end;
  }
  plain(pos, total);
  return out;
}

/**
 * Go where a citation points. activity: the step on the Discover canvas with its XML; step: that
 * phase of the process; file: the generated file in Build's source and target explorer; memory:
 * the node in the memory explorer. Off the main screen (on /memory, say) the main screen is opened
 * on the step afterwards.
 */
export function useCiteNavigate(): (target: CiteTarget) => void {
  const { jumpToStep, openProcess, setStep, openBuildFile } = useAppState();
  const router = useRouter();
  const pathname = usePathname();
  return useCallback(
    (target: CiteTarget) => {
      if (target.kind === "memory" || (target.kind === "step" && target.step === "memory")) {
        router.push(target.uid ? `/memory?uid=${encodeURIComponent(target.uid)}` : "/memory");
        return;
      }
      let step: StepKey;
      if (target.kind === "activity" && target.activity_id) {
        jumpToStep(target.activity_id, target.process_id);
        step = "discover";
      } else if (target.kind === "file" && target.path) {
        if (target.process_id) openProcess(target.process_id);
        openBuildFile(target.path, target.line ?? null);
        step = "build";
        setStep(step);
      } else {
        step = target.step && (STEP_KEYS as readonly string[]).includes(target.step) ? (target.step as StepKey) : "discover";
        if (target.process_id) openProcess(target.process_id);
        setStep(step);
      }
      if (pathname !== "/") {
        const q = new URLSearchParams({ step });
        if (target.process_id) q.set("process", target.process_id);
        router.push(`/?${q.toString()}`);
      }
    },
    [jumpToStep, openProcess, setStep, openBuildFile, router, pathname],
  );
}
