"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, LogIn, LogOut, Monitor, Moon, ShieldCheck, Sun } from "lucide-react";
import { signOutOidc, type Principal } from "@/lib/auth";
import { PERSONAS, personaOf, stepsOf, useActingPersona, type Persona } from "@/lib/personas";
import { useSession } from "@/lib/signin";
import { useAppState } from "@/lib/state";
import { STEP_LABELS } from "@/lib/steps";
import { useTheme, type ThemeChoice } from "@/lib/theme";
import type { UserName } from "@/lib/types";

/** "Design to Deploy", or just "Deploy" when the persona owns one step. */
function range(persona: Persona): string {
  const steps = stepsOf(persona);
  if (steps.length === 0) return "read only";
  const first = STEP_LABELS[steps[0]] ?? steps[0];
  const last = STEP_LABELS[steps[steps.length - 1]] ?? steps[steps.length - 1];
  return steps.length === 1 ? first : `${first} to ${last}`;
}

export function UserMenu() {
  const { user, setUser, auth } = useAppState();
  const { session, signOut } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const acting = useActingPersona();
  const signedIn = auth.mode === "oidc" ? auth.me : null;
  const me = signedIn ? acting : personaOf(user);
  const shown = signedIn ? signedIn.name || signedIn.upn : user;

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
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-strong text-sm font-semibold uppercase shadow-inset">
          {shown.slice(0, 1)}
        </span>
        <span className="text-left leading-tight max-lg:sr-only">
          <span className="block text-md font-semibold">{shown}</span>
          <span className="block text-xs text-lilac">{me.role}</span>
        </span>
        <ChevronDown size={16} aria-hidden className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && signedIn ? (
        <SignedInMenu me={signedIn} persona={me} />
      ) : open ? (
        <div role="menu" className="card absolute right-0 top-14 z-50 w-[360px] overflow-hidden p-0 shadow-pop">
          <ThemeOptions />
          <p id="user-menu-act-as" className="label border-b border-line bg-sunken px-4 py-2.5">
            Act as
          </p>
          {/* A group of its own, so the persona choices are told apart from the theme choices. */}
          <div role="group" aria-labelledby="user-menu-act-as">
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
                    mine ? "bg-accent-soft" : "hover:bg-sunken"
                  }`}
                >
                  <span className="mt-0.5 w-4 shrink-0">
                    {mine ? <Check size={16} className="text-accent-ink" aria-hidden /> : null}
                  </span>
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className={`text-md font-semibold ${mine ? "text-accent-ink" : "text-heading"}`}>{p.role}</span>
                      <span className="font-mono text-xs text-slate">{p.id}</span>
                    </span>
                    <span className="mt-0.5 block text-sm leading-snug text-slate">{p.responsibility}</span>
                    <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="rounded-full bg-sunken px-2 py-0.5 font-medium text-heading">{range(p)}</span>
                      <span className="flex items-center gap-1 rounded-full bg-gate-bg px-2 py-0.5 font-medium text-gate">
                        <ShieldCheck size={11} aria-hidden />
                        {p.gates.join(", ")}
                      </span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          {/* The sign-in screen records which account was chosen on this browser. It decides
              nothing here: the role is the user's to pick either way. */}
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-line bg-sunken px-4 py-2.5 text-xs text-slate"
            data-testid="user-menu-session"
          >
            {session ? (
              <>
                <span className="min-w-0 flex-1 break-all">
                  Signed in as <span className="font-medium text-heading">{session.email}</span>
                  {session.user === user ? null : <> · now acting as {me.role}</>}
                </span>
                <button
                  type="button"
                  data-testid="user-menu-signout"
                  className="flex shrink-0 items-center gap-1 font-medium text-accent-ink hover:underline"
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
                  className="flex shrink-0 items-center gap-1 font-medium text-accent-ink hover:underline"
                  onClick={() => setOpen(false)}
                >
                  <LogIn size={12} aria-hidden /> Sign-in screen
                </Link>
              </>
            )}
          </div>
          <p className="border-t border-line bg-sunken px-4 py-2.5 text-xs leading-snug text-slate">
            Recorded as the actor on every gate decision. Earlier steps stay readable; their controls belong to
            the role that owns them.
          </p>
        </div>
      ) : null}
    </div>
  );
}

const THEMES: { id: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { id: "system", label: "System", Icon: Monitor },
  { id: "light", label: "Light", Icon: Sun },
  { id: "dark", label: "Dark", Icon: Moon },
];

/**
 * The theme switch at the top of either menu: System (the default, follows the operating system),
 * Light or Dark, kept per browser. Picking one applies at once and leaves the menu open.
 */
function ThemeOptions() {
  const { choice, setChoice } = useTheme();
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
      <span id="user-menu-theme" className="label">
        Theme
      </span>
      {/* ARIA's owner for menu radio items is a group (a radiogroup owns plain radios). */}
      <div
        role="group"
        aria-labelledby="user-menu-theme"
        data-testid="theme-switch"
        className="flex rounded-btn border border-line bg-sunken p-0.5"
      >
        {THEMES.map(({ id, label, Icon }) => {
          const on = choice === id;
          return (
            <button
              key={id}
              type="button"
              role="menuitemradio"
              aria-checked={on}
              data-testid={`theme-${id}`}
              onClick={() => setChoice(id)}
              className={`flex min-h-8 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors duration-fast ${
                on ? "bg-surface text-heading shadow-e1" : "text-slate hover:text-heading"
              }`}
            >
              <Icon size={13} aria-hidden />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * AUTH_MODE=oidc: who signed in with Entra ID and the roles their groups grant. There is no
 * switcher; a role is granted in Entra, not chosen here.
 */
function SignedInMenu({ me, persona }: { me: Principal; persona: Persona }) {
  const [busy, setBusy] = useState(false);
  return (
    <div role="menu" className="card absolute right-0 top-14 z-50 w-[360px] overflow-hidden p-0 shadow-pop">
      <ThemeOptions />
      <div className="border-b border-line px-4 py-3" data-testid="user-menu-identity">
        <p className="text-md font-semibold text-heading">{me.name || me.upn}</p>
        <p className="break-all font-mono text-xs text-slate">{me.upn}</p>
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          {me.roles.length === 0 ? (
            <span className="rounded-full bg-sunken px-2 py-0.5 font-medium text-heading">no role</span>
          ) : (
            me.roles.map((r) => (
              <span key={r} className="rounded-full bg-sunken px-2 py-0.5 font-medium text-heading" data-testid={`role-${r}`}>
                {r}
              </span>
            ))
          )}
          {persona.gates.length ? (
            <span className="flex items-center gap-1 rounded-full bg-gate-bg px-2 py-0.5 font-medium text-gate">
              <ShieldCheck size={11} aria-hidden />
              {persona.gates.join(", ")}
            </span>
          ) : null}
        </p>
        <p className="mt-1.5 text-xs leading-snug text-slate">
          Acting as {persona.role}: {range(persona)}.
        </p>
      </div>
      <div className="flex items-center justify-between gap-3 bg-sunken px-4 py-2.5 text-xs text-slate">
        <span>Recorded as the actor on every gate decision.</span>
        <button
          type="button"
          data-testid="user-menu-signout"
          disabled={busy}
          className="flex shrink-0 items-center gap-1 font-medium text-accent-ink hover:underline"
          onClick={() => {
            setBusy(true);
            void signOutOidc();
          }}
        >
          <LogOut size={12} aria-hidden /> Sign out
        </button>
      </div>
    </div>
  );
}
