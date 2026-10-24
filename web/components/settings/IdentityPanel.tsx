"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Eye, EyeOff, ExternalLink, Loader2, LogIn, RotateCcw, Save, ShieldCheck, X } from "lucide-react";
import { Panel } from "@/components/tabs/build/Panel";
import { Pill } from "@/components/ui/Pill";
import { MicrosoftIcon } from "@/components/ui/BrandIcons";
import { useAppState } from "@/lib/state";
import {
  PROVIDERS,
  appsettings,
  fieldsFor,
  missing,
  providerOf,
  useIdentity,
  type IdentityConfig,
  type ProviderKey,
} from "@/lib/identity";

/**
 * A sign-in the way the configured provider would show it, so the flow can be seen before the
 * application is deployed behind it. It calls nothing, and the authorization line it shows is a
 * fixed string, not a credential.
 */
function SignInPreview({ config, onClose }: { config: IdentityConfig; onClose: () => void }) {
  const provider = providerOf(config.provider);
  const [stage, setStage] = useState<"account" | "signing" | "done">("account");
  const account = "firstname.lastname@gmfinancial.com";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" role="presentation">
      <button type="button" aria-label="Dismiss the sign-in preview" className="absolute inset-0 bg-plum/40" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${provider.name} sign-in preview`}
        data-testid="sso-preview"
        className="relative w-[440px] max-w-full overflow-hidden rounded-card bg-white shadow-pop"
      >
        <div className="flex items-center justify-between border-b border-line bg-warm px-5 py-2.5">
          <span className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.06em] text-slate">
            <ShieldCheck size={14} aria-hidden /> Sign-in preview
          </span>
          <button type="button" className="btn-ghost" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="px-7 py-7">
          <p className="text-[13px] font-semibold uppercase tracking-[0.06em] text-slate">{provider.name}</p>
          {stage === "account" ? (
            <>
              <h3 className="mt-3 font-display text-[22px] font-semibold text-plum">Sign in</h3>
              <p className="mt-1 text-[13px] text-slate">to continue to {config.audience || "the migrated service"}</p>
              <div className="mt-5 rounded-btn border border-line px-3 py-2.5 text-[14px] text-ink">{account}</div>
              <button
                type="button"
                className="btn-primary mt-4 w-full"
                data-testid="sso-continue"
                onClick={() => {
                  setStage("signing");
                  window.setTimeout(() => setStage("done"), 900);
                }}
              >
                Continue
              </button>
              <p className="mt-3 text-[12px] text-slate">
                {config.instance || "No authority endpoint is configured yet"}
              </p>
            </>
          ) : stage === "signing" ? (
            <div className="flex min-h-[200px] flex-col items-center justify-center text-center">
              <Loader2 size={28} className="animate-spin text-purple" aria-hidden />
              <p className="mt-3 text-[14px] text-slate">Redirecting to {provider.name}</p>
            </div>
          ) : (
            <div className="min-h-[200px]">
              <p className="flex items-center gap-2 text-[15px] font-semibold text-gate">
                <Check size={18} aria-hidden /> Signed in
              </p>
              <dl className="mt-4 space-y-2 text-[13px]">
                {[
                  ["Account", account],
                  ["Issuer", config.instance || "not configured"],
                  ["Audience", config.audience || "not configured"],
                  ["Scope", config.scopes || "not configured"],
                ].map(([k, v]) => (
                  <div key={k} className="flex gap-3">
                    <dt className="w-20 shrink-0 text-slate">{k}</dt>
                    <dd className="min-w-0 flex-1 break-all font-mono text-[12px] text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-4 rounded-btn bg-page px-3 py-2 font-mono text-[11px] text-slate">
                Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6…
              </p>
            </div>
          )}
        </div>

        <p className="border-t border-line bg-page px-5 py-2.5 text-[11.5px] leading-snug text-slate">
          The sign-in your callers see, drawn from the values above. Nothing leaves this page and no credential is
          held here.
        </p>
      </div>
    </div>
  );
}

/**
 * Where the identity provider for the generated application is configured: Microsoft Entra ID
 * (Azure AD) or Okta, with the endpoint, client and audience it validates tokens against. Each
 * field names the configuration key the generated service actually reads, and the JSON beside them
 * is the appsettings section those values produce.
 *
 * Secrets typed here are never written to storage. They stay in the form while the page is open,
 * because the deployed service reads its secrets from Key Vault with its managed identity, which is
 * the only place one belongs.
 */
export function IdentityPanel() {
  const { user } = useAppState();
  const { config, set, save, reset, saved } = useIdentity();
  const [preview, setPreview] = useState(false);
  const [reveal, setReveal] = useState<string | null>(null);
  const provider = providerOf(config.provider);
  const fields = fieldsFor(config.provider);
  const gaps = missing(config);

  return (
    <Panel
      title="Authentication"
      eyebrow="The identity provider the generated application trusts"
      testId="identity-panel"
      actions={
        <>
          <Pill tone={gaps.length === 0 ? "gate" : "decision"} testId="identity-state">
            {gaps.length === 0 ? "configured" : `${gaps.length} field${gaps.length === 1 ? "" : "s"} to fill`}
          </Pill>
          <button type="button" className="btn-secondary" data-testid="identity-preview" onClick={() => setPreview(true)}>
            <LogIn size={15} aria-hidden /> Preview sign-in
          </button>
          {/* The same screen on its own route, for when a link is wanted rather than a dialog over
              the settings it was opened from. */}
          <Link href="/signin" className="btn-secondary" data-testid="identity-signin-page">
            <ExternalLink size={15} aria-hidden /> Sign-in screen
          </Link>
          <button type="button" className="btn-primary" data-testid="identity-save" onClick={save}>
            {saved ? <Check size={15} aria-hidden /> : <Save size={15} aria-hidden />} {saved ? "Saved" : "Save"}
          </button>
        </>
      }
    >
      <fieldset className="flex flex-wrap gap-2" data-testid="identity-providers">
        <legend className="label mb-1.5">Provider</legend>
        {PROVIDERS.map((p) => {
          const on = p.key === config.provider;
          return (
            <button
              key={p.key}
              type="button"
              role="radio"
              aria-checked={on}
              data-testid={`identity-provider-${p.key}`}
              onClick={() => set("provider", p.key as ProviderKey)}
              className={`flex min-w-[230px] flex-1 flex-col items-start gap-0.5 rounded-card border px-4 py-3 text-left transition-colors ${
                on ? "border-purple bg-purple-lt" : "border-line bg-white hover:bg-warm"
              }`}
            >
              <span className="flex w-full items-center gap-2">
                {p.key === "entra" ? <MicrosoftIcon size={16} /> : <ShieldCheck size={16} className="text-slate" aria-hidden />}
                <span className={`text-[14.5px] font-semibold ${on ? "text-purple" : "text-plum"}`}>{p.name}</span>
                {on ? <Check size={15} className="ml-auto text-purple" aria-hidden /> : null}
              </span>
              <span className="text-[12px] text-slate">
                {p.also} · {p.docs}
              </span>
            </button>
          );
        })}
      </fieldset>

      <div className="mt-5 grid items-start gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          {fields.map((f) => {
            const value = String(config[f.key] ?? "");
            const open = reveal === f.key;
            return (
              <label key={f.key} className="block text-[13px]">
                <span className="flex flex-wrap items-baseline gap-2">
                  <span className="label">{f.label}</span>
                  {f.optional ? <span className="text-[11px] text-slate">optional</span> : null}
                  <code className="ml-auto rounded bg-page px-1.5 py-0.5 font-mono text-[11px] text-slate">
                    {f.setting(provider.section)}
                  </code>
                </span>
                <span className="mt-1 flex items-center gap-2">
                  <input
                    className="input"
                    type={f.secret && !open ? "password" : "text"}
                    autoComplete="off"
                    spellCheck={false}
                    data-testid={`identity-${f.key}`}
                    placeholder={f.placeholder}
                    value={value}
                    onChange={(e) => set(f.key, e.target.value as never)}
                  />
                  {f.secret ? (
                    <button
                      type="button"
                      className="btn-ghost shrink-0"
                      aria-label={open ? `Hide ${f.label}` : `Show ${f.label}`}
                      onClick={() => setReveal(open ? null : f.key)}
                    >
                      {open ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  ) : null}
                </span>
                <span className="mt-0.5 block text-[12px] text-slate">{f.hint}</span>
              </label>
            );
          })}

          <div className="flex flex-wrap items-end gap-4 pt-1">
            <label className="flex items-center gap-2 text-[13px] text-ink">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[color:var(--purple)]"
                data-testid="identity-https"
                checked={config.requireHttps}
                onChange={(e) => set("requireHttps", e.target.checked)}
              />
              Require HTTPS for provider metadata
            </label>
            <label className="text-[13px]">
              <span className="label">Clock skew (minutes)</span>
              <input
                className="input mt-1 w-28"
                inputMode="numeric"
                data-testid="identity-skew"
                value={config.clockSkew}
                onChange={(e) => set("clockSkew", e.target.value)}
              />
            </label>
            <button type="button" className="btn-secondary ml-auto" data-testid="identity-reset" onClick={reset}>
              <RotateCcw size={15} aria-hidden /> Clear
            </button>
          </div>
        </div>

        <div>
          <p className="label">Generated appsettings section</p>
          <pre
            data-testid="identity-appsettings"
            className="mt-1 max-h-[340px] overflow-auto rounded-btn border border-line bg-page p-3 font-mono text-[11.5px] leading-5 text-ink"
          >
            {appsettings(config)}
          </pre>
          {gaps.length > 0 ? (
            <p className="mt-2 rounded-btn bg-decision-bg px-3 py-2 text-[12.5px] text-ink" data-testid="identity-gaps">
              Still to fill: {gaps.map((f) => f.label).join(", ")}.
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-5 grid gap-3 text-[12.5px] leading-relaxed text-slate md:grid-cols-2">
        <p className="rounded-btn border border-line bg-page px-3 py-2">
          <span className="font-semibold text-plum">Secrets are not stored here.</span> A client secret or API token
          typed above stays in this form while the page is open and is never written to storage. The generated
          application reads its secrets from Azure Key Vault with its managed identity, so no secret is carried in its
          configuration, its container environment or its image.
        </p>
        <p className="rounded-btn border border-line bg-page px-3 py-2">
          <span className="font-semibold text-plum">Where these values go.</span> Each field names the key the generated
          service reads. In Development the service issues its own token for Swagger and the verifier; in every other
          environment it validates provider tokens against this section. Saving records the configuration against your
          user ({user}); it is not yet written into a build, so change it freely while the design is being reviewed.
          The sign-in screen at <code className="font-mono text-[11.5px]">/signin</code> walks the flow these values
          describe; it contacts no provider, and no screen of this application requires it.
        </p>
      </div>

      {preview ? <SignInPreview config={config} onClose={() => setPreview(false)} /> : null}
    </Panel>
  );
}
