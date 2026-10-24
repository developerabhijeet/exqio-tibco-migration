"use client";

// The left nav of Help & Support. "About this install" shows only when GET /system/status answers
// 200: the API decides who is an admin (the admin role in oidc, ops.sam in dev), so the UI asks
// rather than inferring it from the persona.
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { ABOUT, GUIDES } from "@/lib/help";
import { useAppState } from "@/lib/state";

/** The About data for the current user, or null when the API says they may not see it. */
export function useSystemStatus() {
  const { user } = useAppState();
  return useQuery({ queryKey: ["system-status", user], queryFn: api.systemStatus, retry: 0, staleTime: 15_000 });
}

export function HelpNav({ current }: { current: string }) {
  const status = useSystemStatus();
  const items = status.data ? [...GUIDES, ABOUT] : GUIDES;
  return (
    <nav data-testid="help-nav" aria-label="Help topics" className="sticky top-4 self-start">
      <ul className="card space-y-0.5 p-2">
        {items.map(({ slug, label }) => (
          <li key={slug}>
            <Link
              href={`/help/${slug}`}
              aria-current={slug === current ? "page" : undefined}
              className={`block rounded-btn px-3 py-2 text-[14px] font-medium transition-colors ${
                slug === current ? "bg-purple-lt text-purple-dk" : "text-slate hover:bg-warm hover:text-plum"
              }`}
            >
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function SupportFoot() {
  const { support_contact } = useBrand();
  if (!support_contact) return null;
  return (
    <p data-testid="support-contact" className="mt-10 border-t border-line pt-4 text-[14px] text-slate">
      Need help? Contact <span className="font-medium text-plum">{support_contact}</span>.
    </p>
  );
}
