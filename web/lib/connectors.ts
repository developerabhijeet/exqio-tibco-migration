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

export type ConnectorState = "install-off" | "off" | "not-configured" | "failed" | "ready" | "untested" | "on";

export const STATE_LABEL: Record<ConnectorState, string> = {
  "install-off": "Off in this install",
  off: "Off",
  "not-configured": "Not configured",
  failed: "Last test failed",
  ready: "Ready",
  untested: "Not tested yet",
  on: "On",
};

/** URL fields of a connector, from its sections. */
export function urlFields(kind: ConnectorKind): string[] {
  return CONNECTOR_META[kind].sections.flatMap((s) => s.fields.filter((f) => f.kind === "url").map((f) => f.key));
}

/** What the card leads with: the install ceiling, the toggle, then credentials, URL and last test. */
export function connectorState(kind: ConnectorKind, view: ConnectorSettings): ConnectorState {
  if (!view.install_enabled) return "install-off";
  if (!view.enabled) return "off";
  if (!isAdminView(view)) return "on";
  const secretsMissing = Object.values(view.secrets_present ?? {}).some((present) => !present);
  const record = view as unknown as Record<string, unknown>;
  const hasUrl = urlFields(kind).some((f) => f !== "web_url" && !!record[f]);
  if (secretsMissing || !hasUrl) return "not-configured";
  const last = view.last_test;
  if (!last) return "untested";
  return last.result.ok ? "ready" : "failed";
}

/** Atlassian products that are on (GET /connectors), for the section headers. */
export function productOn(flags: ConnectorFlags | undefined, flag: "confluence" | "jira"): boolean | undefined {
  return flags ? flags.atlassian[flag] : undefined;
}
