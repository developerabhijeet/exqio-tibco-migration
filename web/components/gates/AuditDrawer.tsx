"use client";

import { useEffect } from "react";
import { History, X } from "lucide-react";
import { useAudit } from "@/lib/queries";
import type { AuditEntry } from "@/lib/types";

function hashesOf(e: AuditEntry): { label: string; hash: string }[] {
  const d = e.detail ?? {};
  const out: { label: string; hash: string }[] = [];
  const spec = d.spec_hash ?? d.new_spec_hash;
  if (typeof spec === "string") out.push({ label: "spec", hash: spec });
  if (typeof d.design_hash === "string") out.push({ label: "design", hash: d.design_hash });
  return out;
}

function summary(e: AuditEntry): string | null {
  const d = e.detail ?? {};
  if (e.action === "rule edited") return `${d.rule_key ?? ""}: "${d.old_text ?? ""}" became "${d.new_text ?? ""}"`;
  if (e.action.startsWith("rule ")) return (d.rule_key as string) ?? null;
  if (e.action === "finding decided")
    return `${d.finding_key ?? ""}: ${d.decision ?? ""}${d.note ? `, note "${d.note}"` : ""}`;
  if (e.action === "gate reset") return `${d.reason ?? ""}${d.previous_state ? ` (was ${d.previous_state})` : ""}`;
  if (e.action.startsWith("gate ")) return (d.note as string) ?? null;
  if (e.action === "analysis completed")
    return `${d.rules ?? 0} rules, ${d.findings ?? 0} findings, provider ${d.provider ?? "none"}`;
  return null;
}

/** Who did what, when, against which spec hash, for the open process. Newest first. */
export function AuditDrawer({ processId, open, onClose }: { processId: string; open: boolean; onClose: () => void }) {
  const audit = useAudit(open ? processId : null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const rows = [...(audit.data ?? [])].reverse();
  return (
    <div className="fixed inset-0 z-50 flex justify-end print:hidden" role="presentation">
      <button type="button" aria-label="Close audit trail" className="absolute inset-0 bg-plum/20" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Audit trail"
        data-testid="audit-drawer"
        className="relative flex h-full w-[520px] max-w-full flex-col border-l border-line bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-line px-6 py-5">
          <div>
            <p className="eyebrow">Audit trail</p>
            <h2 className="panel-title mt-1">Decisions on this process</h2>
            <p className="mt-1 text-[13px] text-slate">Every rule, finding and gate decision, newest first.</p>
          </div>
          <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <ol className="flex-1 divide-y divide-line overflow-y-auto px-6">
          {audit.isLoading ? <li className="py-4 text-[14px] text-slate">Loading.</li> : null}
          {!audit.isLoading && rows.length === 0 ? (
            <li className="py-4 text-[14px] text-slate">No decisions recorded yet.</li>
          ) : null}
          {rows.map((e) => {
            const hashes = hashesOf(e);
            const s = summary(e);
            return (
              <li key={e.id} className="py-3 text-[14px]" data-testid="audit-row">
                <div className="flex flex-wrap items-center gap-x-2">
                  <History size={14} className="text-slate" aria-hidden />
                  <span className="font-semibold text-plum">{e.action}</span>
                  {e.gate ? <span className="font-mono text-[12px] text-gate">{e.gate}</span> : null}
                  <span className="ml-auto text-[12px] text-slate">{new Date(e.ts).toLocaleString()}</span>
                </div>
                <p className="mt-0.5 text-[13px] text-slate">
                  by <span className={e.actor_kind === "user" ? "font-medium text-ink" : ""}>{e.actor}</span>
                  {hashes.map((h) => (
                    <span key={h.label} className="ml-2 font-mono text-[11.5px]" title={h.hash}>
                      {h.label} {h.hash.slice(0, 12)}
                    </span>
                  ))}
                </p>
                {s ? <p className="mt-1 break-words text-[13px] text-ink">{s}</p> : null}
              </li>
            );
          })}
        </ol>
      </aside>
    </div>
  );
}
