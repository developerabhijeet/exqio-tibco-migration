"use client";

// Tokenomics: what each agent costs. Every model call already records the agent identity that made
// it (the `stage` column), its tokens in, out and cached, its price and whether it fell back to
// another provider; this screen is that table rolled up two ways — by agent across the project, and
// per session, a session being one job (or the Ask calls made outside any job).
import { useMemo, useState } from "react";
import { Bot, ChevronRight, Coins, Gauge, Layers, RotateCcw, TriangleAlert } from "lucide-react";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { Panel } from "@/components/tabs/build/Panel";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useHistory, useResetTokenomics } from "@/lib/buildQueries";
import { useAppState } from "@/lib/state";
import type { AgentSession, AgentTotals } from "@/lib/buildTypes";

const fmt = new Intl.NumberFormat("en-US");
const usd = (n: number) => `$${n.toFixed(n < 1 ? 4 : 2)}`;
const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);

/** The agents, in the order a migration meets them, so the table reads like the pipeline. */
const AGENT_ORDER = ["analyst", "risk", "challenger", "designer", "test_engineer", "builder",
  "reviewer", "repair", "namer", "ask", "eval"];
const AGENT_LABELS: Record<string, string> = {
  analyst: "Workflow Analyst",
  risk: "Risk Reviewer",
  challenger: "Independent Challenger",
  designer: "Solution Designer",
  test_engineer: "Test Engineer",
  builder: "Code Builder",
  reviewer: "Review panel",
  repair: "Repair agent",
  namer: "Namer",
  ask: "Ask",
  eval: "Provider evaluation",
};
const label = (agent: string) => AGENT_LABELS[agent] ?? agent;

function Tile({ label: name, value, sub, icon: Icon }: {
  label: string;
  value: string;
  sub?: string;
  icon: typeof Coins;
}) {
  return (
    <div className="card px-5 py-4">
      <p className="label flex items-center gap-1.5">
        <Icon size={14} aria-hidden /> {name}
      </p>
      <p className="mt-1 text-[26px] font-semibold text-plum">{value}</p>
      {sub ? <p className="text-[12.5px] text-slate">{sub}</p> : null}
    </div>
  );
}

/** A proportion bar: one agent's share of the project's spend, read at a glance. */
function Share({ value, of }: { value: number; of: number }) {
  const pct = of > 0 ? (value / of) * 100 : 0;
  return (
    <span className="flex items-center gap-2">
      <span aria-hidden className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-warm">
        <span className="block h-full rounded-full bg-purple" style={{ width: `${Math.max(pct, 1.5)}%` }} />
      </span>
      <span className="tabular-nums text-[12.5px] text-slate">{pct.toFixed(0)}%</span>
    </span>
  );
}

function AgentTable({ rows, totalCost }: { rows: AgentTotals[]; totalCost: number }) {
  if (rows.length === 0) return <p className="px-4 py-3 text-[14px] text-slate">No model calls yet.</p>;
  const ordered = [...rows].sort((a, b) => {
    const ia = AGENT_ORDER.indexOf(a.agent);
    const ib = AGENT_ORDER.indexOf(b.agent);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13.5px]" data-testid="agent-costs">
        <thead className="border-b border-line text-left text-slate">
          <tr>
            <th className="px-3 py-2">Agent</th>
            <th className="px-3 py-2 text-right">Calls</th>
            <th className="px-3 py-2 text-right">Tokens in</th>
            <th className="px-3 py-2 text-right">Tokens out</th>
            <th className="px-3 py-2 text-right">Cached</th>
            <th className="px-3 py-2 text-right">Cost</th>
            <th className="px-3 py-2">Share</th>
            <th className="px-3 py-2 text-right">Avg latency</th>
            <th className="px-3 py-2">Models</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((r) => (
            <tr key={r.agent} className="border-b border-line/60 last:border-0" data-agent={r.agent}>
              <td className="px-3 py-2 font-medium text-plum">
                <span className="flex items-center gap-2">
                  <Bot size={15} className="text-purple" aria-hidden />
                  {label(r.agent)}
                  {r.failed_calls > 0 ? (
                    <span title={`${r.failed_calls} failed call(s)`}>
                      <TriangleAlert size={14} className="text-error" aria-hidden />
                    </span>
                  ) : null}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{fmt.format(r.model_calls)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmt.format(r.tokens_in)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmt.format(r.tokens_out)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-slate">
                {r.tokens_cached > 0 ? fmt.format(r.tokens_cached) : "n/a"}
              </td>
              <td className="px-3 py-2 text-right font-semibold tabular-nums text-plum">{usd(r.cost_usd)}</td>
              <td className="px-3 py-2"><Share value={r.cost_usd} of={totalCost} /></td>
              <td className="px-3 py-2 text-right tabular-nums text-slate">{secs(r.latency_ms_avg)}</td>
              <td className="px-3 py-2 text-slate">
                <span className="flex flex-wrap gap-1">
                  {r.models.map((m) => <Pill key={m} tone="det">{m}</Pill>)}
                  {r.fallbacks > 0 ? <Pill tone="decision">{r.fallbacks} fallback</Pill> : null}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One job, expandable to the agents that ran inside it. */
function SessionRow({ session, totalCost }: { session: AgentSession; totalCost: number }) {
  const [open, setOpen] = useState(false);
  const t = session.totals;
  const when = session.started_at ? new Date(session.started_at).toLocaleString() : "n/a";
  return (
    <li className="border-b border-line/60 last:border-0" data-testid="session-row">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-warm"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ChevronRight size={15} className={`shrink-0 text-slate transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        <span className="w-24 shrink-0 font-medium text-plum">{session.job_kind}</span>
        <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate">
          {session.process_name ?? "whole project"} · {when}
          {session.status && session.status !== "done" ? ` · ${session.status}` : ""}
        </span>
        <span className="shrink-0 text-[13px] tabular-nums text-slate">{fmt.format(t.model_calls)} calls</span>
        <span className="w-20 shrink-0 text-right text-[13px] tabular-nums text-slate">
          {fmt.format(t.tokens_in + t.tokens_out)} tok
        </span>
        <span className="w-20 shrink-0 text-right font-semibold tabular-nums text-plum">{usd(t.cost_usd)}</span>
      </button>
      {open ? (
        <div className="bg-warm/40 px-4 pb-3 pt-1">
          <AgentTable rows={session.by_agent} totalCost={t.cost_usd || totalCost} />
        </div>
      ) : null}
    </li>
  );
}

/** Clear the meter. The jobs, gates and builds behind it are untouched, and the reset is audited. */
function ResetMeter({ projectId, calls }: { projectId: string; calls: number }) {
  const reset = useResetTokenomics(projectId);
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <span className="flex items-center gap-2 text-[13.5px]">
        <span className="text-slate">Clear {fmt.format(calls)} model calls?</span>
        <button
          type="button"
          className="btn border border-line bg-white px-3 py-1 text-error hover:bg-error-bg"
          data-testid="tokenomics-reset-confirm"
          disabled={reset.isPending}
          onClick={() => reset.mutate(undefined, { onSettled: () => setConfirming(false) })}
        >
          {reset.isPending ? "Clearing…" : "Clear"}
        </button>
        <button type="button" className="btn-ghost" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      className="btn-ghost flex items-center gap-1.5"
      data-testid="tokenomics-reset"
      onClick={() => setConfirming(true)}
    >
      <RotateCcw size={14} aria-hidden /> Reset
    </button>
  );
}

export default function TokenomicsPage() {
  const { projectId } = useAppState();
  const history = useHistory(projectId);
  const data = history.data ?? null;

  const { agents, sessions, totals } = useMemo(() => ({
    agents: data?.by_agent ?? [],
    sessions: data?.sessions ?? [],
    totals: data?.totals.all ?? null,
  }), [data]);

  const dearest = agents.length > 0 ? [...agents].sort((a, b) => b.cost_usd - a.cost_usd)[0] : null;
  const cached = totals?.tokens_cached ?? 0;
  const cacheRate = totals && totals.tokens_in > 0 ? (cached / totals.tokens_in) * 100 : null;

  return (
    <SecondaryScreen
      title="Tokenomics"
      description="What every agent spent on this project: tokens in and out, and the cost of each session."
    >
      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar to see its spend.</Empty>
      ) : history.isError ? (
        <ErrorNote error={history.error} />
      ) : history.isLoading || !totals ? (
        <p className="text-slate">Loading…</p>
      ) : totals.model_calls === 0 ? (
        <Empty title="No model calls yet">Run a step that uses a model and its cost shows up here.</Empty>
      ) : (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <p className="text-[13.5px] text-slate">
              Counting since the project was created. Resetting clears the model calls only; the runs,
              gates and builds stay, and the reset goes on the audit trail.
            </p>
            <ResetMeter projectId={projectId} calls={totals.model_calls} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Tile
              icon={Coins}
              label="Total cost"
              value={usd(totals.cost_usd)}
              sub={`${fmt.format(totals.model_calls)} model calls`}
            />
            <Tile
              icon={Layers}
              label="Tokens"
              value={fmt.format(totals.tokens_in + totals.tokens_out)}
              sub={`${fmt.format(totals.tokens_in)} in · ${fmt.format(totals.tokens_out)} out`}
            />
            <Tile
              icon={Gauge}
              label="Cached input"
              value={cacheRate === null ? "n/a" : `${cacheRate.toFixed(0)}%`}
              sub={cached > 0 ? `${fmt.format(cached)} tokens read from cache` : "no cache hits yet"}
            />
            <Tile
              icon={Bot}
              label="Dearest agent"
              value={dearest ? label(dearest.agent) : "n/a"}
              sub={dearest ? `${usd(dearest.cost_usd)} over ${fmt.format(dearest.model_calls)} calls` : undefined}
            />
          </div>

          <Panel
            title="Cost by agent"
            eyebrow="Every model call, grouped by the agent that made it"
            testId="by-agent"
          >
            <AgentTable rows={agents} totalCost={totals.cost_usd} />
          </Panel>

          <Panel
            title="Sessions"
            eyebrow="One row per run; open a row for the agents inside it"
            testId="sessions"
          >
            {sessions.length === 0 ? (
              <p className="px-4 py-3 text-[14px] text-slate">No sessions yet.</p>
            ) : (
              <ul>
                {sessions.map((s) => (
                  <SessionRow key={`${s.job_id ?? "ask"}-${s.started_at}`} session={s} totalCost={totals.cost_usd} />
                ))}
              </ul>
            )}
          </Panel>
        </div>
      )}
    </SecondaryScreen>
  );
}
