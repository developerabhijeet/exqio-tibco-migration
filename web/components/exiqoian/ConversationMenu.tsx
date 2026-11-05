"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Trash2 } from "lucide-react";
import type { Conversation } from "@/lib/exiqoian";

/**
 * The conversation list: a disclosure button and the list it shows (title, date, delete). Escape
 * closes it and returns focus to the button; a click elsewhere closes it too.
 */
export function ConversationMenu({
  conversations,
  activeId,
  onSelect,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  return (
    <div
      ref={root}
      className="relative"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
    >
      <button
        ref={button}
        type="button"
        className="btn-ghost"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)}
      >
        Conversations <ChevronDown size={14} aria-hidden />
      </button>
      {open ? (
        <ul role="menu" aria-label="Conversations" className="absolute right-0 top-full z-10 mt-1 max-h-72 w-72 overflow-y-auto rounded-card border border-line bg-surface p-1.5 shadow-e2">
          {conversations.length === 0 ? <li className="px-3 py-2 text-sm text-slate">No conversations yet</li> : null}
          {conversations.map((c) => (
            <li key={c.id} className="flex items-center gap-1" role="none">
              <button
                type="button"
                role="menuitem"
                aria-current={c.id === activeId ? "true" : undefined}
                className={`min-w-0 flex-1 rounded-btn px-3 py-2 text-left hover:bg-sunken ${c.id === activeId ? "bg-accent-soft" : ""}`}
                onClick={() => {
                  onSelect(c.id);
                  setOpen(false);
                }}
              >
                <span className="block truncate text-md font-medium text-heading">{c.title || "New conversation"}</span>
                <span className="block text-xs text-slate">{new Date(c.updated_at).toLocaleString()}</span>
              </button>
              <button
                type="button"
                className="btn-ghost px-2"
                aria-label="Delete conversation"
                title={`Delete ${c.title || "conversation"}`}
                onClick={() => onDelete(c.id)}
              >
                <Trash2 size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
