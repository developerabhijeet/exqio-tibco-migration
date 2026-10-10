"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Save } from "lucide-react";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { Panel, errorText } from "@/components/tabs/build/Panel";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { useSettingsView } from "@/lib/buildQueries";
import { useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import type { SettingsView } from "@/lib/buildTypes";

const TIERS = ["strong", "standard", "efficient"] as const;
const CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"];

function Presence({ ok, yes = "configured", no = "missing" }: { ok: boolean; yes?: string; no?: string }) {
  return <Pill tone={ok ? "gate" : "det"}>{ok ? yes : no}</Pill>;
}

function Providers({ s }: { s: SettingsView }) {
  return (
    <Panel
      title="Providers"
      eyebrow="Presence only, never values"
      testId="providers"
      actions={
        <span className="flex items-center gap-2 text-[13px]">
          <span className="label">Choice</span>
          <Pill tone="purple">{s.provider.choice}</Pill>
          <span className="label">Active</span>
          <Pill tone={s.provider.active === "none" ? "error" : "gate"}>{s.provider.active}</Pill>
        </span>
      }
    >
      <ul className="divide-y divide-line rounded-btn border border-line">
        {s.providers.map((p) => (
          <li key={p.name} data-provider={p.name} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[13.5px]">
            <span className="w-20 font-semibold text-plum">{p.name}</span>
            <Presence ok={p.configured} />
            <span className="text-slate">{p.role}</span>
            <span className="ml-auto flex flex-wrap gap-1">
              {p.env.map((e) => (
                <code key={e} className="rounded bg-page px-1.5 py-0.5 font-mono text-[11.5px] text-slate">
                  {e}
                </code>
              ))}
            </span>
            {p.deployments_set ? (
              <span className="flex w-full flex-wrap gap-1.5 pl-[92px] text-[12px] text-slate">
                endpoint <Presence ok={!!p.endpoint_set} yes="set" no="missing" /> key <Presence ok={!!p.key_set} yes="set" no="missing" />
                {Object.entries(p.deployments_set).map(([t, ok]) => (
                  <span key={t} className="flex items-center gap-1">
                    {t} deployment <Presence ok={ok} yes="set" no="missing" />
                  </span>
                ))}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Models({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const [models, setModels] = useState<Record<string, string>>(() =>
    Object.fromEntries(TIERS.map((t) => [t, s.models.tiers[t]?.claude ?? ""])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.putSettings({ models }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel
      title="Models"
      eyebrow="Per tier, used by every stage"
      testId="models"
      actions={
        <button type="button" className="btn-primary" onClick={save} disabled={busy}>
          {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Save size={15} aria-hidden />} Save models
        </button>
      }
    >
      {error ? <ErrorNote error={error} /> : null}
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <table className="w-full text-[13.5px]">
          <thead>
            <tr className="table-head">
              <th className="px-3 py-2">Tier</th>
              <th className="px-3 py-2">Claude model</th>
              <th className="px-3 py-2">Azure OpenAI deployment</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {TIERS.map((t) => (
              <tr key={t}>
                <td className="px-3 py-2 font-medium text-plum">{t}</td>
                <td className="px-3 py-2">
                  <select
                    aria-label={`Claude model for the ${t} tier`}
                    className="rounded-btn border border-line bg-white px-2 py-1 font-mono text-[12.5px]"
                    value={models[t]}
                    onChange={(e) => setModels((m) => ({ ...m, [t]: e.target.value }))}
                  >
                    {[...new Set([models[t], ...CLAUDE_MODELS])].filter(Boolean).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-3 py-2">
                  <Presence ok={!!s.models.tiers[t]?.azure_deployment_set} yes="set" no="not set" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="table-head">
                <th className="px-3 py-2">Stage</th>
                <th className="px-3 py-2">Task</th>
                <th className="px-3 py-2 text-right">Weight</th>
                <th className="px-3 py-2">Tier</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {s.models.stages.map((st) => (
                <tr key={st.stage}>
                  <td className="px-3 py-1.5 font-medium text-plum">{st.stage}</td>
                  <td className="px-3 py-1.5 text-slate">{st.task_kind}</td>
                  <td className="px-3 py-1.5 text-right font-mono">{st.weight}</td>
                  <td className="px-3 py-1.5 text-slate">{st.tier}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1.5 text-[12px] text-slate">
            A stage takes the tier its task complexity falls in; the model is the tier&apos;s model for the active provider. The
            API has no per-stage override.
          </p>
        </div>
      </div>
    </Panel>
  );
}

function Routing({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const [bands, setBands] = useState<Record<string, [number, number]>>(() => ({ ...s.routing.bands }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [langfuse, setLangfuse] = useState(s.langfuse.enabled);
  const set = (t: string, i: 0 | 1, v: number) =>
    setBands((b) => {
      const next: [number, number] = [...(b[t] ?? [0, 0])] as [number, number];
      next[i] = v;
      return { ...b, [t]: next };
    });
  const save = async (body: Parameters<typeof api.putSettings>[0]) => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.putSettings(body));
    } catch (e) {
      setError(errorText(e));
      // A refused toggle goes back to what the server holds.
      if (body.langfuse_enabled !== undefined) setLangfuse(!body.langfuse_enabled);
    } finally {
      setBusy(false);
    }
  };
  const auto = s.provider.choice === "auto";
  return (
    <Panel
      title="Routing"
      eyebrow="Task complexity to model tier"
      testId="routing"
      actions={
        <button type="button" className="btn-primary" onClick={() => save({ routing_bands: bands })} disabled={busy}>
          {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Save size={15} aria-hidden />} Save bands
        </button>
      }
    >
      {error ? <ErrorNote error={error} /> : null}
      <p className="text-[13.5px] text-ink">Task complexity: {s.routing.complexity}.</p>
      <div className="mt-3 flex flex-wrap gap-3">
        {TIERS.map((t) => (
          <fieldset key={t} className="rounded-btn border border-line px-3 py-2">
            <legend className="px-1 text-[13px] font-semibold text-plum">{t}</legend>
            <div className="flex items-center gap-2 text-[13px] text-slate">
              <label className="flex items-center gap-1">
                from
                <input
                  type="number"
                  min={0}
                  max={10}
                  aria-label={`${t} band from`}
                  className="w-16 rounded-btn border border-line px-2 py-1 text-ink"
                  value={bands[t]?.[0] ?? 0}
                  onChange={(e) => set(t, 0, Number(e.target.value))}
                />
              </label>
              <label className="flex items-center gap-1">
                to
                <input
                  type="number"
                  min={0}
                  max={10}
                  aria-label={`${t} band to`}
                  className="w-16 rounded-btn border border-line px-2 py-1 text-ink"
                  value={bands[t]?.[1] ?? 0}
                  onChange={(e) => set(t, 1, Number(e.target.value))}
                />
              </label>
            </div>
          </fieldset>
        ))}
      </div>
      <p className="mt-1.5 text-[12px] text-slate">The three bands must cover 0 to 10 once each; the API refuses anything else.</p>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-btn border border-line p-3 text-[13px]">
          <label className="flex items-center gap-2 font-semibold text-plum">
            <input type="checkbox" checked={auto} disabled aria-describedby="fallback-note" /> Auto fallback
          </label>
          <p id="fallback-note" className="mt-1 text-slate">
            {s.routing.fallback} Chosen with PROVIDER at API startup (now {s.provider.choice}); there is no setting for it in the
            API.
          </p>
        </div>
        <div className="rounded-btn border border-line p-3 text-[13px]" data-testid="langfuse">
          <label className="flex items-center gap-2 font-semibold text-plum">
            <input
              type="checkbox"
              checked={langfuse}
              onChange={(e) => {
                setLangfuse(e.target.checked);
                save({ langfuse_enabled: e.target.checked });
              }}
              disabled={busy}
            />{" "}
            Langfuse tracing
          </label>
          <p className="mt-1 text-slate">
            Host {s.langfuse.host ?? "not set"}; keys <Presence ok={s.langfuse.keys_configured} />
          </p>
        </div>
      </div>
    </Panel>
  );
}

function Evaluation() {
  const { projectId } = useAppState();
  const processes = useProcesses(projectId);
  const [picked, setPicked] = useState<string[]>([]);
  const [evalId, setEvalId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const view = useQuery({
    queryKey: ["eval", evalId],
    queryFn: () => api.evalView(evalId!),
    enabled: !!evalId,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 2000),
  });
  const list = useMemo(() => [...(processes.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [processes.data]);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.startEval(picked);
      setEvalId(r.eval_id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const v = view.data;
  const running = !!v && !["done", "failed"].includes(v.status);
  return (
    <Panel
      title="Per-provider evaluation"
      eyebrow="Same processes, every configured provider"
      testId="provider-eval"
      actions={
        <button type="button" className="btn-primary" onClick={run} disabled={busy || running || picked.length === 0}>
          {busy || running ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Play size={15} aria-hidden />} Run evaluation
        </button>
      }
    >
      {!projectId ? (
        <p className="text-[14px] text-slate">Pick a project in the top bar to choose its processes.</p>
      ) : (
        <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
          {list.map((p) => (
            <label
              key={p.id}
              className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12.5px] ${
                picked.includes(p.id) ? "border-purple bg-purple-lt text-plum" : "border-line text-slate"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={picked.includes(p.id)}
                onChange={(e) => setPicked((x) => (e.target.checked ? [...x, p.id] : x.filter((y) => y !== p.id)))}
              />
              {p.name}
            </label>
          ))}
        </div>
      )}
      {error ? (
        <div className="mt-2">
          <ErrorNote error={error} />
        </div>
      ) : null}
      {v ? (
        <div className="mt-3">
          <p className="mb-1.5 text-[13px] text-slate">
            Evaluation {v.id.slice(0, 8)}: {v.status}
          </p>
          {v.rows.length > 0 ? (
            <div className="overflow-x-auto rounded-btn border border-line">
              <table className="w-full min-w-[860px] text-[13px]">
                <thead>
                  <tr className="table-head">
                    {["Provider", "Model", "Process", "Citations verified", "Coverage", "Challenger agree", "Rules", "Latency", "Cost"].map((h) => (
                      <th key={h} className="px-3 py-2">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {v.rows.map((r, i) => (
                    <tr key={i}>
                      <td className="px-3 py-1.5 font-medium text-plum">{r.provider}</td>
                      <td className="px-3 py-1.5 font-mono text-[12px]">{r.model}</td>
                      <td className="px-3 py-1.5">{r.process_name}</td>
                      <td className="px-3 py-1.5">
                        {r.citations_verified}/{r.citations_total}
                      </td>
                      <td className="px-3 py-1.5">
                        {r.coverage}/{r.coverage_total}
                      </td>
                      <td className="px-3 py-1.5">{r.challenger_agree == null ? "n/a" : `${Math.round(r.challenger_agree * 100)}%`}</td>
                      <td className="px-3 py-1.5">{r.rules}</td>
                      <td className="px-3 py-1.5">{Math.round(r.latency_ms)} ms</td>
                      <td className="px-3 py-1.5">${r.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : running ? null : (
            <p className="text-[13px] text-slate">No result rows.</p>
          )}
        </div>
      ) : null}
    </Panel>
  );
}

function Toolchains({ s }: { s: SettingsView }) {
  const order = ["dotnet", "terraform", "terragrunt", "docker", "az", "node"];
  const names = [...order.filter((n) => s.toolchains[n]), ...Object.keys(s.toolchains).filter((n) => !order.includes(n))];
  return (
    <Panel title="Toolchains" eyebrow="Detected on the API host" testId="toolchains">
      <table className="w-full text-[13.5px]">
        <thead>
          <tr className="table-head">
            <th className="px-3 py-2">Tool</th>
            <th className="px-3 py-2">Present</th>
            <th className="px-3 py-2">Version</th>
            <th className="px-3 py-2">Path</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {names.map((n) => {
            const t = s.toolchains[n];
            return (
              <tr key={n} data-tool={n}>
                <td className="px-3 py-2 font-medium text-plum">{n}</td>
                <td className="px-3 py-2">
                  <Presence ok={t.present} yes="present" no="missing" />
                </td>
                <td className="px-3 py-2 font-mono text-[12px]">{t.version ?? ""}</td>
                <td className="px-3 py-2 font-mono text-[12px] text-slate">{t.path ?? ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[12.5px] text-slate">
        APP_SECRET <Presence ok={s.app_secret_configured} yes="set" no="not set" /> · Scenario database: {s.scenario_database}
      </p>
    </Panel>
  );
}

export default function Page() {
  const qc = useQueryClient();
  const settings = useSettingsView();
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => setSaved(null), 3000);
    return () => clearTimeout(t);
  }, [saved]);
  const onSaved = (v: SettingsView) => {
    qc.setQueryData(["settings-view"], v);
    setSaved("Saved; new jobs use these settings.");
  };
  const s = settings.data;
  return (
    <SecondaryScreen title="Settings" description="Providers, models per tier and stage, routing bands, evaluations and toolchains.">
      {settings.error ? <ErrorNote error={settings.error} /> : null}
      {saved ? (
        <p role="status" className="rounded-card border border-gate/30 bg-gate-bg px-4 py-2 text-[14px] text-gate">
          {saved}
        </p>
      ) : null}
      {!s ? (
        settings.isLoading ? null : <Empty title="Settings unavailable">The API did not answer GET /settings.</Empty>
      ) : (
        <div className="space-y-4">
          <Providers s={s} />
          <Models key={JSON.stringify(s.models.tiers)} s={s} onSaved={onSaved} />
          <Routing key={JSON.stringify(s.routing.bands) + s.langfuse.enabled} s={s} onSaved={onSaved} />
          <Evaluation />
          <Toolchains s={s} />
        </div>
      )}
    </SecondaryScreen>
  );
}
