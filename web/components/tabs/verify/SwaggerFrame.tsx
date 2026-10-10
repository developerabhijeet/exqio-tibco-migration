"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Loader2, Play, Square } from "lucide-react";
import { api } from "@/lib/api";
import { useRunState } from "@/lib/buildQueries";
import type { RunState } from "@/lib/buildTypes";
import { Pill } from "@/components/ui/Pill";
import { errorText, Panel } from "@/components/tabs/build/Panel";

export type ServiceState = "idle" | "starting" | "running" | "stopped";

/**
 * Last meaningful line of a service log: the last line that starts with "restore failed", "error"
 * or "exception" (or names an exception type), else the last line mentioning a failure, else the
 * last line.
 */
function reasonFromLog(log: string | undefined): string | null {
  if (!log) return null;
  const lines = log.split("\n").map((l) => l.trim()).filter(Boolean);
  const back = [...lines].reverse();
  const hit =
    back.find((l) => /^(restore failed|error|exception|unhandled exception)\b/i.test(l) || /^[\w.]+Exception\b/.test(l)) ??
    back.find((l) => /fail|unable/i.test(l));
  return hit ?? lines[lines.length - 1] ?? null;
}

export interface ServiceHandle {
  state: ServiceState;
  run: RunState | null;
  reason: string | null;
  busy: "start" | "stop" | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
}

/** The generated service of a build: state from GET /builds/{id}/run plus the last start attempt. */
export function useService(buildId: string | null): ServiceHandle {
  const qc = useQueryClient();
  const run = useRunState(buildId);
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tried, setTried] = useState(false);

  const r = run.data ?? null;
  const state: ServiceState =
    busy === "start" ? "starting" : r?.running ? "running" : tried || r?.exit_code != null ? "stopped" : "idle";
  const reason =
    state === "running" || state === "starting"
      ? null
      : (error ?? (r?.exit_code != null ? `exited with code ${r.exit_code}: ${reasonFromLog(r.log_tail) ?? ""}` : null));

  const start = async () => {
    if (!buildId) return;
    setBusy("start");
    setError(null);
    setTried(true);
    try {
      const out = await api.startService(buildId);
      qc.setQueryData(["run", buildId], (old: RunState | undefined) => ({ ...(old ?? {}), ...out }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
      qc.invalidateQueries({ queryKey: ["run", buildId] });
    }
  };
  const stop = async () => {
    if (!buildId) return;
    setBusy("stop");
    try {
      await api.stopService(buildId);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
      qc.invalidateQueries({ queryKey: ["run", buildId] });
    }
  };
  return { state, run: r, reason, busy, start, stop };
}

/** Start and stop the generated service, with its status, url, the reason it is down and the log tail. */
export function ServiceControl({ svc }: { svc: ServiceHandle }) {
  const { state, run: r, reason, busy, start, stop } = svc;
  return (
    <Panel
      title="Generated service"
      eyebrow="Development auth, in-memory bus, scenario database"
      testId="service-control"
      actions={
        <>
          <button type="button" className="btn-primary" onClick={start} disabled={!!busy || state === "running"}>
            {busy === "start" ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Play size={16} aria-hidden />}
            Start service
          </button>
          <button type="button" className="btn-secondary" onClick={stop} disabled={!!busy || state !== "running"}>
            <Square size={15} aria-hidden /> Stop
          </button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13.5px]">
        <span data-testid="service-status" data-state={state} className="flex items-center gap-1.5">
          <span className="label">Status</span>
          <Pill tone={state === "running" ? "gate" : state === "starting" ? "purple" : state === "stopped" ? "error" : "det"}>
            {state === "idle" ? "not started" : state === "stopped" ? "not running" : state}
          </Pill>
        </span>
        {r?.url && state === "running" ? (
          <span>
            <span className="label mr-1.5">URL</span>
            <a href={r.url} target="_blank" rel="noreferrer" className="font-mono text-purple hover:underline">
              {r.url}
            </a>
          </span>
        ) : null}
        {r?.started_at && state === "running" ? (
          <span className="text-slate">started {new Date(r.started_at).toLocaleTimeString()}</span>
        ) : null}
        {r?.pid && state === "running" ? <span className="text-slate">pid {r.pid}</span> : null}
      </div>
      {reason ? (
        <p data-testid="service-reason" className="mt-2 whitespace-pre-wrap break-words rounded-btn bg-error-bg px-3 py-2 text-[13px] text-error">
          {reason}
        </p>
      ) : null}
      {r?.log_tail ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium text-plum">Log tail</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-btn bg-page p-3 font-mono text-[11.5px] leading-5 text-ink">
            {r.log_tail}
          </pre>
        </details>
      ) : null}
    </Panel>
  );
}

/** Swagger UI of the running service, or why it is not available. */
export function SwaggerFrame({ run, state, reason }: { run: RunState | null; state: ServiceState; reason: string | null }) {
  if (state === "running" && run?.swagger_url) {
    return (
      <div className="overflow-hidden rounded-card border border-line bg-white">
        <div className="flex items-center gap-2 border-b border-line bg-page px-4 py-2 text-[13px]">
          <span className="label">Swagger</span>
          <span className="font-mono text-slate">{run.swagger_url}</span>
          <a href={run.swagger_url} target="_blank" rel="noreferrer" className="ml-auto text-purple hover:underline">
            Open in a new tab
          </a>
        </div>
        <p className="border-b border-line px-4 py-1.5 text-[12px] text-slate">
          An empty frame means the service answered without the Swagger UI (for example a 404 on /swagger); open it in a new tab
          to see the answer.
        </p>
        <iframe data-testid="swagger-frame" title="Swagger UI of the generated service" src={run.swagger_url} className="h-[640px] w-full bg-white" />
      </div>
    );
  }
  if (state === "running") {
    return (
      <div
        data-testid="swagger-placeholder"
        data-reason="no-swagger-url"
        className="flex min-h-[160px] flex-col items-center justify-center rounded-card border border-dashed border-line bg-warm/60 px-6 py-8 text-center"
      >
        <p className="font-medium text-plum">Service running, Swagger URL not reported</p>
        <p className="mt-1 max-w-[640px] text-[14px] text-slate">
          The API reports the service as running but gave no Swagger address.
          {run?.url ? ` Try ${run.url}/swagger in a new tab.` : ""}
        </p>
      </div>
    );
  }
  return (
    <div
      data-testid="swagger-placeholder"
      className="flex min-h-[220px] flex-col items-center justify-center rounded-card border border-dashed border-line bg-warm/60 px-6 py-8 text-center"
    >
      <p className="font-medium text-plum">Service not running</p>
      <p className="mt-1 max-w-[640px] text-[14px] text-slate">
        {state === "starting"
          ? "Starting the service and waiting for /health."
          : reason
            ? `Swagger is shown here once the service runs. Last start: ${reason}`
            : "Start the service to call it from Swagger with the same inputs the scenarios use. It needs a build whose packages restored (a machine with NuGet access)."}
      </p>
    </div>
  );
}

/** Calls the service's POST /auth/dev-token and explains where to paste the token. */
export function DevTokenHelper({ run, running }: { run: RunState | null; running: boolean }) {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const url = run?.url ?? "http://127.0.0.1:<port>";
  const curl = `curl -s -X POST ${url}/auth/dev-token -H 'content-type: application/json' -d '{"subject":"exiqo-verifier"}'`;

  const get = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${url}/auth/dev-token`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ subject: "exiqo-verifier" }),
      });
      if (!res.ok) throw new Error(`the service answered ${res.status} ${res.statusText}`);
      const body = (await res.json()) as { access_token?: string };
      if (!body.access_token) throw new Error("the answer had no access_token");
      setToken(body.access_token);
    } catch (e) {
      setError(
        e instanceof TypeError
          ? "The browser could not call the service directly (it does not allow this page's origin). Run the command below instead."
          : `No token: ${errorText(e)}.`,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-card border border-line bg-white p-4" data-testid="dev-token">
      <div className="flex flex-wrap items-center gap-2">
        <KeyRound size={16} className="text-purple" aria-hidden />
        <span className="font-semibold text-plum">Development token</span>
        <button type="button" className="btn-secondary ml-auto px-3 py-1.5 text-[14px]" onClick={get} disabled={!running || busy}>
          {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
          Get dev token
        </button>
      </div>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-[13px] text-slate">
        <li>Get a token (Development auth only; production validates Microsoft Entra ID tokens).</li>
        <li>In Swagger, select Authorize, paste the token into the Bearer field and select Authorize again.</li>
        <li>Call the endpoint with a scenario&apos;s input; the parity table compares the same call field by field.</li>
      </ol>
      {token ? (
        <div className="mt-2 flex items-start gap-2">
          <code data-testid="dev-token-value" className="block max-h-20 flex-1 overflow-auto break-all rounded-btn bg-page p-2 font-mono text-[11.5px]">
            {token}
          </code>
          <button type="button" className="btn-ghost" aria-label="Copy token" onClick={() => navigator.clipboard?.writeText(token)}>
            <Copy size={15} />
          </button>
        </div>
      ) : null}
      {error ? <p className="mt-2 text-[13px] text-error">{error}</p> : null}
      {error || !running ? (
        <code className="mt-2 block overflow-x-auto whitespace-pre rounded-btn bg-page p-2 font-mono text-[11.5px] text-ink">{curl}</code>
      ) : null}
    </div>
  );
}
