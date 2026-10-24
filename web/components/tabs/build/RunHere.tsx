"use client";

import { ExternalLink, Loader2, Play, Square, TerminalSquare } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import { useService } from "@/components/tabs/verify/SwaggerFrame";

/**
 * Compiles and runs the generated solution on the API host and shows what it did: the port it came
 * up on, a link to its Swagger UI, and the tail of its own log. The same service the Verify tab
 * drives, surfaced beside the download so the sequence is one place: take the code, run the code,
 * call the code.
 *
 * Every number here comes from the running process. When it does not start, the reason is the last
 * meaningful line of its log, not a message this screen invented.
 */
export function RunHere({ buildId, compiled }: { buildId: string; compiled: boolean | null }) {
  const svc = useService(buildId);
  const { state, run, reason, busy } = svc;
  const blocked = compiled === false ? "This build did not compile, so there is nothing to run." : null;

  return (
    <div className="mt-5 border-t border-line pt-4" data-testid="run-here">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <TerminalSquare size={16} className="text-purple" aria-hidden />
        <span className="font-semibold text-plum">Run it here</span>
        <span className="text-[13px] text-slate">
          Starts the generated service on this host and opens its Swagger UI, without downloading anything.
        </span>
        <span className="ml-auto flex items-center gap-2">
          <span title={blocked ?? undefined}>
            <button
              type="button"
              className={blocked ? "btn-secondary" : "btn-primary"}
              data-testid="run-here-start"
              onClick={svc.start}
              disabled={!!blocked || !!busy || state === "running"}
            >
              {busy === "start" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Play size={15} aria-hidden />}
              Compile and run
            </button>
          </span>
          <button
            type="button"
            className="btn-secondary"
            data-testid="run-here-stop"
            onClick={svc.stop}
            disabled={!!busy || state !== "running"}
          >
            <Square size={14} aria-hidden /> Stop
          </button>
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13.5px]">
        <span className="flex items-center gap-1.5" data-testid="run-here-status" data-state={state}>
          <span className="label">Status</span>
          <Pill tone={state === "running" ? "gate" : state === "starting" ? "purple" : state === "stopped" ? "error" : "det"}>
            {state === "idle" ? "not started" : state === "starting" ? "starting" : state === "stopped" ? "not running" : "running"}
          </Pill>
        </span>
        {state === "running" && run?.url ? (
          <span>
            <span className="label mr-1.5">Listening on</span>
            <span className="font-mono text-ink">{run.url}</span>
          </span>
        ) : null}
        {state === "running" && run?.pid ? <span className="text-slate">pid {run.pid}</span> : null}
        {state === "running" && run?.swagger_url ? (
          <a
            href={run.swagger_url}
            target="_blank"
            rel="noreferrer"
            data-testid="run-here-swagger"
            className="flex items-center gap-1.5 font-medium text-purple hover:underline"
          >
            <ExternalLink size={14} aria-hidden /> Open Swagger
          </a>
        ) : null}
      </div>

      {blocked ? (
        <p className="mt-2 text-[13px] text-slate">{blocked}</p>
      ) : reason ? (
        <p
          data-testid="run-here-reason"
          className="mt-2 whitespace-pre-wrap break-words rounded-btn bg-error-bg px-3 py-2 text-[13px] text-error"
        >
          {reason}
        </p>
      ) : null}

      {run?.log_tail ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium text-plum">Running output</summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-btn bg-page p-3 font-mono text-[11.5px] leading-5 text-ink">
            {run.log_tail}
          </pre>
        </details>
      ) : null}

      <p className="mt-3 text-[12.5px] text-slate">
        It restores and runs the same solution the zip contains, with Development authentication, the in-process bus and
        the scenario database. It needs a host with the .NET SDK and access to the NuGet feed; where either is missing
        the reason above is the service&apos;s own log.
      </p>
    </div>
  );
}
