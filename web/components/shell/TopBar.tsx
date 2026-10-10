"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ClipboardCheck, History, Settings } from "lucide-react";
import { useStepStates } from "@/lib/steps";
import { ProjectSelector } from "./ProjectSelector";
import { UserMenu } from "./UserMenu";

const LINKS = [
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/history", label: "History", icon: History },
  { href: "/readiness", label: "Readiness", icon: ClipboardCheck },
];

function PhasePill() {
  const steps = useStepStates();
  const current = steps.find((s) => s.status !== "approved" && s.status !== "complete") ?? steps[steps.length - 1];
  return (
    <span
      data-testid="phase-pill"
      className="whitespace-nowrap rounded-full bg-purple-lt px-3 py-1 text-[12px] font-semibold text-plum"
    >
      Phase: {current.label}
    </span>
  );
}

export function TopBar() {
  const path = usePathname();
  return (
    <header className="bg-plum">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-6 px-6">
        <Link href="/" className="flex shrink-0 items-center gap-3">
          <img src="/rsi-logo-white.svg" alt="RSI" className="h-9 w-auto" />
          <span className="leading-tight">
            <span className="block text-[17px] font-semibold text-white">ExiQO Migrate</span>
            <span className="block text-[12px] text-lilac">TIBCO BusinessWorks to .NET</span>
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
              className={`flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-[14px] ${
                path === href ? "bg-white/15 text-white" : "text-lilac hover:bg-white/10 hover:text-white"
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
