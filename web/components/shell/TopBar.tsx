"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ClipboardCheck,
  Coins,
  Headphones,
  History,
  MoreHorizontal,
  Network,
  Settings,
} from "lucide-react";
import { useBrand } from "@/lib/brand";
import { useStepStates } from "@/lib/steps";
import { ProjectSelector } from "./ProjectSelector";
import { UserMenu } from "./UserMenu";

// `minor`: the least-used links, which fold into a "More" menu below 1360 px (unless open).
const LINKS = [
  { href: "/settings", label: "Settings", icon: Settings, minor: false },
  { href: "/history", label: "History", icon: History, minor: true },
  { href: "/memory", label: "Memory", icon: Network, minor: false },
  { href: "/tokenomics", label: "Tokenomics", icon: Coins, minor: false },
  {
    href: "/readiness",
    label: "Readiness",
    icon: ClipboardCheck,
    minor: false,
  },
  { href: "/help", label: "Help & Support", icon: Headphones, minor: true },
];

const isActive = (path: string, href: string) =>
  path === href || path.startsWith(`${href}/`);

/** The folded links, below 1360 px: a disclosure (a button and the list it shows), closed by
 * Escape (focus back on the button), a click elsewhere, or following a link. */
function MoreMenu({ links, path }: { links: typeof LINKS; path: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
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
  }, [open]);
  if (links.length === 0) return null;
  return (
    <div className="relative shrink-0 min-[1360px]:hidden" ref={ref}>
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls="topbar-more"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 rounded-btn px-2.5 py-1.5 text-[13.5px] font-medium text-lilac transition-colors hover:bg-white/10 hover:text-white"
      >
        <MoreHorizontal size={16} aria-hidden />
        More
        <ChevronDown
          size={13}
          className={`transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {open ? (
        <ul
          id="topbar-more"
          className="absolute right-0 top-full z-40 mt-2 w-52 rounded-card bg-white p-1.5 shadow-pop ring-1 ring-line"
        >
          {links.map(({ href, label, icon: Icon }) => (
            <li key={href}>
              <Link
                href={href}
                className="flex items-center gap-2.5 rounded-btn px-3 py-2 text-[14px] font-medium text-plum transition-colors hover:bg-warm"
              >
                <Icon size={16} className="text-slate" aria-hidden />
                {label}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function PhasePill() {
  const steps = useStepStates();
  const current =
    steps.find((s) => s.status !== "approved" && s.status !== "complete") ??
    steps[steps.length - 1];
  return (
    <span
      data-testid="phase-pill"
      className="whitespace-nowrap rounded-full bg-white/10 px-3 py-1 text-[11.5px] font-semibold text-lilac ring-1 ring-inset ring-white/15"
    >
      Phase: {current.label}
    </span>
  );
}

export function TopBar() {
  const path = usePathname();
  const { brand_name } = useBrand();
  return (
    <header className="z-30 border-b border-black/20 bg-plum shadow-pop">
      <div className="flex h-16 w-full items-center gap-5 px-6">
        <Link href="/" className="flex shrink-0 items-center gap-3">
          <img src="/brand/mark.svg" alt="" className="h-9 w-9" />
          <span className="leading-tight max-[1599px]:hidden">
            <span className="block font-display text-[16.5px] font-semibold tracking-[-0.01em] text-white">
              {brand_name} Migrate
            </span>
            <span className="block text-[11.5px] text-lilac">
              TIBCO BusinessWorks to .NET
            </span>
          </span>
        </Link>
        <div className="h-8 w-px bg-white/15" />
        <ProjectSelector />
        <div className="max-[1599px]:hidden">
          <PhasePill />
        </div>
        {/* Every link keeps its label down to 1360 px (below 1600 the brand name and the phase
            pill make room). Below 1360 the least-used links (History, Help) fold into "More"
            unless one is open; below 1180 the other links show their icon only, except the open
            one, which keeps its label. If even that does not fit, the nav scrolls on its own
            rather than pushing the user menu off the bar. */}
        <div className="ml-auto flex min-w-0 items-center gap-0.5">
          <nav
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto"
            aria-label="Secondary"
          >
            {LINKS.map(({ href, label, icon: Icon, minor }) => {
              const active = isActive(path, href);
              return (
                <Link
                  key={href}
                  href={href}
                  title={label}
                  aria-current={active ? "page" : undefined}
                  className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-btn px-2.5 py-1.5 text-[13.5px] font-medium transition-colors ${
                    active
                      ? "bg-white/15 text-white shadow-inset"
                      : "text-lilac hover:bg-white/10 hover:text-white"
                  } ${minor && !active ? "max-[1359px]:hidden" : ""}`}
                >
                  <Icon size={16} aria-hidden />
                  <span className={active ? "" : "max-[1179px]:sr-only"}>
                    {label}
                  </span>
                </Link>
              );
            })}
          </nav>
          {/* Beside the nav, not in it: the nav's own overflow would clip the list. */}
          <MoreMenu
            links={LINKS.filter((l) => l.minor && !isActive(path, l.href))}
            path={path}
          />
        </div>
        <div className="shrink-0">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
