import { expect, test } from "@playwright/test";
import {
  connectorState,
  hostAllowed,
  serverErrors,
  urlProblem,
  validateConnector,
} from "../lib/connectors";
import type { ConnectorSettings } from "../lib/buildTypes";

// The Settings → Connectors validator mirrors api/app/connectors/config.py (check_url,
// host_allowed, validate_app) and the Jira/Figma key patterns; these pin the same cases.

test.describe("urlProblem", () => {
  const allowed = ["github.gmf.example", "jira.gmf.example:8443", "127.0.0.1:9711"];

  test("https on an allowed host passes", () => {
    expect(urlProblem("https://github.gmf.example", allowed)).toBeNull();
    expect(urlProblem("https://github.gmf.example:443/api/v3", allowed)).toBeNull();
    expect(urlProblem("https://jira.gmf.example:8443", allowed)).toBeNull();
  });

  test("http only on the loopback (the API also needs its loopback flag)", () => {
    expect(urlProblem("http://github.gmf.example", allowed)).toBe("https required");
    expect(urlProblem("ftp://github.gmf.example", allowed)).toBe("https required");
    expect(urlProblem("http://127.0.0.1:9711/api/v3", allowed)).toBeNull();
    expect(urlProblem("http://localhost:9711", ["localhost:9711"])).toBe("https required");
  });

  test("credentials in the URL are refused", () => {
    expect(urlProblem("https://bot:secret@github.gmf.example", allowed)).toBe("credentials in the URL are not allowed");
    expect(urlProblem("https://bot@github.gmf.example", allowed)).toBe("credentials in the URL are not allowed");
  });

  test("a host or port outside the allowlist is named", () => {
    expect(urlProblem("https://elsewhere.example", allowed)).toBe("host elsewhere.example not in allowedHosts");
    expect(urlProblem("https://jira.gmf.example", allowed)).toBe("host jira.gmf.example not in allowedHosts");
    expect(urlProblem("https://github.gmf.example:8443", allowed)).toBe(
      "host github.gmf.example:8443 not in allowedHosts",
    );
  });

  test("not a URL; host unchecked when the allowlist is unknown", () => {
    expect(urlProblem("github.gmf.example", allowed)).toBe("not a valid URL");
    expect(urlProblem("https://anywhere.example", undefined)).toBeNull();
  });

  test("hostAllowed ignores case and matches the default port of a bare entry", () => {
    expect(hostAllowed(new URL("https://GitHub.GMF.example"), ["github.gmf.example"])).toBe(true);
    expect(hostAllowed(new URL("https://github.gmf.example:444"), ["github.gmf.example"])).toBe(false);
    expect(hostAllowed(new URL("https://github.gmf.example"), ["github.gmf.example:443"])).toBe(true);
  });
});

test.describe("validateConnector", () => {
  const install = { base_url: "", api_url: "" };

  test("an enabled GitHub needs a base URL, here or in the install", () => {
    expect(validateConnector("github", { values: { base_url: "" }, enabled: true, install, allowedHosts: [] })).toEqual({
      base_url: "required while GitHub is on (here or connectors.github.baseUrl)",
    });
    expect(
      validateConnector("github", {
        values: { base_url: "" },
        enabled: true,
        install: { base_url: "https://github.gmf.example" },
        allowedHosts: ["github.gmf.example"],
      }),
    ).toEqual({});
    expect(validateConnector("github", { values: { base_url: "" }, enabled: false, install, allowedHosts: [] })).toEqual({});
  });

  test("Atlassian: a Confluence or Jira URL when on, the project key pattern, a non-empty issue type", () => {
    const errors = validateConnector("atlassian", {
      values: { confluence_url: "", jira_url: "", project_key: "gmf-1", issue_type: " " },
      enabled: true,
      install: {},
      allowedHosts: [],
    });
    expect(errors).toEqual({
      confluence_url: "set a Confluence or a Jira URL while Atlassian is on",
      project_key: "not a Jira project key (capital letters, digits, _)",
      issue_type: "issue type must not be empty",
    });
    expect(
      validateConnector("atlassian", {
        values: { jira_url: "https://jira.gmf.example", project_key: "GMF_CREDIT2", issue_type: "Task" },
        enabled: true,
        allowedHosts: ["jira.gmf.example"],
      }),
    ).toEqual({});
  });

  test("Figma: file keys pattern; the web URL is a link only, so its host is not checked", () => {
    expect(
      validateConnector("figma", {
        values: { api_url: "https://figma.gmf.example", web_url: "https://www.figma.example", file_keys: "abc_123, bad key!" },
        enabled: true,
        allowedHosts: ["figma.gmf.example"],
      }),
    ).toEqual({ file_keys: "not a Figma file key: bad key!" });
    expect(
      validateConnector("figma", { values: { web_url: "http://www.figma.example" }, enabled: false, allowedHosts: [] }),
    ).toEqual({ web_url: "https required" });
  });
});

test.describe("serverErrors", () => {
  test("the API's field-prefixed string goes to that field", () => {
    expect(serverErrors("github", "api_url: host x.example not in allowedHosts")).toEqual({
      fields: { api_url: "host x.example not in allowedHosts" },
      message: null,
    });
    expect(serverErrors("atlassian", "issue_type must not be empty")).toEqual({
      fields: { issue_type: "must not be empty" },
      message: null,
    });
  });

  test("a message that names no field stays general", () => {
    expect(serverErrors("figma", "enable it in the install first (connectors.figma.enabled)")).toEqual({
      fields: {},
      message: "enable it in the install first (connectors.figma.enabled)",
    });
    expect(serverErrors("github", "github: Input should be a valid string").message).toBe(
      "github: Input should be a valid string",
    );
  });

  test("FastAPI's validation array (sent as JSON) is read per location", () => {
    const detail = JSON.stringify([
      { loc: ["body", "github", "base_url"], msg: "Input should be a valid string", type: "string_type" },
      { loc: ["body", "github", "token"], msg: "Extra inputs are not permitted", type: "extra_forbidden" },
    ]);
    expect(serverErrors("github", detail)).toEqual({
      fields: { base_url: "Input should be a valid string" },
      message: "github.token: Extra inputs are not permitted",
    });
  });
});

test.describe("connectorState", () => {
  const ok = { ok: true, account: "exiqo-bot", site: "https://github.gmf.example", scopes: [], latency_ms: 3, message: "" };
  const admin = (over: Partial<ConnectorSettings>): ConnectorSettings => ({
    install_enabled: true,
    enabled: true,
    secrets_present: { token: true },
    base_url: "https://github.gmf.example",
    last_test: { result: ok, at: "2026-10-26T10:00:00Z", actor: "ops.sam" },
    ...over,
  });

  test("states in order of precedence", () => {
    expect(connectorState("github", admin({ install_enabled: false, enabled: false }))).toBe("install-off");
    expect(connectorState("github", admin({ enabled: false }))).toBe("off");
    expect(connectorState("github", { install_enabled: true, enabled: true })).toBe("on");
    expect(connectorState("github", admin({ secrets_present: { token: false } }))).toBe("not-configured");
    expect(connectorState("github", admin({ base_url: "" }))).toBe("not-configured");
    expect(connectorState("github", admin({ last_test: null }))).toBe("untested");
    expect(connectorState("github", admin({ last_test: { result: { ...ok, ok: false }, at: "", actor: "" } }))).toBe("failed");
    expect(connectorState("github", admin({}))).toBe("ready");
  });

  test("a passing test of another URL, or a change saved here since, reads as changed", () => {
    expect(connectorState("github", admin({ base_url: "https://github2.gmf.example" }))).toBe("changed");
    expect(connectorState("github", admin({ base_url: "https://github.gmf.example/" }))).toBe("ready");
    expect(connectorState("github", admin({}), { changedLocally: true })).toBe("changed");
  });
});
