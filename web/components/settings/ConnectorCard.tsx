"use client";

// One connector of Settings → Connectors. It leads with the state (off in the install, off,
// not configured, ready, last test failed), then the install ceiling, the toggle, the URLs and
// defaults, the credentials (their Key Vault names and whether each file is mounted: never a
// value) and the connection test. An admin edits and tests; everyone else reads.
import { useId, useMemo, useState } from "react";
import { CheckCircle2, CircleSlash, KeyRound, Loader2, Lock, PlugZap, Save, ServerCog, XCircle } from "lucide-react";
import { errorText } from "@/components/tabs/build/Panel";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import {
  CONNECTOR_META,
  STATE_LABEL,
  connectorState,
  isAdminView,
  productOn,
  serverErrors,
  testConnector,
  urlFields,
  validateConnector,
  type ConnectorField,
  type ConnectorState,
  type FieldErrors,
} from "@/lib/connectors";
import { ApiError } from "@/lib/api";
import type {
  ConnectionTest,
  ConnectorFlags,
  ConnectorKind,
  ConnectorLastTest,
  ConnectorPatch,
  ConnectorSettings,
  ConnectorSettingsView,
} from "@/lib/buildTypes";

const STATE_TONE: Record<ConnectorState, "det" | "decision" | "error" | "gate" | "purple"> = {
  "install-off": "det",
  off: "det",
  "not-configured": "decision",
  failed: "error",
  changed: "decision",
  ready: "gate",
  untested: "purple",
  on: "gate",
};

const INPUT =
  "mt-1 w-full rounded-btn border border-line bg-surface px-2.5 py-1.5 text-md text-ink placeholder:text-slate disabled:cursor-not-allowed disabled:bg-page disabled:text-slate";

type Form = { enabled: boolean; values: Record<string, string> };

function fieldValue(view: ConnectorSettings, field: ConnectorField): string {
  if (field.kind === "url") return view.overrides?.[field.key] ?? "";
  const raw = (view as unknown as Record<string, unknown>)[field.key];
  if (Array.isArray(raw)) return raw.join(", ");
  return typeof raw === "string" ? raw : "";
}

function formOf(kind: ConnectorKind, view: ConnectorSettings): Form {
  const fields = CONNECTOR_META[kind].sections.flatMap((s) => s.fields);
  return { enabled: !!view.app_enabled, values: Object.fromEntries(fields.map((f) => [f.key, fieldValue(view, f)])) };
}

const asList = (text: string) =>
  text
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

/** Only what changed: the API checks the fields it is sent, and audits their names. */
function patchOf(kind: ConnectorKind, initial: Form, form: Form): ConnectorPatch {
  const patch: ConnectorPatch = {};
  if (form.enabled !== initial.enabled) patch.enabled = form.enabled;
  for (const field of CONNECTOR_META[kind].sections.flatMap((s) => s.fields)) {
    const now = form.values[field.key] ?? "";
    if (now === (initial.values[field.key] ?? "")) continue;
    patch[field.key] = field.kind === "list" ? asList(now) : now.trim();
  }
  return patch;
}

function when(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleString();
}

function TestResult({ last, testing, error }: { last: ConnectorLastTest | null; testing: boolean; error: string | null }) {
  if (testing) {
    return (
      <p className="flex items-center gap-2 text-slate">
        <Loader2 size={15} className="animate-spin" aria-hidden /> Testing the connection…
      </p>
    );
  }
  if (error) {
    return (
      <p className="flex items-start gap-2 text-error">
        <XCircle size={15} className="mt-0.5 shrink-0" aria-hidden /> {error}
      </p>
    );
  }
  if (!last) return <p className="text-slate">Not tested yet.</p>;
  const r: ConnectionTest = last.result;
  return (
    <div className="space-y-1">
      <p className={`flex flex-wrap items-center gap-2 font-semibold ${r.ok ? "text-gate" : "text-error"}`}>
        {r.ok ? <CheckCircle2 size={15} aria-hidden /> : <XCircle size={15} aria-hidden />}
        {r.ok ? "Connected" : "Connection failed"}
        {r.account ? <span className="font-normal text-ink">as {r.account}</span> : null}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        {r.site ? (
          <>
            <dt className="text-slate">Site</dt>
            <dd className="break-all font-mono text-ink">{r.site}</dd>
          </>
        ) : null}
        <dt className="text-slate">Latency</dt>
        <dd className="text-ink">{r.latency_ms} ms</dd>
        {r.scopes.length > 0 ? (
          <>
            <dt className="text-slate">Scopes</dt>
            <dd className="text-ink">{r.scopes.join(", ")}</dd>
          </>
        ) : null}
        {r.message ? (
          <>
            <dt className="text-slate">{r.ok ? "Notes" : "Reason"}</dt>
            <dd className={r.ok ? "text-ink" : "text-error"}>{r.message}</dd>
          </>
        ) : null}
      </dl>
      <p className="text-xs text-slate">
        Tested {when(last.at)} by {last.actor}
      </p>
    </div>
  );
}

export function ConnectorCard({
  kind,
  view,
  flags,
  user,
  onSaved,
  onTested,
}: {
  kind: ConnectorKind;
  view: ConnectorSettings;
  flags: ConnectorFlags | undefined;
  user: string;
  onSaved: (all: ConnectorSettingsView) => void;
  /** After a test: the API kept it as the last test, so the view is read again. */
  onTested: () => void;
}) {
  const meta = CONNECTOR_META[kind];
  const admin = isAdminView(view);
  const uid = useId();
  const [initial, setInitial] = useState<Form>(() => formOf(kind, view));
  const [form, setForm] = useState<Form>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [fresh, setFresh] = useState<ConnectorLastTest | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  // Fields the admin has edited (their client errors show from then on), whether Save was tried,
  // the API's refusal attached to fields, and whether this screen saved a URL or the toggle after
  // the last test.
  const [touched, setTouched] = useState<Set<string>>(() => new Set());
  const [submitted, setSubmitted] = useState(false);
  const [refused, setRefused] = useState<FieldErrors>({});
  const [changedLocally, setChangedLocally] = useState(false);

  const state = connectorState(kind, view, { changedLocally });
  const editable = admin && view.install_enabled && !busy;
  const patch = useMemo(() => patchOf(kind, initial, form), [kind, initial, form]);
  const dirty = Object.keys(patch).length > 0;
  const clientErrors = useMemo(
    () =>
      admin
        ? validateConnector(kind, {
            values: form.values,
            enabled: form.enabled,
            install: view.install,
            allowedHosts: view.allowed_hosts,
          })
        : {},
    [admin, kind, form, view.install, view.allowed_hosts],
  );
  const fieldError = (key: string): string | undefined =>
    refused[key] ?? (submitted || touched.has(key) ? clientErrors[key] : undefined);
  const edit = (key: string, value: string) => {
    setSaved(false);
    setTouched((t) => new Set(t).add(key));
    setRefused((r) => {
      if (!(key in r)) return r;
      const next = { ...r };
      delete next[key];
      return next;
    });
    setForm((f) => ({ ...f, values: { ...f.values, [key]: value } }));
  };
  const last = fresh ?? view.last_test ?? null;
  const tid = (suffix: string) => `connector-${kind}-${suffix}`;

  const save = async () => {
    setSubmitted(true);
    setSaved(false);
    setError(null);
    const invalid = Object.keys(clientErrors);
    if (invalid.length > 0) {
      document.getElementById(`${uid}-${invalid[0]}`)?.focus();
      return;
    }
    setBusy(true);
    try {
      const all = await api.putConnectorSettings({ [kind]: patch });
      const next = formOf(kind, all[kind]);
      setInitial(next);
      setForm(next);
      setTouched(new Set());
      setSubmitted(false);
      setRefused({});
      setSaved(true);
      if ("enabled" in patch || urlFields(kind).some((f) => f in patch)) setChangedLocally(true);
      onSaved(all);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const { fields, message } = serverErrors(kind, e.detail);
        setRefused(fields);
        setError(message);
        const first = Object.keys(fields)[0];
        if (first) document.getElementById(`${uid}-${first}`)?.focus();
      } else {
        setError(errorText(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestError(null);
    try {
      const result = await testConnector(kind);
      setFresh({ result, at: new Date().toISOString(), actor: user });
      setChangedLocally(false);
      onTested();
    } catch (e) {
      setFresh(null);
      setTestError(errorText(e));
    } finally {
      setTesting(false);
    }
  };

  const testBlocked = !admin
    ? "Connection tests are run by administrators."
    : !view.install_enabled
      ? "Enable the connector in the install first."
      : !view.enabled
        ? "Turn the connector on and save before testing it."
        : null;

  return (
    <section className="card" data-testid={`connector-${kind}`} aria-labelledby={`${uid}-title`}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`${uid}-title`} className="flex items-center gap-2 text-xl font-semibold text-heading">
            <PlugZap size={18} className="text-accent-ink" aria-hidden /> {meta.name}
          </h3>
          <p className="mt-1 max-w-[70ch] text-md text-slate">{meta.summary}</p>
        </div>
        <Pill tone={STATE_TONE[state]} testId={tid("state")}>
          {STATE_LABEL[state]}
        </Pill>
      </header>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <p
          data-testid={tid("install")}
          className="flex items-start gap-2 rounded-btn border border-line bg-page/60 px-3 py-2.5 text-sm text-ink"
        >
          {view.install_enabled ? (
            <ServerCog size={15} className="mt-0.5 shrink-0 text-gate" aria-hidden />
          ) : (
            <CircleSlash size={15} className="mt-0.5 shrink-0 text-slate" aria-hidden />
          )}
          <span>
            {view.install_enabled ? (
              <>
                Enabled by the install
                {kind === "atlassian" && view.install?.flavor ? (
                  <span className="text-slate"> ({view.install.flavor === "cloud" ? "Cloud" : "Data Center"})</span>
                ) : null}
              </>
            ) : (
              <>
                Off in this install: set <code className="font-mono text-xs">connectors.{kind}.enabled</code>
              </>
            )}
          </span>
        </p>
        <div className="rounded-btn border border-line px-3 py-2.5 text-sm">
          <label className="flex items-center gap-2 font-semibold text-heading">
            <input
              type="checkbox"
              role="switch"
              data-testid={tid("toggle")}
              checked={admin ? form.enabled : view.enabled}
              disabled={!editable}
              aria-describedby={`${uid}-toggle-hint`}
              onChange={(e) => {
                setSaved(false);
                setForm((f) => ({ ...f, enabled: e.target.checked }));
              }}
            />
            On in Settings
          </label>
          <p id={`${uid}-toggle-hint`} className="mt-0.5 text-xs text-slate">
            {!view.install_enabled
              ? "Cannot be turned on while the install leaves it off."
              : admin
                ? "Takes effect when saved; a connector that is off makes no requests."
                : "Changed by administrators."}
          </p>
        </div>
      </div>

      {admin ? (
        <fieldset disabled={!editable} className="mt-5 space-y-4">
          <legend className="sr-only">{meta.name} settings</legend>
          {meta.sections.map((section) => {
            const on = section.flag ? productOn(flags, section.flag) : undefined;
            return (
              <div key={section.title ?? "main"}>
                {section.title ? (
                  <h4 className="mb-2 flex items-center gap-2 text-md font-semibold text-heading">
                    {section.title}
                    {on !== undefined ? (
                      <Pill tone={on ? "gate" : "det"}>{on ? "on" : "off: needs the connector on and a URL"}</Pill>
                    ) : null}
                  </h4>
                ) : null}
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {section.fields.map((field) => {
                    const id = `${uid}-${field.key}`;
                    const err = fieldError(field.key);
                    const installValue = String(view.install?.[field.key] ?? "");
                    const placeholder =
                      field.kind === "url" ? installValue || "not set in the install" : field.kind === "list" ? "none" : "";
                    return (
                      <div key={field.key} className="text-sm">
                        <label htmlFor={id} className="label">
                          {field.label}
                        </label>
                        <input
                          id={id}
                          type={field.kind === "url" ? "url" : "text"}
                          inputMode={field.kind === "url" ? "url" : undefined}
                          spellCheck={false}
                          className={`${INPUT} ${field.kind === "url" ? "font-mono text-sm" : ""} ${err ? "border-error" : ""}`}
                          data-testid={field.key === meta.primaryUrl ? tid("url") : tid(`field-${field.key}`)}
                          placeholder={placeholder}
                          value={form.values[field.key] ?? ""}
                          aria-describedby={`${id}-hint`}
                          aria-invalid={err ? true : undefined}
                          aria-errormessage={err ? `${id}-error` : undefined}
                          onChange={(e) => edit(field.key, e.target.value)}
                        />
                        {err ? (
                          <p id={`${id}-error`} data-testid={`connector-${kind}-error-${field.key}`} className="mt-1 text-xs font-medium text-error">
                            {err}
                          </p>
                        ) : null}
                        <p id={`${id}-hint`} className="mt-1 text-xs text-slate">
                          {field.kind === "url"
                            ? `${field.hint ? `${field.hint}. ` : ""}Empty uses the install's value.`
                            : field.hint ?? ""}
                        </p>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          <div className="text-sm">
            <p className="label">Allowed hosts</p>
            <p className="mt-1 flex flex-wrap gap-1.5">
              {(view.allowed_hosts ?? []).length > 0 ? (
                view.allowed_hosts!.map((h) => (
                  <code key={h} className="rounded bg-page px-1.5 py-0.5 font-mono text-xs text-slate">
                    {h}
                  </code>
                ))
              ) : (
                <span className="text-slate">None in the install: every URL is refused.</span>
              )}
            </p>
            <p className="mt-1 text-xs text-slate">Set by the install (connectors.{kind}.allowedHosts); URLs must use https and one of these hosts.</p>
          </div>
        </fieldset>
      ) : null}

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <div className="text-sm">
          <h4 className="flex items-center gap-1.5 text-md font-semibold text-heading">
            <KeyRound size={15} aria-hidden /> Credentials
          </h4>
          {admin ? (
            <ul className="mt-2 divide-y divide-line rounded-btn border border-line">
              {Object.entries(view.secret_names ?? {}).map(([role, name]) => {
                const present = !!view.secrets_present?.[role];
                return (
                  <li key={role} data-testid={tid(`secret-${role}`)} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <span className="font-medium text-ink">{meta.secrets[role] ?? role}</span>
                    <code className="rounded bg-page px-1.5 py-0.5 font-mono text-xs text-slate">{name}</code>
                    <Pill tone={present ? "gate" : "error"} className="ml-auto">
                      {present ? "present" : "missing"}
                    </Pill>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-2 text-slate">Which credentials are mounted is shown to administrators.</p>
          )}
          <p className="mt-1.5 text-xs text-slate">
            Credentials come only from Key Vault mounted files; their values are never shown or stored here.
          </p>
        </div>
        <div className="text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-md font-semibold text-heading">Connection test</h4>
            <button
              type="button"
              className="btn-secondary"
              data-testid={tid("test")}
              disabled={!!testBlocked || testing || busy}
              aria-describedby={testBlocked ? `${uid}-test-hint` : undefined}
              onClick={test}
            >
              {testing ? <Loader2 size={15} className="animate-spin" aria-hidden /> : testBlocked ? <Lock size={14} aria-hidden /> : <PlugZap size={15} aria-hidden />}
              Test connection
            </button>
          </div>
          {testBlocked ? (
            <p id={`${uid}-test-hint`} className="mt-1 text-xs text-slate">
              {testBlocked}
            </p>
          ) : null}
          <div data-testid={tid("result")} aria-live="polite" aria-atomic="true" className="mt-2 rounded-btn border border-line px-3 py-2.5">
            {admin ? (
              <TestResult last={last} testing={testing} error={testError} />
            ) : (
              <p className="text-slate">Test results are shown to administrators.</p>
            )}
          </div>
        </div>
      </div>

      {admin ? (
        <footer className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <button type="button" className="btn-primary" data-testid={tid("save")} disabled={!editable || !dirty} onClick={save}>
            {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Save size={15} aria-hidden />} Save {meta.name}
          </button>
          <span role="status" className="text-sm text-gate">
            {saved ? "Saved." : ""}
          </span>
          {!dirty && !saved ? <span className="text-xs text-slate">No unsaved changes.</span> : null}
          {error ? (
            <div className="w-full">
              <ErrorNote error={error} />
            </div>
          ) : null}
        </footer>
      ) : (
        <p className="mt-5 flex items-center gap-2 border-t border-line pt-4 text-sm text-slate">
          <Lock size={14} aria-hidden /> Read only: connector settings and connection tests are managed by administrators.
        </p>
      )}
    </section>
  );
}
