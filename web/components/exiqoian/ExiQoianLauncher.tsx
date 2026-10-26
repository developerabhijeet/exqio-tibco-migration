"use client";

import { useEffect } from "react";
import { Sparkles } from "lucide-react";
import { useAppState } from "@/lib/state";

/** The top bar button (a sparkle and "ExiQoian"); Ctrl+J / ⌘J toggles the panel from anywhere. */
export function ExiQoianLauncher() {
  const { projectId, chatOpen, setChatOpen } = useAppState();
  useEffect(() => {
    if (!projectId) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setChatOpen(!chatOpen);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [projectId, chatOpen, setChatOpen]);
  if (!projectId) return null;
  return (
    <button
      type="button"
      data-testid="exiqoian-launcher"
      aria-keyshortcuts="Control+J Meta+J"
      aria-expanded={chatOpen}
      title="ExiQoian (Ctrl+J)"
      onClick={() => setChatOpen(!chatOpen)}
      className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-btn bg-white/10 px-3 py-1.5 text-md font-medium text-white ring-1 ring-inset ring-white/15 transition-colors duration-fast hover:bg-white/20"
    >
      <Sparkles size={16} aria-hidden />
      ExiQoian
    </button>
  );
}
