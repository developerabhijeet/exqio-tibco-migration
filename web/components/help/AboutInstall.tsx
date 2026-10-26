"use client";

import { Loader2 } from "lucide-react";
import { Panel } from "@/components/tabs/build/Panel";
import { Empty } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useSystemStatus } from "./HelpNav";

/** ok is green, disabled is neutral, anything else ("down: <Class>") is an error. */
function CheckPill({ name, value }: { name: string; value: string }) {
  const tone = value === "ok" ? "gate" : value === "disabled" ? "det" : "error";
  return <Pill tone={tone} testId={`check-${name}`}>{value}</Pill>;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2.5">
      <dt className="label w-44 shrink-0">{label}</dt>
      <dd className="min-w-0 break-words text-md text-ink">{children}</dd>
    </div>
  );
}

export function AboutInstall() {
  const status = useSystemStatus();
  const s = status.data;
  if (status.isLoading) return <Loader2 size={18} className="animate-spin text-slate" aria-label="Loading" />;
  if (!s) return <Empty title="Not available">About this install is shown to administrators only.</Empty>;
  const digests = Object.entries(s.image_digests);
  return (
    <div data-testid="about-install" className="max-w-[900px] space-y-5">
      <Panel title="This install" eyebrow="From the running API">
        <dl className="divide-y divide-line">
          <Row label="Version">{s.version}</Row>
          <Row label="Role">{s.role}</Row>
          <Row label="Sign-in">{s.auth_mode}</Row>
          <Row label="Deploy credentials">{s.deploy_credential_mode}</Row>
        </dl>
      </Panel>
      <Panel title="Dependency checks" eyebrow="GET /readyz">
        <ul className="divide-y divide-line">
          {Object.entries(s.checks).map(([name, value]) => (
            <li key={name} className="flex items-center gap-4 py-2.5 text-md">
              <span className="w-44 font-medium text-heading">{name}</span>
              <CheckPill name={name} value={value} />
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Image digests" eyebrow="What is running">
        {digests.length === 0 ? (
          <p className="text-md text-slate">None recorded. The chart sets these on a pipeline install.</p>
        ) : (
          <dl className="divide-y divide-line">
            {digests.map(([name, digest]) => (
              <Row key={name} label={name}>
                <code className="break-all font-mono text-sm">{digest}</code>
              </Row>
            ))}
          </dl>
        )}
      </Panel>
      <Panel title="Egress allowlist" eyebrow="The only hosts this install may call">
        {s.egress_allowlist.length === 0 ? (
          <p className="text-md text-slate">None declared.</p>
        ) : (
          <ul className="space-y-1">
            {s.egress_allowlist.map((host) => (
              <li key={host}><code className="font-mono text-sm">{host}</code></li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
