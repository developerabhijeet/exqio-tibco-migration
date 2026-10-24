"use client";

import { useState } from "react";
import { Check, Copy, Download, Loader2, PackageOpen, Terminal } from "lucide-react";
import { api } from "@/lib/api";
import { useBuildFiles } from "@/lib/buildQueries";
import { GENERATED_SWAGGER_URL } from "@/lib/generatedService";
import { ErrorNote } from "@/components/ui/Empty";
import { errorText, Panel } from "./Panel";
import { RunHere } from "./RunHere";

/** A command with a copy button, so nothing has to be retyped from the screen. */
function Command({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* no clipboard in this browser: the text is on screen to select */
    }
  };
  return (
    <div>
      {label ? <p className="label mb-1">{label}</p> : null}
      <div className="flex items-start gap-2">
        <code className="block flex-1 overflow-x-auto whitespace-pre rounded-btn border border-line bg-page px-3 py-2 font-mono text-[12.5px] leading-5 text-ink">
          {text}
        </code>
        <button type="button" className="btn-ghost shrink-0" aria-label={`Copy: ${text}`} onClick={copy}>
          {copied ? <Check size={15} className="text-gate" aria-hidden /> : <Copy size={15} aria-hidden />}
        </button>
      </div>
    </div>
  );
}

/**
 * Downloads the build's folder as a zip and says how to run it: the bundle carries the generated
 * `run-local.sh`, `run-local.ps1` and `RUN_LOCALLY.md`, so the commands here are the ones in the
 * bundle, not a second set to keep in step. The zip is the whole folder (minus bin, obj,
 * TestResults and the generator's manifest), so the solution, the tests, the Dockerfile, the
 * compose file and the Terraform all travel together and the recipient needs nothing from ExiQO
 * Migrate to reach Swagger.
 */
export function ExportPanel({
  buildId,
  blocked = null,
  compiled = null,
}: {
  buildId: string;
  blocked?: string | null;
  compiled?: boolean | null;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [got, setGot] = useState<string | null>(null);
  const files = useBuildFiles(buildId);

  // The scripts are build artifacts; if this build predates them, say so instead of printing a
  // command the folder does not contain.
  const paths = new Set((files.data?.files ?? []).map((f) => f.path));
  const hasScripts = paths.has("run-local.sh");
  const count = files.data?.files.length ?? 0;

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await api.downloadBuild(buildId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setGot(filename);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const folder = got ? got.replace(/\.zip$/, "") : "<folder>";

  return (
    <Panel
      title="Export and run locally"
      eyebrow="The whole bundle as a zip: solution, tests, Dockerfile, compose, Terraform, pipelines"
      testId="export-panel"
      actions={
        <span title={blocked ?? undefined}>
          <button
            type="button"
            className="btn-primary"
            data-testid="export-download"
            onClick={download}
            disabled={busy || !!blocked}
          >
            {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Download size={16} aria-hidden />}
            Download bundle
          </button>
        </span>
      }
    >
      {blocked ? <p className="mb-2 text-[13px] text-slate">Download: {blocked}</p> : null}
      {error ? <ErrorNote error={error} /> : null}
      {got ? (
        <p
          role="status"
          data-testid="export-done"
          className="mb-3 flex items-center gap-2 rounded-btn bg-gate-bg px-3 py-2 text-[13.5px] text-gate"
        >
          <PackageOpen size={16} aria-hidden />
          <span>
            Saved <span className="font-mono font-semibold">{got}</span> to your downloads.
          </span>
        </p>
      ) : null}

      <p className="text-[13.5px] text-slate">
        {count > 0 ? `${count} files. ` : ""}Unzip it anywhere and start it with one command. The script builds the
        image, starts Postgres and the API, waits for <span className="font-mono">/health</span>, then prints the
        Swagger address and a bearer token to paste into Swagger&apos;s Authorize dialog.
      </p>

      <div className="mt-3 space-y-3">
        <Command label="1. Unzip" text={`unzip ${got ?? "<bundle>.zip"} -d ${folder}`} />
        {hasScripts ? (
          <>
            <Command label="2. Run it (Docker; needs Docker Desktop running)" text={`cd ${folder} && bash run-local.sh`} />
            <Command
              label="On Windows"
              text={`cd ${folder}; powershell -ExecutionPolicy Bypass -File run-local.ps1`}
            />
            <Command
              label="Without Docker (the .NET 10 SDK, against a Postgres you already run)"
              text={`cd ${folder} && bash run-local.sh --dotnet`}
            />
          </>
        ) : (
          <>
            <p className="rounded-btn bg-decision-bg px-3 py-2 text-[13px] text-ink">
              This build was generated before the local-run scripts were added, so the bundle has no{" "}
              <span className="font-mono">run-local.sh</span>. Generate and build again to get them, or start the
              stack with the compose file the bundle does carry:
            </p>
            <Command
              label="Docker Compose directly"
              text={`cd ${folder} && docker compose -f docker-compose.override.yml up --build`}
            />
          </>
        )}
        <Command label="3. Open Swagger" text={GENERATED_SWAGGER_URL} />
      </div>

      <details className="mt-4">
        <summary className="flex cursor-pointer items-center gap-1.5 text-[13.5px] font-medium text-plum">
          <Terminal size={15} aria-hidden /> Run the generated tests, and stop the stack
        </summary>
        <div className="mt-2 space-y-3">
          <Command label="Tests (Testcontainers starts their own Postgres, so Docker has to be running)" text="dotnet test" />
          <Command label="Stop (add -v to drop the local database)" text="docker compose -f docker-compose.override.yml down" />
          <p className="text-[13px] text-slate">
            <span className="font-medium text-plum">RUN_LOCALLY.md</span> in the bundle says the same in full, with the
            configuration keys, the endpoints and what to do when it does not start.{" "}
            <span className="font-medium text-plum">EXPORTED_FROM.md</span> records which build it is: its build hash,
            design hash and TIBCO spec hash.
          </p>
        </div>
      </details>

      <RunHere buildId={buildId} compiled={compiled} />

      <p className="mt-4 text-[12.5px] text-slate">
        Everything the scripts set is development configuration: a development JWT instead of Microsoft Entra ID, an
        in-process bus instead of Azure Service Bus, and a local Postgres password. The deployed application reads its
        secrets from Key Vault with its managed identity, and the dev token endpoint is compiled out of Release builds.
      </p>
    </Panel>
  );
}
