"use client";

import { forwardRef, useEffect, useRef } from "react";
import { Send, Square } from "lucide-react";

export const MAX_CHARS = 2000;
export const COUNT_FROM = 1800;

/**
 * The question box: an auto-growing textarea (Enter sends, Shift+Enter a new line), a counter from
 * 1800 characters, Send, or Stop while an answer streams.
 */
export const Composer = forwardRef<
  HTMLTextAreaElement,
  { value: string; onChange: (v: string) => void; onSend: () => void; onStop: () => void; busy: boolean }
>(function Composer({ value, onChange, onSend, onStop, busy }, ref) {
  const inner = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = inner.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);
  return (
    <form
      className="border-t border-line px-5 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) onSend();
      }}
    >
      <div className="flex items-end gap-2">
        <textarea
          ref={(el) => {
            inner.current = el;
            if (typeof ref === "function") ref(el);
            else if (ref) ref.current = el;
          }}
          data-testid="exiqoian-input"
          aria-label="Message ExiQoian"
          rows={1}
          maxLength={MAX_CHARS}
          className="input max-h-40 min-h-10 resize-none"
          placeholder="Ask about this project"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (!busy && value.trim()) onSend();
            }
          }}
        />
        {busy ? (
          <button type="button" className="btn-secondary px-3" onClick={onStop}>
            <Square size={14} aria-hidden /> Stop
          </button>
        ) : (
          <button type="submit" className="btn-primary px-3" disabled={!value.trim()}>
            <Send size={14} aria-hidden /> Send
          </button>
        )}
      </div>
      {value.length >= COUNT_FROM ? (
        <p data-testid="exiqoian-count" className="mt-1 text-right text-xs text-slate">
          {value.length} / {MAX_CHARS}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-slate">ExiQoian answers only about this project, from its memory. Check the cited artifacts.</p>
    </form>
  );
});
