"use client";

// A CitedText as prose: plain runs as they are, each cited run followed by its chips. The cited run
// takes a faint tint while one of its chips is hovered or focused, so the reader sees exactly what
// the chip vouches for. Everything is inline, so it can sit inside a paragraph.
import { useMemo } from "react";
import { segments, type CitedText as CitedTextValue } from "@/lib/citations";
import { CitationChip } from "./CitationChip";

export function CitedText({ value, className = "" }: { value: CitedTextValue; className?: string }) {
  const parts = useMemo(() => segments(value), [value]);
  return (
    <span className={`whitespace-pre-wrap ${className}`} data-testid="cited-text">
      {parts.map((p, i) =>
        p.refs.length === 0 ? (
          <span key={i}>{p.text}</span>
        ) : (
          <span key={i} className="group/cite">
            <span
              data-testid="cited-segment"
              data-refs={p.refs.map((r) => r.id).join(" ")}
              className="rounded-[3px] transition-colors duration-150 group-has-[button:hover]/cite:bg-purple-lt/70 group-has-[button:focus-visible]/cite:bg-purple-lt/70"
            >
              {p.text}
            </span>
            <span className="ml-0.5 whitespace-nowrap" data-testid="cite-group">
              {p.refs.map((r, j) => (
                <CitationChip key={r.id} refItem={r} number={p.number[j]} />
              ))}
            </span>
          </span>
        ),
      )}
    </span>
  );
}
