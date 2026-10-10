"use client";

import { MapPin } from "lucide-react";
import type { Citation } from "@/lib/types";
import { useAppState } from "@/lib/state";

/** A teal chip naming the cited step; a click opens that step on the canvas with its XML lines. */
export function CitationChip({ citation }: { citation: Citation }) {
  const { jumpToStep } = useAppState();
  const bad = citation.verified === false;
  return (
    <button
      type="button"
      data-testid="citation-chip"
      onClick={() => jumpToStep(citation.activity_id)}
      title={`${citation.text}${bad && citation.reason ? `\nNot verified: ${citation.reason}` : ""}\nOpen on the canvas`}
      className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2.5 py-0.5 text-[12px] font-medium ${
        bad ? "border-error/30 bg-error-bg text-error" : "border-ai/20 bg-ai-bg text-ai hover:border-ai/60"
      }`}
    >
      <MapPin size={12} aria-hidden />
      <span className="truncate">{citation.activity_id}</span>
      <span className="opacity-70">{citation.kind}</span>
    </button>
  );
}
