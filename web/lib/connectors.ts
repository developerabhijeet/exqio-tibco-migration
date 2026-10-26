"use client";

// Settings → Connectors: the queries, the one call that tests a connection, and what each
// connector's card shows. The API decides who is an admin (GET /settings/connectors answers the
// full view only to one), so the screen reads that from the answer rather than from the persona.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useAppState } from "./state";
import type { ConnectionTest, ConnectorFlags, ConnectorKind, ConnectorSettings } from "./buildTypes";

export const CONNECTOR_KINDS: readonly ConnectorKind[] = ["github", "atlassian", "figma"];

/** The full view for an admin, the enabled flags for anyone else; keyed by user because of that. */
export function useConnectorSettings() {
  const { user } = useAppState();
  return useQuery({ queryKey: ["settings-connectors", user], queryFn: api.connectorSettings });
}

/** Which connectors are on, for any signed-in user: the actions elsewhere read this. */
export function useConnectors() {
  return useQuery({ queryKey: ["connectors"], queryFn: api.connectors, staleTime: 30_000 });
}

/** Run a connector's connection test now (admin; the API keeps it as the last test). */
export function testConnector(kind: ConnectorKind): Promise<ConnectionTest> {
  return api.testConnector(kind);
}

/** Whether the answer is the admin's view (only an admin is told which secrets are mounted). */
export function isAdminView(view: ConnectorSettings): boolean {
  return view.secrets_present !== undefined;
}

export type FieldKind = "url" | "text" | "list";

export interface ConnectorField {
  key: string;
  label: string;
  kind: FieldKind;
  hint?: string;
}

export interface ConnectorSection {
  /** A product within the connector (Confluence and Jira share the Atlassian credential). */
  title?: string;
  /** GET /connectors flag that says this product is on (Atlassian only). */
  flag?: "confluence" | "jira";
  fields: ConnectorField[];
}

export interface ConnectorMeta {
  name: string;
  summary: string;
  /** The URL field that gets the `connector-<kind>-url` test id: where requests go first. */
  primaryUrl: string;
  sections: ConnectorSection[];
  /** What each secret role is, in words. */
  secrets: Record<string, string>;
}

export const CONNECTOR_META: Record<ConnectorKind, ConnectorMeta> = {
  github: {
    name: "GitHub",
    summary: "Push generated solutions to exiqo/ branches, open pull requests and clone sources.",
    primaryUrl: "base_url",
    sections: [
      {
        fields: [
          { key: "base_url", label: "Base URL", kind: "url", hint: "GitHub Enterprise Server, for example https://github.gmf.example" },
          { key: "api_url", label: "API URL", kind: "url", hint: "Empty: the base URL's /api/v3" },
          { key: "default_owner", label: "Default owner", kind: "text", hint: "Organisation or user the repository field starts with" },
        ],
      },
    ],
    secrets: { token: "Access token" },
  },
  atlassian: {
    name: "Atlassian",
    summary: "Publish architecture pages to Confluence and raise Jira issues from findings, questions and parity differences.",
    primaryUrl: "confluence_url",
    sections: [
      {
        title: "Confluence",
        flag: "confluence",
        fields: [
          { key: "confluence_url", label: "Confluence URL", kind: "url" },
          { key: "space_key", label: "Space key", kind: "text" },
          { key: "parent_page_id", label: "Parent page id", kind: "text", hint: "Architecture pages are created under this page" },
        ],
      },
      {
        title: "Jira",
        flag: "jira",
        fields: [
          { key: "jira_url", label: "Jira URL", kind: "url" },
          { key: "project_key", label: "Project key", kind: "text" },
          { key: "issue_type", label: "Issue type", kind: "text" },
          { key: "labels", label: "Labels", kind: "list", hint: "Comma separated" },
        ],
      },
    ],
    secrets: { token: "API token", user: "User (Cloud)" },
  },
  figma: {
    name: "Figma",
    summary: "Attach Figma frames to a design as references, with their text and thumbnails.",
    primaryUrl: "api_url",
    sections: [
      {
        fields: [
          { key: "api_url", label: "API URL", kind: "url" },
          { key: "web_url", label: "Web URL", kind: "url", hint: "Used for links to frames only" },
          { key: "file_keys", label: "Allowed file keys", kind: "list", hint: "Comma separated; empty allows any file" },
        ],
      },
    ],
    secrets: { token: "Access token" },
  },
};

export type ConnectorState =
  | "install-off"
  | "off"
  | "not-configured"
  | "failed"
  | "changed"
  | "ready"
  | "untested"
  | "on";

export const STATE_LABEL: Record<ConnectorState, string> = {
  "install-off": "Off in this install",
  off: "Off",
  "not-configured": "Not configured",
  failed: "Last test failed",
  changed: "Settings changed since last test",
  ready: "Ready",
  untested: "Not tested yet",
  on: "On",
};

/** URL fields of a connector, from its sections. */
export function urlFields(kind: ConnectorKind): string[] {
  return CONNECTOR_META[kind].sections.flatMap((s) => s.fields.filter((f) => f.kind === "url").map((f) => f.key));
}

/** The URL fields requests go to (the API checks their host); web_url is only shown as links. */
const LINK_ONLY = new Set(["web_url"]);

/** The URL a connection test reports as its site: the first request URL that is set (the API's
 * GitHub base URL, else API URL; Confluence, else Jira; the Figma API URL). */
const SITE_FIELDS: Record<ConnectorKind, string[]> = {
  github: ["base_url", "api_url"],
  atlassian: ["confluence_url", "jira_url"],
  figma: ["api_url"],
};

const trimSlash = (u: string) => u.replace(/\/+$/, "");

/** Whether the connector now points elsewhere than where its last test went. Only the URL is
 * comparable: the API tells whether a secret is mounted, not which one, so a rotated token cannot
 * be seen here (a missing one already reads as not configured). */
export function siteChangedSinceTest(kind: ConnectorKind, view: ConnectorSettings): boolean {
  const site = view.last_test?.result.site;
  if (!site) return false;
  const record = view as unknown as Record<string, unknown>;
  const current = SITE_FIELDS[kind].map((f) => String(record[f] ?? "")).find(Boolean) ?? "";
  return trimSlash(site) !== trimSlash(current);
}

/** What the card leads with: the install ceiling, the toggle, then credentials, URL and last test.
 * `changedLocally`: this screen saved a URL or the toggle after the last test. */
export function connectorState(
  kind: ConnectorKind,
  view: ConnectorSettings,
  { changedLocally = false }: { changedLocally?: boolean } = {},
): ConnectorState {
  if (!view.install_enabled) return "install-off";
  if (!view.enabled) return "off";
  if (!isAdminView(view)) return "on";
  const secretsMissing = Object.values(view.secrets_present ?? {}).some((present) => !present);
  const record = view as unknown as Record<string, unknown>;
  const hasUrl = urlFields(kind).some((f) => !LINK_ONLY.has(f) && !!record[f]);
  if (secretsMissing || !hasUrl) return "not-configured";
  const last = view.last_test;
  if (!last) return "untested";
  if (!last.result.ok) return "failed";
  return changedLocally || siteChangedSinceTest(kind, view) ? "changed" : "ready";
}

/** Atlassian products that are on (GET /connectors), for the section headers. */
export function productOn(flags: ConnectorFlags | undefined, flag: "confluence" | "jira"): boolean | undefined {
  return flags ? flags.atlassian[flag] : undefined;
}

// ---- Validation: the API's rules (api/app/connectors/config.py check_url, validate_app; the Jira
// project key and Figma file key patterns), so a mistake is shown at the field before a round trip.
// The API stays the authority: http is accepted here for 127.0.0.1 only, and the API also needs
// its loopback flag for that.

export type FieldErrors = Record<string, string>;

const LOOPBACK = "127.0.0.1";
export const JIRA_PROJECT_KEY = /^[A-Z][A-Z0-9_]*$/;
export const FIGMA_FILE_KEY = /^[A-Za-z0-9_-]{1,64}$/;

/** The API's host_allowed: `host:port` exactly, or a bare `host` on the scheme's default port. */
export function hostAllowed(url: URL, allowed: readonly string[]): boolean {
  const host = url.hostname.toLowerCase();
  if (!host) return false;
  const fallback = url.protocol === "https:" ? 443 : 80;
  const port = url.port ? Number(url.port) : fallback;
  return allowed.some((entry) => {
    const m = /^(\[[^\]]+\]|[^:\s]+)(?::(\d+))?$/.exec(entry.trim());
    if (!m || m[1].toLowerCase() !== host) return false;
    return m[2] === undefined ? port === fallback : Number(m[2]) === port;
  });
}

/** Why `value` is not a URL the API accepts, or null. `allowed` undefined: the host is not checked. */
export function urlProblem(value: string, allowed: readonly string[] | undefined): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return "not a valid URL";
  }
  if (url.username || url.password || /^[a-z]+:\/\/[^/]*@/i.test(value)) return "credentials in the URL are not allowed";
  const loopback = url.protocol === "http:" && url.hostname === LOOPBACK;
  if (url.protocol !== "https:" && !loopback) return "https required";
  if (allowed && !hostAllowed(url, allowed)) return `host ${url.host} not in allowedHosts`;
  return null;
}

const listOf = (text: string) =>
  text
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

export interface ValidationInput {
  /** The form's values per field (lists as comma separated text; URL fields are overrides). */
  values: Record<string, string>;
  /** The toggle as the form holds it. */
  enabled: boolean;
  /** The install layer from the admin view (its URLs fill an empty override). */
  install?: Record<string, unknown>;
  /** The install's allowlist; undefined when not known (then hosts are left to the API). */
  allowedHosts?: readonly string[];
}

/** Field errors of a connector's form, by field key; empty when the API would accept it. */
export function validateConnector(kind: ConnectorKind, input: ValidationInput): FieldErrors {
  const errors: FieldErrors = {};
  const v = (key: string) => (input.values[key] ?? "").trim();
  const effective = (key: string) => v(key) || String(input.install?.[key] ?? "").trim();
  for (const field of urlFields(kind)) {
    const value = v(field);
    if (!value) continue;
    const problem = urlProblem(value, LINK_ONLY.has(field) ? undefined : input.allowedHosts);
    if (problem) errors[field] = problem;
  }
  if (kind === "atlassian") {
    if (!v("issue_type")) errors.issue_type = "issue type must not be empty";
    const key = v("project_key");
    if (key && !JIRA_PROJECT_KEY.test(key)) errors.project_key = "not a Jira project key (capital letters, digits, _)";
  }
  if (kind === "figma") {
    const bad = listOf(input.values.file_keys ?? "").filter((k) => !FIGMA_FILE_KEY.test(k));
    if (bad.length) errors.file_keys = `not a Figma file key: ${bad.join(", ")}`;
  }
  if (input.enabled) {
    const required = (field: string, message: string) => {
      if (!errors[field] && !effective(field)) errors[field] = message;
    };
    if (kind === "github") required("base_url", "required while GitHub is on (here or connectors.github.baseUrl)");
    if (kind === "figma") required("api_url", "required while Figma is on (here or connectors.figma.apiUrl)");
    if (kind === "atlassian" && !effective("confluence_url") && !effective("jira_url") && !errors.confluence_url) {
      errors.confluence_url = "set a Confluence or a Jira URL while Atlassian is on";
    }
  }
  return errors;
}

/** A refused PUT (422) as field errors plus whatever names no field. `detail` is the API's string
 * (`"api_url: host x not in allowedHosts"`), or FastAPI's validation array as JSON
 * (`[{"loc": ["body", "github", "api_url"], "msg": "..."}]`). */
export function serverErrors(kind: ConnectorKind, detail: string): { fields: FieldErrors; message: string | null } {
  const known = new Set(CONNECTOR_META[kind].sections.flatMap((s) => s.fields.map((f) => f.key)));
  const fields: FieldErrors = {};
  const general: string[] = [];
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(detail);
  } catch {
    /* a plain message */
  }
  if (Array.isArray(parsed)) {
    for (const item of parsed as { loc?: unknown[]; msg?: unknown }[]) {
      const loc = (Array.isArray(item?.loc) ? item.loc : []).map(String);
      const msg = String(item?.msg ?? "invalid");
      const field = loc[loc.length - 1];
      if (field && known.has(field)) fields[field] = msg;
      else general.push(loc.length > 1 ? `${loc.slice(1).join(".")}: ${msg}` : msg);
    }
  } else if (parsed && typeof parsed === "object") {
    general.push(JSON.stringify(parsed));
  } else {
    const m = /^([a-z_]+)(?::\s*|\s+)([\s\S]+)$/.exec(detail);
    if (m && known.has(m[1])) fields[m[1]] = m[2];
    else general.push(detail);
  }
  return { fields, message: general.length ? general.join("; ") : null };
}
