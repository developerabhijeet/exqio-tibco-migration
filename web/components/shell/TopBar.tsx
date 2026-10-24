"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ClipboardCheck, Coins, History, Settings } from "lucide-react";
import { useStepStates } from "@/lib/steps";
import { ProjectSelector } from "./ProjectSelector";
import { UserMenu } from "./UserMenu";

const LINKS = [
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/history", label: "History", icon: History },
  { href: "/tokenomics", label: "Tokenomics", icon: Coins },
  { href: "/readiness", label: "Readiness", icon: ClipboardCheck },
];

function PhasePill() {
  const steps = useStepStates();
  const current = steps.find((s) => s.status !== "approved" && s.status !== "complete") ?? steps[steps.length - 1];
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
  return (
    <header className="z-30 border-b border-black/20 bg-plum shadow-pop">
      <div className="flex h-16 w-full items-center gap-5 px-6">
        <Link href="/" className="flex shrink-0 items-center gap-3">
          <img src="/rsi-logo-white.svg" alt="RSI" className="h-9 w-auto" />
          <span className="leading-tight">
            <span className="block font-display text-[16.5px] font-semibold tracking-[-0.01em] text-white">
              ExiQO Migrate
            </span>
            <span className="block text-[11.5px] text-lilac">TIBCO BusinessWorks to .NET</span>
          </span>
        </Link>
        <div className="h-8 w-px bg-white/15" />
        <ProjectSelector />
        <PhasePill />
        <nav className="ml-auto flex items-center gap-1" aria-label="Secondary">
          {LINKS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-[13.5px] font-medium transition-colors ${
                path === href ? "bg-white/15 text-white shadow-inset" : "text-lilac hover:bg-white/10 hover:text-white"
              }`}
            >
              <Icon size={16} aria-hidden />
              {label}
            </Link>
          ))}
        </nav>
        <UserMenu />
      </div>
    </header>
  );
}
