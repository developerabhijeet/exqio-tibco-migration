"use client";

import { Info, MessageSquare } from "lucide-react";
import { CitedText } from "@/components/citations/CitedText";
import { Pill } from "@/components/ui/Pill";
import type { CitedText as CitedTextValue } from "@/lib/citations";
import type { ChatMessage } from "@/lib/exiqoian";

/** A streamed answer in progress: the sentences received so far. */
export interface Draft {
  text: string;
  cited: CitedTextValue;
}

const DECLINED = new Set(["off_topic", "injection", "not_covered", "unavailable", "intro"]);

/** One message of the conversation: the question right-aligned, the answer as cited prose. */
export function MessageView({ message, draft }: { message?: ChatMessage; draft?: { cited: CitedTextValue } }) {
  if (message?.role === "user") {
    return (
      <div data-testid="exiqoian-message" data-role="user" data-verdict="" className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-card bg-accent-soft px-3 py-2 text-md text-heading">{message.text}</p>
      </div>
    );
  }
  const verdict = message?.verdict ?? "pending";
  const declined = DECLINED.has(verdict);
  const cited = message?.cited ?? draft?.cited ?? { text: message?.text ?? "", spans: [], refs: [] };
  return (
    <div
      data-testid="exiqoian-message"
      data-role="assistant"
      data-verdict={verdict}
      className={`rounded-card border px-3 py-2 ${declined ? "border-line bg-sunken text-slate" : "border-line bg-surface text-ink"}`}
    >
      <div className="mb-1 flex items-center gap-2">
        {declined ? (
          <Info size={14} className="text-slate" aria-hidden />
        ) : (
          <MessageSquare size={14} className="text-ai" aria-hidden />
        )}
        <span className="eyebrow">ExiQoian</span>
        {message && verdict === "answer" ? (
          <Pill tone={message.used_model ? "ai" : "det"} testId="exiqoian-source">
            {message.used_model ? "AI answer" : "From memory, no model"}
          </Pill>
        ) : null}
      </div>
      <p className="text-md leading-relaxed">
        <CitedText value={cited} />
      </p>
    </div>
  );
}
