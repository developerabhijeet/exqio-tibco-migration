"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowUpRight, Loader2 } from "lucide-react";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { useReadiness } from "@/lib/buildQueries";
import { useAppState } from "@/lib/state";
import type { ReadinessRow } from "@/lib/buildTypes";

const STATUS: Record<string, { tone: "gate" | "decision" | "error"; text: string }> = {
  met: { tone: "gate", text: "met" },
  partial: { tone: "decision", text: "partial" },
  missing: { tone: "error", text: "missing" },
};

type Target = { href: string; process?: string | null; ask?: boolean };

/** Where an evidence route lives on screen: a step (with its process and panel) or a page. */
async function target(ref: string, kind: string): Promise<Target> {
  const path = ref.split("?")[0];
  if (path.startsWith("/settings")) return { href: "/settings" };
  if (path.startsWith("/history")) return { href: "/history" };
  if (path.startsWith("/ask")) return { href: "/", ask: true };
  let m = path.match(/^\/projects\/[^/]+\/(report|domains|graph|files)/);
  if (m) return { href: m[1] === "report" || m[1] === "files" ? "/?step=upload" : "/?step=discover" };
  m = path.match(/^\/processes\/([^/]+)\/(canvas|scenarios|test-data|analysis|design)/);
  if (m) {
    const step = { canvas: "discover", scenarios: "verify", "test-data": "verify", analysis: "validate", design: "design" }[m[2]];
    const hash = m[2] === "scenarios" ? "#scenarios" : m[2] === "test-data" ? "#test-data" : "";
    return { href: `/?step=${step}&process=${m[1]}${hash}`, process: m[1] };
  }
  m = path.match(/^\/builds\/([^/]+)(?:\/(\w[\w-]*))?/);
  if (m) {
    const build = await api.build(m[1]);
    const sub = m[2] ?? kind;
    const hash = sub === "tests" ? "#tests" : sub === "infra" ? "#infra" : sub === "metrics" ? "#metrics" : sub === "parity" ? "#parity" : "";
    const step = sub === "parity" ? "verify" : sub === "plan" ? "deploy" : "build";
    return { href: `/?step=${step}&process=${build.process_id}${hash}`, process: build.process_id };
  }
  return { href: "/" };
}

function Row({ row }: { row: ReadinessRow }) {
  const router = useRouter();
  const { setAskOpen } = useAppState();
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = STATUS[row.status] ?? { tone: "decision" as const, text: row.status };
  const open = async (i: number) => {
    setBusy(i);
    setError(null);
    try {
      const t = await target(row.evidence[i].ref, row.evidence[i].kind);
      if (t.ask) setAskOpen(true);
      router.push(t.href);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <tr data-testid="readiness-row" data-status={row.status} data-key={row.key} className="align-top">
      <td className="px-4 py-3 font-medium text-plum">{row.expectation}</td>
      <td className="px-4 py-3">
        <Pill tone={s.tone}>{s.text}</Pill>
      </td>
      <td className="px-4 py-3">
        <ul className="space-y-1">
          {row.evidence.map((e, i) => (
            <li key={`${e.ref}:${i}`}>
              <button
                type="button"
                data-testid="evidence-link"
                onClick={() => open(i)}
                className="flex items-start gap-1 text-left text-[13px] text-purple hover:underline"
                title={e.ref}
              >
                {busy === i ? <Loader2 size={12} className="mt-1 shrink-0 animate-spin" aria-hidden /> : <ArrowUpRight size={12} className="mt-1 shrink-0" aria-hidden />}
                {e.label}
              </button>
            </li>
          ))}
          {row.evidence.length === 0 ? <li className="text-[13px] text-slate">no evidence yet</li> : null}
        </ul>
        {error ? <p className="mt-1 text-[12px] text-error">{error}</p> : null}
      </td>
      <td className="px-4 py-3 text-[13px] text-slate">{row.gap ?? ""}</td>
    </tr>
  );
}

export default function Page() {
  const { projectId } = useAppState();
  const readiness = useReadiness(projectId);
  const r = readiness.data;
  return (
    <SecondaryScreen title="Readiness" description="Each expectation from the SME review with its status, the evidence on screen and the gap.">
      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar.</Empty>
      ) : readiness.error ? (
        <ErrorNote error={readiness.error} />
      ) : !r ? null : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2" data-testid="readiness-summary">
            <Pill tone="gate">{r.summary.met} met</Pill>
            <Pill tone="decision">{r.summary.partial} partial</Pill>
            <Pill tone="error">{r.summary.missing} missing</Pill>
            <span className="text-[13px] text-slate">Status is computed from stored evidence; select a link to open it.</span>
          </div>
          <section className="card overflow-x-auto">
            <table className="w-full min-w-[900px] text-[14px]">
              <thead>
                <tr className="table-head">
                  <th className="w-[220px] px-4 py-2.5">Expectation</th>
                  <th className="w-[110px] px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Evidence</th>
                  <th className="px-4 py-2.5">Gap</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {r.rows.map((row) => (
                  <Row key={row.key} row={row} />
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
    </SecondaryScreen>
  );
}
