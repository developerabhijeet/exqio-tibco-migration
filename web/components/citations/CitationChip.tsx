"use client";

// One inline citation: a small phase-tinted chip ("Validate 2") after the text it cites.
//
// Opening the preview:
// - mouse: hovering 150 ms opens it; leaving the chip and its card closes it, unless focus is in
//   one of them;
// - keyboard: focusing the chip opens it after FOCUS_DELAY ms if focus is still there (keyboard
//   focus only, not a click), so tabbing along a cited paragraph, or a screen reader walking it,
//   is not interrupted by a card per chip; ArrowDown opens it at once; Tab or ArrowDown then moves
//   into it;
// - touch: the first tap opens it, a second tap (or its Open button) goes to the source.
// Escape closes it from anywhere (WCAG 1.4.13), handing focus back to the chip if focus was in it.
// Clicking the chip, or Enter or Space on it, goes to the source. Unverified refs are shown in the
// error tone whatever their phase.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { PHASE_LABEL, PHASE_TONE, UNVERIFIED_TONE, useCiteNavigate, type CiteRef } from "@/lib/citations";
import { CitationPreview } from "./CitationPreview";

const OPEN_DELAY = 150;
const FOCUS_DELAY = 300;
// Long enough to cross the gap from the chip to its card without it closing.
const CLOSE_DELAY = 120;

export function CitationChip({ refItem, number }: { refItem: CiteRef; number: number }) {
  const navigate = useCiteNavigate();
  const id = useId();
  const previewId = `cite-${id.replace(/[^A-Za-z0-9_-]/g, "")}`;
  const btn = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set while the preview closes itself: focus handed back to the chip must not reopen it.
  const closing = useRef(false);
  // The pointer that started the current click: "touch" taps open first, then navigate.
  const pointer = useRef<string | null>(null);

  const card = useCallback(() => document.getElementById(previewId), [previewId]);
  const focusInside = useCallback(() => {
    const a = document.activeElement;
    return !!a && (a === btn.current || !!card()?.contains(a));
  }, [card]);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const later = (next: boolean, ms: number) => {
    clear();
    timer.current = setTimeout(() => setOpen(next), ms);
  };
  const hoverOut = () => {
    if (focusInside()) clear();
    else later(false, CLOSE_DELAY);
  };
  useEffect(() => clear, []);

  // Escape dismisses the card however it was opened, focus anywhere.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const refocus = focusInside();
      clear();
      setOpen(false);
      if (refocus && document.activeElement !== btn.current) {
        closing.current = true;
        btn.current?.focus();
        closing.current = false;
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, focusInside]);

  const phase = PHASE_LABEL[refItem.phase];
  const unverified = refItem.verified === false;

  return (
    <>
      <button
        ref={btn}
        type="button"
        data-testid="cite-chip"
        data-ref={refItem.id}
        data-phase={refItem.phase}
        data-target-kind={refItem.target.kind}
        data-step={refItem.target.activity_id ?? ""}
        data-verified={refItem.verified == null ? "" : String(refItem.verified)}
        aria-label={`Citation ${number}: ${phase} ${refItem.kind} ${refItem.label}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? previewId : undefined}
        onPointerDown={(e) => {
          pointer.current = e.pointerType;
        }}
        onPointerEnter={(e) => {
          if (e.pointerType === "mouse") later(true, OPEN_DELAY);
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === "mouse") hoverOut();
        }}
        onFocus={(e) => {
          if (closing.current || open) return;
          if (e.currentTarget.matches(":focus-visible")) later(true, FOCUS_DELAY);
        }}
        onBlur={(e) => {
          const to = e.relatedTarget as Node | null;
          if (to && card()?.contains(to)) return;
          clear();
          setOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            clear();
            setOpen(true);
          } else if ((e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey)) && open) {
            const first = card()?.querySelector<HTMLElement>("button");
            if (first) {
              e.preventDefault();
              first.focus();
            }
          }
        }}
        onClick={(e) => {
          clear();
          const touch = e.detail > 0 && pointer.current === "touch";
          pointer.current = null;
          if (touch && !open) {
            setOpen(true);
            return;
          }
          setOpen(false);
          navigate(refItem.target);
        }}
        className={`tnum mx-px inline-flex translate-y-[-1px] items-center whitespace-nowrap rounded-[5px] px-1.5 align-baseline text-2xs font-semibold leading-[18px] tracking-[0.01em] ring-1 ring-inset transition-[filter,box-shadow] duration-150 hover:brightness-[0.97] hover:shadow-card ${
          unverified ? UNVERIFIED_TONE : PHASE_TONE[refItem.phase]
        }`}
      >
        {phase} {number}
      </button>
      {open ? (
        <CitationPreview
          id={previewId}
          refItem={refItem}
          number={number}
          anchor={btn.current}
          onClose={() => {
            clear();
            setOpen(false);
            // The card hands focus back synchronously (Escape) or moves it on (Tab past its end).
            closing.current = true;
            setTimeout(() => (closing.current = false), 0);
          }}
          onHover={(inside) => (inside ? clear() : hoverOut())}
        />
      ) : null}
    </>
  );
}
