"use client";

import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Bot, Cog, Gavel } from "lucide-react";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { Panel } from "@/components/tabs/build/Panel";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { BandPill, Pill } from "@/components/ui/Pill";
import { useHistory } from "@/lib/buildQueries";
import { useAppState } from "@/lib/state";
import type { TimelineEntry, Totals } from "@/lib/buildTypes";

const fmt = new Intl.NumberFormat("en-US");
const usd = (n: number) => `$${n.toFixed(n < 1 ? 4 : 2)}`;

const MEASURES = {
  tokens: { label: "Tokens", get: (t: Totals) => t.tokens_in + t.tokens_out, show: (n: number) => fmt.format(n) },
  calls: { label: "Model calls", get: (t: Totals) => t.model_calls, show: (n: number) => fmt.format(n) },
  cost: { label: "Cost", get: (t: Totals) => t.cost_usd, show: usd },
  jobs: { label: "Jobs", get: (t: Totals) => t.jobs, show: (n: number) => fmt.format(n) },
} as const;
type Measure = keyof typeof MEASURES;

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card px-5 py-4">
      <p className="label">{label}</p>
      <p className="mt-1 text-[26px] font-semibold text-plum">{value}</p>
      {sub ? <p className="text-[12.5px] text-slate">{sub}</p> : null}
    </div>
  );
}

/** A raw error (often a whole HTTP body) on one line, with a toggle for the full text. */
function ErrorText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const oneLine = text.replace(/\s+/g, " ").trim();
  return (
    <p className="flex min-w-0 items-start gap-2 text-error" data-testid="timeline-error">
      <span className={open ? "whitespace-pre-wrap break-words" : "min-w-0 truncate"} title={open ? undefined : oneLine}>
        {open ? text : oneLine}
      </span>
      {oneLine.length > 90 || text.includes("\n") ? (
        <button type="button" className="shrink-0 text-[12px] font-medium text-purple hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "hide" : "show"}
        </button>
      ) : null}
    </p>
  );
}

function entryText(e: TimelineEntry): { title: string; detail: string; error: string | null } {
  if (e.kind === "job")
    return {
      title: `${e.job_kind} job ${e.status}`,
      detail: [e.process_name, e.stage ? `stage ${e.stage}` : null, e.duration_ms != null ? `${Math.round(e.duration_ms)} ms` : null]
        .filter(Boolean)
        .join(" · "),
      error: e.error || null,
    };
  if (e.kind === "model_call")
    return {
      title: `${e.agent} on ${e.provider} ${e.model}`,
      detail: [
        e.process_name,
        `tier ${e.tier}`,
        `${fmt.format(e.tokens_in)} in, ${fmt.format(e.tokens_out)} out`,
        e.tokens_cached ? `${fmt.format(e.tokens_cached)} cached` : null,
        usd(e.cost_usd),
        `${Math.round(e.latency_ms)} ms`,
        e.fallback_from ? `fallback from ${e.fallback_from}` : null,
        e.ok ? null : "failed",
      ]
        .filter(Boolean)
        .join(" · "),
      error: e.ok ? null : e.error || null,
    };
  return { title: `${e.actor}: ${e.action}${e.gate ? ` ${e.gate}` : ""}`, detail: e.process_name ?? "", error: null };
}

const KIND_ICON = { job: Cog, model_call: Bot, decision: Gavel };
const KIND_LABEL = { job: "jobs", model_call: "model calls", decision: "decisions" };

export default function Page() {
  const { projectId } = useAppState();
  const history = useHistory(projectId);
  const [kinds, setKinds] = useState<Set<string>>(new Set(["job", "model_call", "decision"]));
  const [phase, setPhase] = useState<string>("all");
  const [measure, setMeasure] = useState<Measure>("tokens");
  const h = history.data;

  const timeline = useMemo(
    () =>
      [...(h?.timeline ?? [])]
        .filter((e) => kinds.has(e.kind) && (phase === "all" || ("phase" in e && e.phase === phase)))
        .reverse(),
    [h, kinds, phase],
  );
  const bars = useMemo(
    () => (h ? h.phases.map((p) => ({ phase: p, value: MEASURES[measure].get(h.totals.by_phase[p] ?? ({} as Totals)) || 0 })) : []),
    [h, measure],
  );

  return (
    <SecondaryScreen title="History" description="Jobs, model calls and decisions of this project on one timeline, with tokens and cost.">
      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar.</Empty>
      ) : history.error ? (
        <ErrorNote error={history.error} />
      ) : !h ? null : (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-4">
            <Tile label="Tokens in" value={fmt.format(h.totals.all.tokens_in)} sub={`${fmt.format(h.totals.all.tokens_cached)} cached`} />
            <Tile label="Tokens out" value={fmt.format(h.totals.all.tokens_out)} sub={`cache hit rate ${Math.round(h.totals.cache_hit_rate * 100)}%`} />
            <Tile label="Cost" value={usd(h.totals.all.cost_usd)} sub={`${h.totals.all.model_calls} model calls, ${h.totals.all.failed_calls} failed`} />
            <Tile label="Jobs and decisions" value={`${h.totals.all.jobs} / ${h.totals.all.decisions}`} sub="jobs / human decisions" />
          </div>

          <div className="grid items-start gap-4 xl:grid-cols-2">
            <Panel
              title="By phase"
              eyebrow="Totals"
              testId="history-phases"
              actions={
                <select
                  aria-label="Measure"
                  className="rounded-btn border border-line bg-white px-2 py-1 text-[13px]"
                  value={measure}
                  onChange={(e) => setMeasure(e.target.value as Measure)}
                >
                  {Object.entries(MEASURES).map(([k, m]) => (
                    <option key={k} value={k}>
                      {m.label}
                    </option>
                  ))}
                </select>
              }
            >
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                    <CartesianGrid vertical={false} stroke="#E2DFD6" />
                    <XAxis dataKey="phase" tick={{ fontSize: 12, fill: "#425466" }} axisLine={{ stroke: "#E2DFD6" }} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: "#425466" }} axisLine={false} tickLine={false} width={64} tickFormatter={(n: number) => MEASURES[measure].show(n)} />
                    <Tooltip
                      cursor={{ fill: "#F1E9FC" }}
                      formatter={(v: number) => [MEASURES[measure].show(v), MEASURES[measure].label]}
                      contentStyle={{ borderRadius: 6, borderColor: "#E2DFD6", fontSize: 12 }}
                    />
                    <Bar dataKey="value" fill="#9B5DE5" radius={[4, 4, 0, 0]} maxBarSize={48} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <table className="mt-2 w-full text-[12.5px]">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-slate">
                    <th className="py-1">Phase</th>
                    <th className="py-1 text-right">Jobs</th>
                    <th className="py-1 text-right">Calls</th>
                    <th className="py-1 text-right">Tokens</th>
                    <th className="py-1 text-right">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {h.phases.map((p) => {
                    const t = h.totals.by_phase[p];
                    return t ? (
                      <tr key={p}>
                        <td className="py-1 font-medium text-plum">{p}</td>
                        <td className="py-1 text-right">{t.jobs}</td>
                        <td className="py-1 text-right">{t.model_calls}</td>
                        <td className="py-1 text-right">{fmt.format(t.tokens_in + t.tokens_out)}</td>
                        <td className="py-1 text-right">{usd(t.cost_usd)}</td>
                      </tr>
                    ) : null;
                  })}
                </tbody>
              </table>
            </Panel>

            <Panel title="By workflow" eyebrow="Complexity band" testId="history-workflows">
              <div className="mb-3 flex flex-wrap gap-2 text-[12.5px]">
                {Object.entries(h.totals.by_band).map(([band, t]) => (
                  <span key={band} className="flex items-center gap-1.5 rounded-btn border border-line px-2 py-1">
                    <BandPill band={band} /> {t.workflows ?? 0} workflows · {fmt.format(t.tokens_in + t.tokens_out)} tokens · {usd(t.cost_usd)}
                  </span>
                ))}
              </div>
              <div className="max-h-[360px] overflow-auto rounded-btn border border-line">
                <table className="w-full min-w-[560px] text-[12.5px]">
                  <thead className="sticky top-0">
                    <tr className="table-head">
                      <th className="px-3 py-2">Workflow</th>
                      <th className="px-3 py-2">Band</th>
                      <th className="px-3 py-2">Gates</th>
                      <th className="px-3 py-2 text-right">Calls</th>
                      <th className="px-3 py-2 text-right">Tokens</th>
                      <th className="px-3 py-2 text-right">Cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {[...h.workflows]
                      .sort((a, b) => (b.complexity_score ?? 0) - (a.complexity_score ?? 0))
                      .map((w) => (
                        <tr key={w.process_id}>
                          <td className="px-3 py-1.5 font-medium text-plum">{w.name}</td>
                          <td className="px-3 py-1.5">
                            <BandPill band={w.complexity_band} />{" "}
                            <span className="text-slate">{w.complexity_score?.toFixed(1) ?? ""}</span>
                          </td>
                          <td className="px-3 py-1.5">
                            {Object.entries(w.gates)
                              .filter(([, st]) => st === "approved")
                              .map(([g]) => g)
                              .join(" ") || <span className="text-slate">none</span>}
                          </td>
                          <td className="px-3 py-1.5 text-right">{w.model_calls}</td>
                          <td className="px-3 py-1.5 text-right">{fmt.format(w.tokens_in + w.tokens_out)}</td>
                          <td className="px-3 py-1.5 text-right">{usd(w.cost_usd)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>

          <Panel
            title="Timeline"
            eyebrow="Newest first"
            testId="history-timeline"
            actions={
              <>
                {(["job", "model_call", "decision"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={kinds.has(k)}
                    onClick={() =>
                      setKinds((s) => {
                        const n = new Set(s);
                        if (n.has(k)) n.delete(k);
                        else n.add(k);
                        return n;
                      })
                    }
                    className={`rounded-full border px-2.5 py-0.5 text-[12.5px] ${kinds.has(k) ? "border-purple bg-purple-lt text-plum" : "border-line text-slate"}`}
                  >
                    {KIND_LABEL[k]}
                  </button>
                ))}
                <select aria-label="Phase" className="rounded-btn border border-line bg-white px-2 py-1 text-[13px]" value={phase} onChange={(e) => setPhase(e.target.value)}>
                  <option value="all">all phases</option>
                  {h.phases.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </>
            }
          >
            {timeline.length === 0 ? (
              <p className="text-[14px] text-slate">Nothing matches the filters.</p>
            ) : (
              <ol className="max-h-[560px] divide-y divide-line overflow-y-auto">
                {timeline.map((e) => {
                  const Icon = KIND_ICON[e.kind];
                  const t = entryText(e);
                  return (
                    <li key={`${e.kind}:${e.id}`} className="flex gap-3 py-2 text-[13px]">
                      <Icon size={15} className={`mt-0.5 shrink-0 ${e.kind === "model_call" ? "text-ai" : e.kind === "decision" ? "text-gate" : "text-slate"}`} aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-ink">
                          {t.title}{" "}
                          {"phase" in e ? (
                            <Pill tone="det" className="ml-1">
                              {e.phase}
                            </Pill>
                          ) : null}
                        </p>
                        {t.detail ? <p className="break-words text-slate">{t.detail}</p> : null}
                        {t.error ? <ErrorText text={t.error} /> : null}
                      </div>
                      <time className="shrink-0 text-[12px] text-slate">{new Date(e.ts).toLocaleString()}</time>
                    </li>
                  );
                })}
              </ol>
            )}
          </Panel>
        </div>
      )}
    </SecondaryScreen>
  );
}
