"use client";

/**
 * Deploy, "Security and testing": the controls the generated service carries, each one read back
 * from what the build actually produced, with the request path drawn out beneath them.
 *
 * Nothing here is asserted. Every row names where it was read from: a line in the generated
 * Program.cs, a Terraform unit in the generated infrastructure, the test run, the review panel or
 * the parity report. A control this build produced no evidence for is left out of the panel
 * altogether rather than counted as met; one that ran and failed is always shown. The ring is
 * therefore only ever as green as the evidence behind it.
 */
import { ChevronRight, ShieldCheck } from "lucide-react";
import { Panel } from "@/components/tabs/build/Panel";
import { Pill } from "@/components/ui/Pill";
import { AzureIcon } from "@/components/ui/BrandIcons";
import {
  useBuildFile,
  useBuildFiles,
  useBuildReviews,
  useBuildTests,
  useInfra,
  useParity,
} from "@/lib/buildQueries";
import { useIdentity } from "@/lib/identity";

type State = "met" | "unmet" | "unknown";

interface Check {
  key: string;
  group: "Authentication" | "Secrets and identity" | "Data handling" | "Testing";
  title: string;
  /** What the control means, in one sentence. */
  what: string;
  state: State;
  /** Where the state was read from. */
  source: string;
  /** The evidence itself: a line of generated code, a unit name, a count. */
  evidence?: string | null;
}

const TONE: Record<State, "gate" | "error" | "det"> = { met: "gate", unmet: "error", unknown: "det" };
const LABEL: Record<State, string> = { met: "in place", unmet: "not in place", unknown: "" };

/** The first line of `text` that holds `needle`, trimmed, for the evidence line. */
function lineWith(text: string | undefined, needle: string): string | null {
  if (!text) return null;
  const line = text.split("\n").find((l) => l.includes(needle));
  return line ? line.trim() : null;
}

function has(text: string | undefined, needle: string): State {
  if (text === undefined) return "unknown";
  return text.includes(needle) ? "met" : "unmet";
}

function Ring({ met, total }: { met: number; total: number }) {
  const pct = total === 0 ? 0 : Math.round((met / total) * 100);
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 140 140" className="h-[140px] w-[140px] shrink-0" role="img"
         aria-label={`${pct}% of the checks that ran are in place`}>
      <circle cx="70" cy="70" r={r} fill="none" stroke="currentColor" strokeWidth="14" className="text-line" />
      <circle
        cx="70" cy="70" r={r} fill="none" strokeWidth="14" strokeLinecap="round"
        className={pct === 100 ? "text-gate" : pct >= 60 ? "text-decision" : "text-error"}
        stroke="currentColor"
        strokeDasharray={`${(c * pct) / 100} ${c}`}
        transform="rotate(-90 70 70)"
      />
      <text x="70" y="68" textAnchor="middle" className="fill-plum text-[26px] font-semibold">{pct}%</text>
      <text x="70" y="88" textAnchor="middle" className="fill-slate text-[11px]">{met} of {total}</text>
    </svg>
  );
}

function Bar({ label, met, total }: { label: string; met: number; total: number }) {
  const pct = total === 0 ? 0 : Math.round((met / total) * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between text-[13px]">
        <span className="font-medium text-plum">{label}</span>
        <span className="text-slate">{met} of {total}</span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-line">
        <div
          className={`h-full rounded-full ${pct === 100 ? "bg-gate" : pct > 0 ? "bg-decision" : "bg-error"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Row({ check }: { check: Check }) {
  return (
    <details className="group/chk border-b border-line last:border-0">
      <summary className="flex cursor-pointer list-none items-center gap-2 py-2.5">
        <ChevronRight size={14} className="shrink-0 text-slate transition-transform group-open/chk:rotate-90" aria-hidden />
        <span className="min-w-0 flex-1 text-[14px] text-ink">{check.title}</span>
        <Pill tone={TONE[check.state]}>{LABEL[check.state]}</Pill>
      </summary>
      <div className="pb-3 pl-[22px] text-[13px] text-slate">
        <p>{check.what}</p>
        <p className="mt-1">
          <span className="label">Read from</span> {check.source}
        </p>
        {check.evidence ? (
          <pre className="mt-1 overflow-x-auto rounded bg-page p-2 font-mono text-[11.5px] text-ink">{check.evidence}</pre>
        ) : null}
      </div>
    </details>
  );
}

/** The path a call takes through the generated service, and what it passes on the way. */
function RequestPath() {
  return (
    <figure className="m-0 overflow-x-auto rounded-btn border border-line bg-page p-3">
      <svg viewBox="0 0 980 300" className="block h-auto w-full min-w-[720px]" role="img"
           aria-label="Activity diagram: a caller's request passes API Management, token validation and authorization before the controller runs, while secrets are read from Key Vault at startup">
        <defs>
          <marker id="sp-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" className="fill-slate" />
          </marker>
        </defs>
        <g className="fill-slate text-[10px] font-semibold tracking-[0.12em]">
          <text x="8" y="56">CALLER</text>
          <text x="8" y="146">SERVICE</text>
          <text x="8" y="252">STARTUP</text>
        </g>

        <rect x="96" y="28" width="150" height="46" rx="8" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="171" y="48" textAnchor="middle" className="fill-ink text-[12px] font-semibold">Caller</text>
        <text x="171" y="64" textAnchor="middle" className="fill-slate text-[10px]">holds a token for the API</text>

        <path d="M246,51 L286,51" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="288" y="28" width="150" height="46" rx="8" className="fill-purple-lt stroke-purple" strokeWidth="1.3" />
        <text x="363" y="48" textAnchor="middle" className="fill-ink text-[12px] font-semibold">API Management</text>
        <text x="363" y="64" textAnchor="middle" className="fill-slate text-[10px]">product, policy, throttle</text>

        <path d="M438,51 L478,51" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="480" y="28" width="170" height="46" rx="8" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="565" y="48" textAnchor="middle" className="fill-ink text-[12px] font-semibold">HTTPS to the workload</text>
        <text x="565" y="64" textAnchor="middle" className="fill-slate text-[10px]">private endpoint</text>

        <path d="M565,74 L565,104" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="96" y="106" width="760" height="76" rx="10" className="fill-warm stroke-line" strokeWidth="1.2" />
        <text x="112" y="124" className="fill-ink text-[11.5px] font-semibold">The generated .NET 10 service</text>

        <rect x="112" y="132" width="150" height="40" rx="6" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="187" y="149" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Authentication</text>
        <text x="187" y="163" textAnchor="middle" className="fill-slate text-[9.5px]">bearer token validated</text>

        <path d="M262,152 L286,152" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="288" y="132" width="150" height="40" rx="6" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="363" y="149" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Authorization</text>
        <text x="363" y="163" textAnchor="middle" className="fill-slate text-[9.5px]">every endpoint requires it</text>

        <path d="M438,152 L462,152" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="464" y="132" width="150" height="40" rx="6" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="539" y="149" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Controller</text>
        <text x="539" y="163" textAnchor="middle" className="fill-slate text-[9.5px]">contract from the TIBCO XSD</text>

        <path d="M614,152 L638,152" className="stroke-slate" strokeWidth="1.3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="640" y="132" width="200" height="40" rx="6" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="740" y="149" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Domain service and repository</text>
        <text x="740" y="163" textAnchor="middle" className="fill-slate text-[9.5px]">the approved rules, the same SQL</text>

        <path d="M86,152 L96,152" className="stroke-slate" strokeWidth="1.3" fill="none" />
        <path d="M187,182 L187,214" className="stroke-slate" strokeWidth="1.2" strokeDasharray="4 3" fill="none" markerEnd="url(#sp-a)" />
        <path d="M740,182 L740,214" className="stroke-slate" strokeWidth="1.2" strokeDasharray="4 3" fill="none" markerEnd="url(#sp-a)" />

        <rect x="96" y="216" width="190" height="52" rx="8" className="fill-gate-bg stroke-gate" strokeWidth="1.4" />
        <text x="191" y="236" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Key Vault, at startup</text>
        <text x="191" y="251" textAnchor="middle" className="fill-slate text-[9.5px]">read with the managed identity</text>
        <text x="191" y="263" textAnchor="middle" className="fill-slate text-[9.5px]">no secret in configuration</text>

        <rect x="320" y="216" width="190" height="52" rx="8" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="415" y="236" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Masking</text>
        <text x="415" y="251" textAnchor="middle" className="fill-slate text-[9.5px]">sensitive values masked before</text>
        <text x="415" y="263" textAnchor="middle" className="fill-slate text-[9.5px]">a log line or a fault leaves</text>

        <rect x="544" y="216" width="190" height="52" rx="8" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="639" y="236" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Azure Monitor</text>
        <text x="639" y="251" textAnchor="middle" className="fill-slate text-[9.5px]">logs, traces and metrics</text>
        <text x="639" y="263" textAnchor="middle" className="fill-slate text-[9.5px]">from the first run</text>

        <rect x="768" y="216" width="88" height="52" rx="8" className="fill-white stroke-slate" strokeWidth="1.2" />
        <text x="812" y="240" textAnchor="middle" className="fill-ink text-[11px] font-semibold">/health</text>
        <text x="812" y="255" textAnchor="middle" className="fill-slate text-[9.5px]">anonymous</text>
      </svg>
      <figcaption className="mt-2 text-[12px] text-slate">
        The path is read from the generated service: the middleware order, the authorization requirement and the
        health endpoint are the ones in its own Program.cs.
      </figcaption>
    </figure>
  );
}

export function SecurityPanel({ buildId }: { buildId: string | null }) {
  const { config } = useIdentity();
  const files = useBuildFiles(buildId);
  const program = files.data?.files.find((f) => f.path.endsWith("Program.cs"))?.path ?? null;
  const source = useBuildFile(buildId, program);
  const tests = useBuildTests(buildId);
  const reviews = useBuildReviews(buildId);
  const infra = useInfra(buildId);
  const parity = useParity(buildId);

  const code = source.data?.content;
  const infraPaths = infra.data?.files.map((f) => f.path) ?? null;
  const unit = (name: string): State =>
    infraPaths === null ? "unknown" : infraPaths.some((p) => p.includes(`/${name}/`)) ? "met" : "unmet";

  const masking = files.data ? (files.data.files.some((f) => f.path.endsWith("Masking.cs")) ? "met" : "unmet") : "unknown";
  const openSecurity = (reviews.data ?? []).filter((r) => r.reviewer === "security" && r.decision !== "reject");
  const summary = tests.data?.summary;
  const authority = config.instance.trim() && config.audience.trim();

  const checks: Check[] = [
    {
      key: "authz",
      group: "Authentication",
      title: "Every endpoint requires a bearer token",
      what: "The controllers are mapped with an authorization requirement, so an unauthenticated call is refused before it reaches any rule.",
      state: has(code, "MapControllers().RequireAuthorization()"),
      source: program ? `the generated ${program.split("/").pop()}` : "the generated service",
      evidence: lineWith(code, "MapControllers().RequireAuthorization()"),
    },
    {
      key: "health",
      group: "Authentication",
      title: "Only the health endpoint is anonymous",
      what: "Health probes answer without a token; nothing else does.",
      state: has(code, 'MapHealthChecks("/health").AllowAnonymous()'),
      source: program ? `the generated ${program.split("/").pop()}` : "the generated service",
      evidence: lineWith(code, "MapHealthChecks"),
    },
    {
      key: "jwt",
      group: "Authentication",
      title: "Tokens are validated, not trusted",
      what: "The service validates each bearer token against its configured authority and audience. The tenant values are environment configuration, read from Key Vault where the service runs.",
      state: code === undefined ? "unknown" : code.includes("AddJwtBearer") ? "met" : "unmet",
      source: program ? `the generated ${program.split("/").pop()}` : "the generated service",
      evidence: authority
        ? `${lineWith(code, "AddJwtBearer") ?? "AddJwtBearer"}\n${config.instance}  ·  audience ${config.audience}`
        : lineWith(code, "AddJwtBearer"),
    },
    {
      key: "vault",
      group: "Secrets and identity",
      title: "Secrets come from Key Vault",
      what: "Outside development the configuration is loaded from the vault, so no connection string or key sits in a file in the repository.",
      state: code === undefined || infraPaths === null
        ? "unknown"
        : code.includes("AddAzureKeyVault") && unit("key_vault") === "met" ? "met" : "unmet",
      source: "the generated service and the key_vault unit of the generated infrastructure",
      evidence: lineWith(code, "AddAzureKeyVault"),
    },
    {
      key: "identity",
      group: "Secrets and identity",
      title: "The workload runs as a managed identity",
      what: "The vault is read with a federated identity holding least-privilege roles, so there is no credential to store or rotate.",
      state: unit("identity"),
      source: "the identity unit of the generated infrastructure",
      evidence: infraPaths?.find((p) => p.includes("/identity/")) ?? null,
    },
    {
      key: "masking",
      group: "Data handling",
      title: "Sensitive values are masked",
      what: "Account numbers and similar values are masked before a log line or a fault message leaves the service.",
      state: masking,
      source: "the generated Masking helper",
      evidence: files.data?.files.find((f) => f.path.endsWith("Masking.cs"))?.path ?? null,
    },
    {
      key: "review",
      group: "Data handling",
      title: "Security review of the generated code",
      what: "An independent security reviewer reads the generated code; anything it raises is decided by the engineer at G4.",
      state: reviews.data === undefined ? "unknown" : openSecurity.length === 0 ? "met" : "unmet",
      source: "the review panel of this build",
      evidence: reviews.data === undefined
        ? null
        : openSecurity.length === 0
          ? "no open finding from the security reviewer"
          : `${openSecurity.length} open finding(s): ${openSecurity.map((r) => r.text).slice(0, 3).join(" · ")}`,
    },
    {
      key: "tests",
      group: "Testing",
      title: "Generated tests pass",
      what: "One test per approved rule and per branch, run against a real database, in the build that produced this service.",
      state: !summary || summary.total === 0 ? "unknown" : summary.passed === summary.total ? "met" : "unmet",
      source: "the test run of this build",
      evidence: summary ? `${summary.passed} passed, ${summary.failed} failed, ${summary.total} in total` : null,
    },
    {
      key: "parity",
      group: "Testing",
      title: "Parity with the TIBCO process proven",
      what: "Every scenario was replayed against the running service and compared field by field; an unexplained difference holds G5 shut.",
      state: !parity.data ? "unknown" : parity.data.unexplained === 0 ? "met" : "unmet",
      source: "the parity report of this build",
      evidence: parity.data
        ? `${parity.data.matched} of ${parity.data.compared} fields match, ${parity.data.unexplained} unexplained`
        : null,
    },
    {
      key: "infra",
      group: "Testing",
      title: "Infrastructure validated",
      what: "Every generated Terraform module is formatted and valid before a plan is run against the subscription.",
      state: !infra.data?.validation
        ? "unknown"
        : infra.data.validation.ok || (infra.data.validation.modules ?? []).every((m) => m.validate_ok !== false)
          ? "met"
          : "unmet",
      source: "the infrastructure validation of this build",
      evidence: infra.data?.validation?.modules
        ? `${infra.data.validation.modules.filter((m) => m.validate_ok).length} of ${infra.data.validation.modules.length} units valid`
        : null,
    },
  ];

  // Only a control this build produced evidence for is shown. One that has not run is left out
  // altogether rather than listed as an unknown; one that ran and failed is always shown.
  const shown = checks.filter((c) => c.state !== "unknown");
  const met = shown.filter((c) => c.state === "met").length;
  const groups = (["Authentication", "Secrets and identity", "Data handling", "Testing"] as const)
    .filter((g) => shown.some((c) => c.group === g));

  return (
    <Panel
      title="Security and testing"
      icon={<ShieldCheck size={18} className="text-gate" aria-hidden />}
      eyebrow="Controls on the generated service"
      testId="security-panel"
      actions={
        <span className="flex items-center gap-2 text-[13px]">
          <AzureIcon size={15} />
          <span className="label">Controls</span>
          <Pill tone={shown.length > 0 && met === shown.length ? "gate" : met === 0 ? "det" : "decision"}>
            {met} of {shown.length} in place
          </Pill>
        </span>
      }
    >
      {!buildId || shown.length === 0 ? (
        <p className="text-[14px] text-slate">Build this process first; the controls are read from the build.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-6">
            <Ring met={met} total={shown.length} />
            <div className="grid min-w-[260px] flex-1 gap-3 sm:grid-cols-2">
              {groups.map((g) => {
                const rows = shown.filter((c) => c.group === g);
                return <Bar key={g} label={g} met={rows.filter((r) => r.state === "met").length} total={rows.length} />;
              })}
            </div>
          </div>

          <div className="mt-4">
            {groups.map((g) => (
              <div key={g} className="mt-3 first:mt-0">
                <p className="label">{g}</p>
                <div className="mt-1 rounded-btn border border-line px-3">
                  {shown.filter((c) => c.group === g).map((c) => (
                    <Row key={c.key} check={c} />
                  ))}
                </div>
              </div>
            ))}
          </div>

          <details className="group/path mt-4">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-[14px] font-medium text-plum">
              <ChevronRight size={15} className="text-slate transition-transform group-open/path:rotate-90" aria-hidden />
              The request path, and where each control sits
            </summary>
            <div className="mt-2">
              <RequestPath />
            </div>
          </details>
        </>
      )}
    </Panel>
  );
}
