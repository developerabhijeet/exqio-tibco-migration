"use client";

import { Eye, Lock } from "lucide-react";
import { usePermission } from "@/lib/personas";
import type { StepKey } from "@/lib/state";

/**
 * Says, above a step that is read-only for the current persona, that it is read-only and who owns
 * it. Renders nothing on a step the persona may act in, so the banner is only ever present when a
 * control below it is disabled.
 */
export function RoleNotice({ step }: { step: StepKey }) {
  const { canAct, reason, persona } = usePermission(step);
  if (canAct) return null;
  return (
    <div
      role="status"
      data-testid="role-notice"
      data-persona={persona.id}
      className="flex items-start gap-3 rounded-card border border-decision/30 bg-decision-bg px-4 py-3"
    >
      <Eye size={17} className="mt-0.5 shrink-0 text-decision" aria-hidden />
      <p className="text-[13.5px] leading-relaxed text-ink">
        <span className="font-semibold text-decision">View only.</span> {reason}
      </p>
    </div>
  );
}

/** The lock a disabled control carries, so the reason is visible and not only in a tooltip. */
export function RoleLock({ reason }: { reason: string | undefined }) {
  if (!reason) return null;
  return <Lock size={14} aria-hidden className="shrink-0 opacity-70" />;
}
