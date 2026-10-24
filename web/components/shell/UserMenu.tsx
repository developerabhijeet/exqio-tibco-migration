"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, LogIn, LogOut, ShieldCheck } from "lucide-react";
import { PERSONAS, personaOf, stepsOf, type Persona } from "@/lib/personas";
import { useSession } from "@/lib/signin";
import { useAppState } from "@/lib/state";
import { STEP_LABELS } from "@/lib/steps";
import type { UserName } from "@/lib/types";

/** "Design to Deploy", or just "Deploy" when the persona owns one step. */
function range(persona: Persona): string {
  const steps = stepsOf(persona);
  const first = STEP_LABELS[steps[0]] ?? steps[0];
  const last = STEP_LABELS[steps[steps.length - 1]] ?? steps[steps.length - 1];
  return steps.length === 1 ? first : `${first} to ${last}`;
}

export function UserMenu() {
  const { user, setUser } = useAppState();
  const { session, signOut } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const me = personaOf(user);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="user-menu"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2.5 rounded-btn px-2 py-1.5 text-white transition-colors hover:bg-white/10"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-purple text-[13px] font-semibold uppercase shadow-inset">
          {user.slice(0, 1)}
        </span>
        <span className="text-left leading-tight">
          <span className="block text-[13.5px] font-semibold">{user}</span>
          <span className="block text-[11.5px] text-lilac">{me.role}</span>
        </span>
        <ChevronDown size={16} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <div role="menu" className="card absolute right-0 top-14 z-50 w-[360px] overflow-hidden p-0 shadow-pop">
          <p className="label border-b border-line bg-warm px-4 py-2.5">Act as</p>
          {PERSONAS.map((p) => {
            const mine = p.id === user;
            return (
              <button
                key={p.id}
                role="menuitemradio"
                aria-checked={mine}
                // The accessible name leads with the user id, so a caller that picks a persona by
                // the name it sends as X-User finds it; the role and the summary are below, read.
                aria-label={`${p.id}, ${p.role}`}
                data-testid={`persona-${p.id}`}
                type="button"
                onClick={() => {
                  setUser(p.id as UserName);
                  setOpen(false);
                }}
                className={`flex w-full gap-3 border-b border-line/60 px-4 py-3 text-left transition-colors last:border-0 ${
                  mine ? "bg-purple-lt" : "hover:bg-warm"
                }`}
              >
                <span className="mt-0.5 w-4 shrink-0">
                  {mine ? <Check size={16} className="text-purple" aria-hidden /> : null}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className={`text-[14px] font-semibold ${mine ? "text-purple" : "text-plum"}`}>{p.role}</span>
                    <span className="font-mono text-[11.5px] text-slate">{p.id}</span>
                  </span>
                  <span className="mt-0.5 block text-[12.5px] leading-snug text-slate">{p.responsibility}</span>
                  <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11.5px]">
                    <span className="rounded-full bg-warm px-2 py-0.5 font-medium text-plum">{range(p)}</span>
                    <span className="flex items-center gap-1 rounded-full bg-gate-bg px-2 py-0.5 font-medium text-gate">
                      <ShieldCheck size={11} aria-hidden />
                      {p.gates.join(", ")}
                    </span>
                  </span>
                </span>
              </button>
            );
          })}
          {/* The sign-in screen records which account was chosen on this browser. It decides
              nothing here: the role is the user's to pick either way. */}
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line bg-warm px-4 py-2.5 text-[11.5px] text-slate"
            data-testid="user-menu-session"
          >
            {session ? (
              <>
                <span className="min-w-0 flex-1 break-all">
                  Signed in as <span className="font-medium text-plum">{session.email}</span>
                  {session.user === user ? null : <> · now acting as {me.role}</>}
                </span>
                <button
                  type="button"
                  data-testid="user-menu-signout"
                  className="flex shrink-0 items-center gap-1 font-medium text-purple hover:underline"
                  // Out of the application and back to where signing in starts, the way a session
                  // ending anywhere else does.
                  onClick={() => {
                    signOut();
                    setOpen(false);
                    router.push("/signin");
                  }}
                >
                  <LogOut size={12} aria-hidden /> Sign out
                </button>
              </>
            ) : (
              <>
                <span className="min-w-0 flex-1">Not signed in.</span>
                <Link
                  href="/signin"
                  data-testid="user-menu-signin"
                  className="flex shrink-0 items-center gap-1 font-medium text-purple hover:underline"
                  onClick={() => setOpen(false)}
                >
                  <LogIn size={12} aria-hidden /> Sign-in screen
                </Link>
              </>
            )}
          </div>
          <p className="border-t border-line bg-warm px-4 py-2.5 text-[11.5px] leading-snug text-slate">
            Recorded as the actor on every gate decision. Earlier steps stay readable; their controls belong to
            the role that owns them.
          </p>
        </div>
      ) : null}
    </div>
  );
}
