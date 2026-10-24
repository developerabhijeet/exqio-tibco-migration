"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Cloud, Loader2, Unplug } from "lucide-react";
import { api } from "@/lib/api";
import { useAzure } from "@/lib/buildQueries";
import type { AzureConnectBody } from "@/lib/buildTypes";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { errorText, Panel } from "@/components/tabs/build/Panel";
import { AzureIcon } from "@/components/ui/BrandIcons";

const FIELDS: { key: keyof AzureConnectBody; label: string; required?: boolean; placeholder: string }[] = [
  { key: "tenant", label: "Tenant ID", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
  { key: "subscription", label: "Subscription ID", required: true, placeholder: "00000000-0000-0000-0000-000000000000" },
  { key: "resource_group", label: "Resource group", placeholder: "rg-gmf-credit-dev" },
  { key: "container_registry", label: "Container registry (ACR)", placeholder: "acrgmfcreditdev" },
  { key: "service_bus_namespace", label: "Service Bus namespace", placeholder: "sb-gmf-credit-dev" },
  { key: "key_vault", label: "Key Vault", placeholder: "kv-gmf-credit-dev" },
  { key: "log_analytics", label: "Log Analytics workspace", placeholder: "log-gmf-credit-dev" },
  { key: "location", label: "Location", placeholder: "eastus2" },
];

/**
 * The Azure connection for the project. The service principal secret is posted once, stored
 * encrypted by the API and never shown again. Connect later is a supported state.
 */
export function AzureConnectForm({
  projectId,
  onConnectLater,
  canConnectLater,
  user,
}: {
  projectId: string;
  onConnectLater: (note: string) => Promise<void>;
  canConnectLater: string | null;
  user: string;
}) {
  const qc = useQueryClient();
  const azure = useAzure(projectId);
  const [form, setForm] = useState<AzureConnectBody>({ tenant: "", subscription: "", runtime: "container_app" });
  const [busy, setBusy] = useState<"connect" | "later" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const conn = azure.data;

  const set = (k: keyof AzureConnectBody, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const connect = async () => {
    setBusy("connect");
    setError(null);
    try {
      const body = Object.fromEntries(Object.entries(form).filter(([, v]) => typeof v === "string" && v.trim() !== "")) as unknown as AzureConnectBody;
      await api.connectAzure(projectId, body);
      setForm((f) => ({ ...f, client_id: "", client_secret: "" }));
      qc.invalidateQueries({ queryKey: ["azure", projectId] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
      setForm((f) => ({ ...f, client_secret: "" }));
    }
  };

  const disconnect = async () => {
    setBusy("disconnect");
    try {
      await api.disconnectAzure(projectId);
      qc.invalidateQueries({ queryKey: ["azure", projectId] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const later = async () => {
    setBusy("later");
    setError(null);
    try {
      await onConnectLater(note.trim());
      setNote("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title="Azure connection"
      icon={<AzureIcon size={18} />}
      eyebrow="Subscription and service principal"
      testId="azure-form"
      actions={
        conn?.connected ? (
          <>
            <Pill tone="gate">connected</Pill>
            <button type="button" className="btn-secondary" onClick={disconnect} disabled={!!busy}>
              <Unplug size={15} aria-hidden /> Disconnect
            </button>
          </>
        ) : (
          <Pill tone="det">not connected</Pill>
        )
      }
    >
      {error ? <ErrorNote error={error} /> : null}
      {conn?.connected ? (
        <div className="mb-4 grid gap-x-6 gap-y-1 rounded-btn border border-line bg-page px-4 py-3 text-[13px] md:grid-cols-2">
          <p>
            <span className="label mr-1.5">Tenant</span>
            <span className="font-mono">{conn.tenant}</span>
          </p>
          <p>
            <span className="label mr-1.5">Subscription</span>
            <span className="font-mono">{conn.subscription}</span>
          </p>
          {Object.entries(conn.settings ?? {})
            .filter(([k, v]) => v && k !== "tenant" && k !== "subscription")
            .map(([k, v]) => (
              <p key={k}>
                <span className="label mr-1.5">{k.replace(/_/g, " ")}</span>
                <span className="font-mono">{v}</span>
              </p>
            ))}
          <p>
            <span className="label mr-1.5">Client secret</span>
            {conn.secret_stored ? "stored encrypted, not shown" : "none"}
          </p>
          {conn.warning ? <p className="text-decision md:col-span-2">{conn.warning}</p> : null}
        </div>
      ) : null}
      <form
        className="grid gap-3 md:grid-cols-2 xl:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault();
          // Enter submits too: the same guard as the button.
          if (busy || !form.tenant || !form.subscription) return;
          connect();
        }}
      >
        {FIELDS.map((f) => (
          <label key={f.key} className="text-[13px] text-slate">
            {f.label}
            {f.required ? <span className="text-coral"> *</span> : null}
            <input
              className="input mt-1 font-mono text-[13px]"
              value={(form[f.key] as string) ?? ""}
              required={f.required}
              placeholder={f.placeholder}
              onChange={(e) => set(f.key, e.target.value)}
            />
          </label>
        ))}
        <label className="text-[13px] text-slate">
          Runtime
          <select className="input select mt-1" value={form.runtime} onChange={(e) => set("runtime", e.target.value)}>
            <option value="container_app">Azure Container Apps</option>
            <option value="aks">AKS</option>
          </select>
        </label>
        <fieldset className="grid gap-3 rounded-btn border border-line p-3 md:col-span-2 md:grid-cols-2 xl:col-span-3">
          <legend className="px-1 text-[13px] font-semibold text-plum">Service principal</legend>
          <label className="text-[13px] text-slate">
            Client ID
            <input className="input mt-1 font-mono text-[13px]" value={form.client_id ?? ""} onChange={(e) => set("client_id", e.target.value)} autoComplete="off" />
          </label>
          <label className="text-[13px] text-slate">
            Client secret
            <input
              type="password"
              className="input mt-1 font-mono text-[13px]"
              value={form.client_secret ?? ""}
              onChange={(e) => set("client_secret", e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <p className="text-[12px] text-slate md:col-span-2">
            Posted once over the API, stored encrypted, never displayed again. Leave both empty to plan with an existing az login.
          </p>
        </fieldset>
        <div className="flex items-end md:col-span-2 xl:col-span-4">
          <button type="submit" className="btn-primary" disabled={!!busy || !form.tenant || !form.subscription}>
            {busy === "connect" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Cloud size={15} aria-hidden />}
            Connect Azure
          </button>
        </div>
      </form>
      <div className="mt-5 border-t border-line pt-4">
        <p className="text-[14px] font-semibold text-plum">Connect later</p>
        <p className="mt-0.5 text-[13px] text-slate">
          An operations user records why Azure is not connected yet. The generated assets and their last validation stay the
          evidence for G6 until a plan runs. Recorded as <span className="font-medium text-plum">{user}</span>.
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="min-w-[320px] flex-1 text-[13px] text-slate">
            Connect-later note
            <input className="input mt-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Subscription access requested from the platform team" />
          </label>
          <span title={canConnectLater ?? undefined}>
            <button type="button" className="btn-secondary" onClick={later} disabled={!!busy || !note.trim() || !!canConnectLater}>
              {busy === "later" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
              Connect later
            </button>
          </span>
        </div>
        {canConnectLater ? <p className="mt-1 text-[12.5px] text-slate">{canConnectLater}</p> : null}
      </div>
    </Panel>
  );
}
