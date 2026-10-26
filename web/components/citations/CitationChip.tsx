"use client";

// One inline citation: a small phase-tinted chip ("Validate 2") after the text it cites. Hovering
// it for 150 ms or focusing it opens its preview (ArrowDown reopens a closed one, Tab or ArrowDown
// moves into it, Escape closes it); clicking it, or Enter or Space on it, goes to the source.
// Unverified refs are shown in the error tone whatever their phase.
import { useEffect, useId, useRef, useState } from "react";
import { PHASE_LABEL, PHASE_TONE, UNVERIFIED_TONE, useCiteNavigate, type CiteRef } from "@/lib/citations";
import { CitationPreview } from "./CitationPreview";

const OPEN_DELAY = 150;
// Long enough to cross the gap from the chip to its card without it closing.
const CLOSE_DELAY = 120;

export function CitationChip({ refItem, number }: { refItem: CiteRef; number: number }) {
  const navigate = useCiteNavigate();
  const id = useId();
  const btn = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set while the preview closes itself: Escape hands focus back to the chip, which must not reopen it.
  const closing = useRef(false);

  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const later = (next: boolean, ms: number) => {
    clear();
    timer.current = setTimeout(() => setOpen(next), ms);
  };
  useEffect(() => clear, []);

  const phase = PHASE_LABEL[refItem.phase];
  const unverified = refItem.verified === false;
  const previewId = `cite-${id.replace(/:/g, "")}`;

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
        onMouseEnter={() => later(true, OPEN_DELAY)}
        onMouseLeave={() => later(false, CLOSE_DELAY)}
        onFocus={() => {
          clear();
          if (!closing.current) setOpen(true);
        }}
        onBlur={(e) => {
          const to = e.relatedTarget as Node | null;
          if (to && document.getElementById(previewId)?.contains(to)) return;
          clear();
          setOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape" && open) {
            e.preventDefault();
            e.stopPropagation();
            clear();
            setOpen(false);
          } else if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          } else if ((e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey)) && open) {
            const first = document.getElementById(previewId)?.querySelector<HTMLElement>("button");
            if (first) {
              e.preventDefault();
              first.focus();
            }
          }
        }}
        onClick={() => {
          clear();
          setOpen(false);
          navigate(refItem.target);
        }}
        className={`tnum mx-px inline-flex translate-y-[-1px] items-center whitespace-nowrap rounded-[5px] px-1.5 align-baseline text-[11px] font-semibold leading-[18px] tracking-[0.01em] ring-1 ring-inset transition-[filter,box-shadow] duration-150 hover:brightness-[0.97] hover:shadow-card ${
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
            closing.current = true;
            setTimeout(() => (closing.current = false), 0);
          }}
          onHover={(inside) => (inside ? clear() : later(false, CLOSE_DELAY))}
        />
      ) : null}
    </>
  );
}
