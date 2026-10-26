"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, ClipboardCheck, Coins, Headphones, History, MoreHorizontal, Network, Settings } from "lucide-react";
import { useBrand } from "@/lib/brand";
import { useStepStates } from "@/lib/steps";
import { ProjectSelector } from "./ProjectSelector";
import { UserMenu } from "./UserMenu";

const LINKS = [
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/history", label: "History", icon: History },
  { href: "/memory", label: "Memory", icon: Network },
  { href: "/tokenomics", label: "Tokenomics", icon: Coins },
  { href: "/readiness", label: "Readiness", icon: ClipboardCheck },
  { href: "/help", label: "Help & Support", icon: Headphones },
];

const isActive = (path: string, href: string) => path === href || path.startsWith(`${href}/`);

/**
 * The secondary links below 1280 px: a disclosure (a button and the list it shows), closed by
 * Escape (focus back on the button), a click elsewhere, or following a link. ArrowDown on the
 * button opens it on the first link; ArrowUp / ArrowDown move between the links.
 */
function MoreMenu({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [focusFirst, setFocusFirst] = useState(false);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    if (focusFirst) {
      list.current?.querySelector<HTMLAnchorElement>("a")?.focus();
      setFocusFirst(false);
    }
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open, focusFirst]);
  const current = LINKS.some((l) => isActive(path, l.href));
  const move = (e: React.KeyboardEvent<HTMLUListElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const links = Array.from(list.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
    const i = links.indexOf(document.activeElement as HTMLAnchorElement);
    const next = e.key === "ArrowDown" ? (i + 1) % links.length : (i - 1 + links.length) % links.length;
    links[next]?.focus();
  };
  return (
    <div className="relative shrink-0 xl:hidden" ref={ref}>
      <button
        ref={button}
        type="button"
        aria-label="More"
        data-testid="topbar-more"
        aria-expanded={open}
        aria-controls="topbar-more-menu"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setFocusFirst(true);
            setOpen(true);
          }
        }}
        className={`flex items-center gap-1.5 rounded-btn px-3 py-2 text-md font-medium transition-colors duration-fast ${
          current ? "bg-white/15 text-white shadow-inset" : "text-lilac hover:bg-white/10 hover:text-white"
        }`}
      >
        <MoreHorizontal size={18} aria-hidden />
        <span className="max-sm:sr-only">More</span>
        <ChevronDown size={14} className={`transition-transform duration-fast ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open ? (
        <ul
          id="topbar-more-menu"
          ref={list}
          onKeyDown={move}
          className="absolute right-0 top-full z-40 mt-2 w-60 rounded-card border border-line bg-surface p-1.5 shadow-e2"
        >
          {LINKS.map(({ href, label, icon: Icon }) => {
            const active = isActive(path, href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`flex min-h-10 items-center gap-3 rounded-btn px-3 py-2 text-md font-medium transition-colors duration-fast ${
                    active ? "bg-accent-soft text-accent-ink" : "text-heading hover:bg-sunken"
                  }`}
                >
                  <Icon size={16} className={active ? "text-accent-ink" : "text-slate"} aria-hidden />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function PhasePill() {
  const steps = useStepStates();
  const current = steps.find((s) => s.status !== "approved" && s.status !== "complete") ?? steps[steps.length - 1];
  return (
    <span
      data-testid="phase-pill"
      className="whitespace-nowrap rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-lilac ring-1 ring-inset ring-white/15"
    >
      Phase: {current.label}
    </span>
  );
}

/**
 * The brand bar, full window width on the content's gutter. From 1280 px every secondary link sits
 * in the bar (labels from 1600 px; below that the icon, with the label as its accessible name and
 * tooltip); below 1280 they fold into "More". Below 768 px the project selector and the phase pill
 * wrap onto a second row, so the first keeps the brand, "More" and the user menu.
 */
export function TopBar() {
  const path = usePathname();
  const { brand_name } = useBrand();
  return (
    <header className="z-30 border-b border-black/20 bg-plum shadow-e2">
      <div className="flex w-full flex-wrap items-center gap-x-5 px-[var(--gutter)]">
        <Link href="/" className="flex h-16 shrink-0 items-center gap-3 rounded-btn">
          <img src="/brand/mark.svg" alt="" className="h-9 w-9" />
          <span className="leading-tight max-2xl:hidden">
            <span className="block font-display text-lg font-semibold tracking-[-0.01em] text-white">{brand_name} Migrate</span>
            <span className="block text-xs text-lilac">TIBCO BusinessWorks to .NET</span>
          </span>
          <span className="sr-only 2xl:hidden">{brand_name} Migrate</span>
        </Link>
        <div className="h-8 w-px bg-white/15 max-md:hidden" />
        {/* The second row under 768 px: last in order and the full width. */}
        <div
          className="flex min-w-0 items-center gap-4 max-md:order-last max-md:basis-full max-md:border-t max-md:border-white/10 max-md:py-3"
          data-testid="topbar-context"
        >
          <ProjectSelector />
          {/* Between 768 and 1023 px the first row has no room for it; under 768 it has its row. */}
          <div className="md:max-lg:hidden">
            <PhasePill />
          </div>
        </div>
        <div className="ml-auto flex h-16 min-w-0 items-center gap-1">
          <nav className="flex min-w-0 items-center gap-0.5 max-xl:hidden" aria-label="Secondary">
            {LINKS.map(({ href, label, icon: Icon }) => {
              const active = isActive(path, href);
              return (
                <Link
                  key={href}
                  href={href}
                  title={label}
                  aria-current={active ? "page" : undefined}
                  className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-btn px-2.5 py-2 text-md font-medium transition-colors duration-fast ${
                    active ? "bg-white/15 text-white shadow-inset" : "text-lilac hover:bg-white/10 hover:text-white"
                  }`}
                >
                  <Icon size={16} aria-hidden />
                  <span className="max-[1599px]:sr-only">{label}</span>
                </Link>
              );
            })}
          </nav>
          {/* Beside the nav, not in it, so the list is never clipped by the bar. */}
          <MoreMenu path={path} />
          <div className="ml-2 shrink-0">
            <UserMenu />
          </div>
        </div>
      </div>
    </header>
  );
}
