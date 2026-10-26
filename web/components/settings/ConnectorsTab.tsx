"use client";

// Settings → Connectors: one card per connector (GitHub, Atlassian for Confluence and Jira, Figma).
// The install (Helm values) is the ceiling; an admin turns a connector on within it, sets its URLs
// and defaults, and tests the connection. Everyone else reads the cards.
import { useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { ConnectorCard } from "@/components/settings/ConnectorCard";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { CONNECTOR_KINDS, isAdminView, useConnectorSettings, useConnectors } from "@/lib/connectors";
import { useAppState } from "@/lib/state";
import type { ConnectorSettingsView } from "@/lib/buildTypes";

export function ConnectorsTab() {
  const qc = useQueryClient();
  const { user } = useAppState();
  const settings = useConnectorSettings();
  const flags = useConnectors();
  const view = settings.data;

  const onSaved = (all: ConnectorSettingsView) => {
    qc.setQueryData(["settings-connectors", user], all);
    void qc.invalidateQueries({ queryKey: ["connectors"] });
  };
  const onTested = () => {
    void qc.invalidateQueries({ queryKey: ["settings-connectors", user] });
  };

  if (settings.error) return <ErrorNote error={settings.error} />;
  if (!view) {
    return settings.isLoading ? (
      <p role="status" className="text-[14px] text-slate">
        Loading connectors…
      </p>
    ) : (
      <Empty title="Connectors unavailable">The API did not answer GET /settings/connectors.</Empty>
    );
  }
  const admin = isAdminView(view.github);
  return (
    <div className="space-y-4" data-testid="connectors-tab">
      <p className="flex items-start gap-2 rounded-card border border-line bg-warm/60 px-4 py-3 text-[13.5px] text-ink">
        <Info size={16} className="mt-0.5 shrink-0 text-purple" aria-hidden />
        <span>
          Connectors reach only the hosts the install allows, and every one is off until both the install and Settings turn
          it on. Credentials are mounted from Key Vault; this page shows whether each one is present, never its value.
          {admin ? null : " You are viewing as a non-administrator: the cards are read only."}
        </span>
      </p>
      {CONNECTOR_KINDS.map((kind) => (
        <ConnectorCard
          // A different user, or the admin view replacing the flags-only one, starts a fresh form.
          key={`${kind}-${user}-${isAdminView(view[kind])}`}
          kind={kind}
          view={view[kind]}
          flags={flags.data}
          user={user}
          onSaved={onSaved}
          onTested={onTested}
        />
      ))}
    </div>
  );
}
