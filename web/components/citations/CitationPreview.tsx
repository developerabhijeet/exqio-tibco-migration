"use client";

// The card a citation chip opens on hover or focus: where the claim comes from, a masked excerpt,
// whether it was verified, and the ways to open the source. It is rendered into document.body so
// no scrolling or clipped container cuts it off, and is placed under the chip, or above it when
// there is no room below, kept inside the viewport; it fades in over 100 ms unless the reader asks
// for reduced motion. Keyboard: Tab from the chip enters it, Shift+Tab from its first button
// returns to the chip, Tab past its last button moves on to whatever follows the chip, and Escape
// closes it with focus back on the chip. The card itself takes focus (tabIndex -1) when its text
// is clicked, so selecting or clicking its text does not count as focus leaving it.
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, Network } from "lucide-react";
import { ApiError } from "@/lib/api";
import { PHASE_LABEL, PHASE_TONE, UNVERIFIED_TONE, useCiteNavigate, type CiteRef } from "@/lib/citations";
import { memoryApi } from "@/lib/memory";
import { useAppState } from "@/lib/state";

const GAP = 6;
const MARGIN = 8;
const WIDTH = 320;

/** Elements reachable by Tab, in document order. */
function tabbables(): HTMLElement[] {
  const sel = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  return Array.from(document.querySelectorAll<HTMLElement>(sel)).filter(
    (el) => el.tabIndex >= 0 && el.getClientRects().length > 0,
  );
}

/** Focus whatever follows `anchor` in Tab order, skipping the preview; the anchor if nothing does. */
export function focusAfter(anchor: HTMLElement, preview: HTMLElement | null): void {
  const all = tabbables().filter((el) => !preview?.contains(el));
  const at = all.indexOf(anchor);
  const next = at >= 0 ? all[at + 1] : undefined;
  (next ?? anchor).focus();
}

/** 404 (not ingested yet) and 503 (memory off) are answers, not failures to retry. */
const retryUnlessAnswered = (count: number, e: unknown) =>
  !(e instanceof ApiError && (e.status === 404 || e.status === 503)) && count < 1;

/**
 * The ref's memory node, read lazily when the preview first opens (memory links are optimistic:
 * built from uid conventions on the server, checked here). Shows the node's current revision,
 * "Not in memory yet" when the node is not ingested (404) and "Memory unavailable" when memory is
 * off (503); without a memory uid, the revision the payload carries, if any.
 */
function MemoryRevision({ refItem }: { refItem: CiteRef }) {
  const { projectId } = useAppState();
  const uid = refItem.memory_uid ?? null;
  const node = useQuery({
    queryKey: ["memory-node", projectId, uid, null],
    queryFn: () => memoryApi.node(projectId!, uid!, null),
    enabled: !!projectId && !!uid,
    staleTime: 60_000,
    retry: retryUnlessAnswered,
  });
  const status = node.error instanceof ApiError ? node.error.status : null;
  const revision = node.data?.node.valid_from ?? (uid ? null : refItem.revision ?? null);
  if (revision != null) {
    return (
      <span
        className="tnum shrink-0 rounded-full bg-sunken px-1.5 py-px font-mono text-2xs text-slate ring-1 ring-inset ring-line"
        title={`Memory revision ${revision}`}
        data-testid="cite-revision"
      >
        r{revision}
      </span>
    );
  }
  if (status === 404 || status === 503) {
    return (
      <span className="shrink-0 text-2xs text-slate" data-testid="cite-memory-state" data-state={status === 404 ? "missing" : "off"}>
        {status === 404 ? "Not in memory yet" : "Memory unavailable"}
      </span>
    );
  }
  return null;
}

export function CitationPreview({
  refItem,
  anchor,
  onClose,
  id,
  number,
  onHover,
}: {
  refItem: CiteRef;
  anchor: HTMLElement | null;
  onClose: () => void;
  id?: string;
  number?: number;
  /** The pointer entered (true) or left (false) the card, so a hover-opened card stays open. */
  onHover?: (inside: boolean) => void;
}) {
  const navigate = useCiteNavigate();
  const box = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; above: boolean } | null>(null);

  // Measured and placed in a layout effect, before the first paint: until then it waits off screen.
  // (Not `visibility: hidden`: the buttons' transition would carry it over and keep them unfocusable.)
  const place = useCallback(() => {
    const el = box.current;
    if (!anchor || !el) return;
    const a = anchor.getBoundingClientRect();
    const h = el.offsetHeight;
    const w = el.offsetWidth;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const below = a.bottom + GAP;
    const above = below + h > vh - MARGIN && a.top - GAP - h >= MARGIN;
    const top = above ? a.top - GAP - h : Math.max(MARGIN, Math.min(below, vh - MARGIN - h));
    const left = Math.max(MARGIN, Math.min(a.left, vw - MARGIN - w));
    setPos({ top, left, above });
  }, [anchor]);

  useLayoutEffect(() => {
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [place]);

  const close = (refocus: boolean) => {
    onClose();
    if (refocus) anchor?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close(true);
      return;
    }
    if (e.key !== "Tab" || !box.current || !anchor) return;
    const buttons = Array.from(box.current.querySelectorAll<HTMLElement>("button"));
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      anchor.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      onClose();
      focusAfter(anchor, box.current);
    }
  };

  // Focus leaving for anywhere but the card or the chip closes the card.
  const onBlur = (e: React.FocusEvent<HTMLDivElement>) => {
    const to = e.relatedTarget as Node | null;
    if (to && (box.current?.contains(to) || anchor?.contains(to))) return;
    onClose();
  };

  const go = (target: CiteRef["target"]) => {
    onClose();
    navigate(target);
  };

  const unverified = refItem.verified === false;
  const phase = PHASE_LABEL[refItem.phase];
  const labelId = id ? `${id}-label` : undefined;

  return createPortal(
    <div
      ref={box}
      id={id}
      role="dialog"
      aria-modal="false"
      aria-labelledby={labelId}
      aria-label={labelId ? undefined : `Citation ${number ?? ""}`.trim()}
      data-testid="cite-preview"
      data-ref={refItem.id}
      data-placement={pos?.above ? "top" : "bottom"}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      tabIndex={-1}
      onPointerEnter={(e) => {
        if (e.pointerType === "mouse") onHover?.(true);
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse") onHover?.(false);
      }}
      style={{
        position: "fixed",
        top: pos?.top ?? -9999,
        left: pos?.left ?? -9999,
        width: WIDTH,
        maxWidth: `calc(100vw - ${MARGIN * 2}px)`,
      }}
      className={`z-[60] rounded-card border border-line bg-surface p-3.5 text-left shadow-pop outline-none motion-safe:transition-opacity motion-safe:duration-100 ${
        pos ? "opacity-100" : "opacity-0"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 text-eyebrow uppercase text-slate">
          <span
            aria-hidden
            className={`inline-block h-2 w-2 shrink-0 rounded-full ring-1 ring-inset ${unverified ? UNVERIFIED_TONE : PHASE_TONE[refItem.phase]}`}
          />
          <span className="truncate">
            {phase} · {refItem.kind}
          </span>
        </p>
        <MemoryRevision refItem={refItem} />
      </div>
      <p id={labelId} className="mt-1.5 break-words text-md font-semibold leading-snug text-heading">
        {refItem.label}
      </p>
      {refItem.preview ? (
        <p className="mt-1 whitespace-pre-line break-words text-sm leading-relaxed text-slate">{refItem.preview}</p>
      ) : null}
      {unverified ? (
        <p
          className="mt-2.5 flex items-start gap-1.5 rounded-btn bg-error-bg px-2 py-1.5 text-sm leading-snug text-error"
          data-testid="cite-unverified"
        >
          <AlertTriangle aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" />
          <span>{refItem.reason ? `Not verified: ${refItem.reason}` : "Not verified"}</span>
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
        <button
          type="button"
          className="btn-secondary px-2.5 py-1 text-sm"
          onClick={() => go(refItem.target)}
          data-testid="cite-open"
        >
          <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
          Open
        </button>
        {refItem.memory_uid ? (
          <button
            type="button"
            className="btn-ghost px-2.5 py-1 text-sm"
            onClick={() => go({ kind: "memory", uid: refItem.memory_uid })}
            data-testid="cite-open-memory"
          >
            <Network aria-hidden className="h-3.5 w-3.5" />
            Open in Memory
          </button>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
