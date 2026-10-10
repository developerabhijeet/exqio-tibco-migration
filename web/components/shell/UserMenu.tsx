"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useAppState } from "@/lib/state";
import { USERS } from "@/lib/types";

const ROLES: Record<string, string> = {
  "sme.vicky": "Business SME",
  "architect.lee": "Architect",
  "engineer.ravi": "Engineer",
  "qa.priya": "QA lead",
  "ops.sam": "Operations",
};

export function UserMenu() {
  const { user, setUser } = useAppState();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="user-menu"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-btn px-2 py-1 text-white hover:bg-white/10"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-purple text-[13px] font-semibold uppercase">
          {user.slice(0, 1)}
        </span>
        <span className="text-left leading-tight">
          <span className="block text-[14px] font-medium">{user}</span>
          <span className="block text-[12px] text-lilac">{ROLES[user]}</span>
        </span>
        <ChevronDown size={16} aria-hidden />
      </button>
      {open ? (
        <div role="menu" className="card absolute right-0 top-12 z-50 w-[240px] py-1 shadow-lg">
          <p className="label px-4 pb-1 pt-2">Act as</p>
          {USERS.map((u) => (
            <button
              key={u}
              role="menuitemradio"
              aria-checked={u === user}
              type="button"
              onClick={() => {
                setUser(u);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between px-4 py-2 text-left text-[14px] hover:bg-warm ${
                u === user ? "font-semibold text-purple" : "text-ink"
              }`}
            >
              <span>{u}</span>
              <span className="text-[12px] text-slate">{ROLES[u]}</span>
            </button>
          ))}
          <p className="border-t border-line px-4 py-2 text-[12px] text-slate">Sent as X-User on API requests.</p>
        </div>
      ) : null}
    </div>
  );
}
