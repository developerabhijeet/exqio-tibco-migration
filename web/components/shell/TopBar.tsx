"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ClipboardCheck, Coins, Headphones, History, Network, Settings } from "lucide-react";
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

export function TopBar() {
  const path = usePathname();
  const { brand_name } = useBrand();
  return (
    <header className="z-30 border-b border-black/20 bg-plum shadow-pop">
      <div className="flex h-16 w-full items-center gap-5 px-6">
        <Link href="/" className="flex shrink-0 items-center gap-3">
          <img src="/brand/mark.svg" alt="" className="h-9 w-9" />
          <span className="leading-tight max-xl:hidden">
            <span className="block font-display text-lg font-semibold tracking-[-0.01em] text-white">
              {brand_name} Migrate
            </span>
            <span className="block text-xs text-lilac">TIBCO BusinessWorks to .NET</span>
          </span>
        </Link>
        <div className="h-8 w-px bg-white/15" />
        <ProjectSelector />
        <div className="max-xl:hidden">
          <PhasePill />
        </div>
        {/* Narrower than 1600 px the links keep their icons and drop their labels (still their
            accessible names, and a tooltip); below 1280 px the brand line and the phase pill go
            too. If even that does not fit, the nav scrolls on its own rather than pushing the
            user menu off the bar. */}
        <nav className="ml-auto flex min-w-0 items-center gap-0.5 overflow-x-auto" aria-label="Secondary">
          {LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              title={label}
              className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-btn px-2.5 py-1.5 text-md font-medium transition-colors ${
                path === href || path.startsWith(`${href}/`) ? "bg-white/15 text-white shadow-inset" : "text-lilac hover:bg-white/10 hover:text-white"
              }`}
            >
              <Icon size={16} aria-hidden />
              <span className="max-[1599px]:sr-only">{label}</span>
            </Link>
          ))}
        </nav>
        <div className="shrink-0">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
