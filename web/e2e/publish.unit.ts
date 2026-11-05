import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import {
  ACTION_ROLES,
  actionError,
  DEV_ROLES,
  detailText,
  findingCandidates,
  parityCandidates,
  parityRef,
  questionCandidates,
} from "../lib/publish";
import type { ParityView } from "../lib/buildTypes";

// The publish actions name what they file by the server's identities and gate on the server's
// roles; these pin both against the API's source, so a change on one side fails here.

const ROOT = join(__dirname, "..", "..");
const source = (path: string) => readFileSync(join(ROOT, path), "utf8");

test.describe("parity references", () => {
  test("parityRef is the server's parity_key(scenario.key, field)", () => {
    // api/app/connectors/jira.py: the identity a parity difference's issue and link carry (X17).
    const jira = source("api/app/connectors/jira.py");
    expect(jira).toMatch(/def parity_key\(scenario_key: str, field: str\) -> str:\s+return f"\{scenario_key\}:\{field\}"/);
    // services/jira_issues.issue_source keys the differences by parity_key(scenario.key, d.field),
    // where d.field is the report's `path` (services/verify stores FieldDiff.field = d.path).
    expect(source("api/app/services/jira_issues.py")).toContain("key = parity_key(skey, d.field)");
    expect(source("api/app/services/verify.py")).toMatch(/FieldDiff\(run_id=run\.id, kind=kind_name, field=d\.path/);
    expect(parityRef("FundingStatusLookup:happy", "output.body.status")).toBe("FundingStatusLookup:happy:output.body.status");
  });

  test("the defects of a parity run, once each, fields and side effects", () => {
    const parity = JSON.parse(readFileSync(join(__dirname, "fixtures", "parity.json"), "utf8")) as ParityView;
    const refs = parityCandidates(parity.reports).map((c) => c.item);
    expect(refs).toEqual([
      { type: "parity", ref: "FundingStatusLookup:happy:output.body.status" },
      { type: "parity", ref: "FundingStatusLookup:happy:output.body.dealerRegion" },
    ]);
    // A report without its scenario key (an older API) offers nothing rather than a wrong ref.
    expect(parityCandidates([{ ...parity.reports[0], scenario_key: undefined }])).toEqual([]);
  });
});

test.describe("findings and questions", () => {
  test("findings by key: undecided and Fix are trackable, keep and ask are not", () => {
    const c = findingCandidates([
      { id: "a", finding_key: "S1-P-1", text: "t1", decision: null, severity: "high" },
      { id: "b", finding_key: "S2-P-1", text: "t2", decision: "fix" },
      { id: "c", finding_key: "S3-P-1", text: "t3", decision: "keep" },
      { id: "d", text: "no key" },
    ]);
    expect(c.map((x) => [x.item.ref, x.trackable])).toEqual([
      ["S1-P-1", true],
      ["S2-P-1", true],
      ["S3-P-1", false],
    ]);
    expect(c[2].note).toBe("decided keep");
  });

  test("questions by their id, never their position", () => {
    const c = questionCandidates([
      { question: "Q one?", recommendation: "r", question_id: "0123456789abcdef" },
      { question: "Q two?", recommendation: "r", question_id: "fedcba9876543210", decision: "answered" },
      { question: "No id (older API)", recommendation: "r" },
    ]);
    expect(c.map((x) => [x.item, x.trackable])).toEqual([
      [{ type: "question", ref: "0123456789abcdef" }, true],
      [{ type: "question", ref: "fedcba9876543210" }, false],
    ]);
  });
});

test.describe("roles", () => {
  test("DEV_ROLES is the API's DEV_PERSONAS", () => {
    const roles = source("api/app/auth/roles.py");
    const block = /DEV_PERSONAS[^=]*=\s*\{([\s\S]*?)\n\}/.exec(roles)?.[1] ?? "";
    const parsed: Record<string, string[]> = {};
    for (const m of block.matchAll(/"([^"]+)":\s*frozenset\(\{([^}]*)\}\)/g)) {
      parsed[m[1]] = [...m[2].matchAll(/"([^"]+)"/g)].map((r) => r[1]).sort();
    }
    expect(Object.keys(parsed).length).toBeGreaterThan(0);
    const mine = Object.fromEntries(Object.entries(DEV_ROLES).map(([k, v]) => [k, [...v].sort()]));
    expect(mine).toEqual(parsed);
  });

  test("ACTION_ROLES are the routes' require_roles", () => {
    const router = source("api/app/routers/connectors.py");
    const route = (name: string) =>
      [...(new RegExp(`${name} = Annotated\\[Principal, Depends\\(require_roles\\(([^)]*)\\)\\)\\]`).exec(router)?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    expect([...ACTION_ROLES.github]).toEqual(route("PushRoles"));
    expect([...ACTION_ROLES.confluence]).toEqual(route("PublishRoles"));
    expect([...ACTION_ROLES.jira]).toEqual(route("IssueRoles"));
    expect([...ACTION_ROLES.figma]).toEqual(route("FigmaRoles"));
  });
});

test.describe("error text", () => {
  test("a string detail is shown as it is", () => {
    expect(detailText("the same issues are already queued for this process")).toBe(
      "the same issues are already queued for this process",
    );
  });

  test("a validation list reads as field: message", () => {
    const detail = [
      { type: "string_too_short", loc: ["body", "items", 0, "ref"], msg: "String should have at least 1 character" },
      { type: "missing", loc: ["body", "repo"], msg: "Field required" },
    ];
    expect(detailText(detail)).toBe("items.0.ref: String should have at least 1 character; repo: Field required");
    // ApiError keeps a non-string detail as JSON (lib/api.ts): actionError reads it back.
    expect(actionError({ detail: JSON.stringify(detail) })).toBe(
      "items.0.ref: String should have at least 1 character; repo: Field required",
    );
    expect(actionError({ detail: "[not json" })).toBe("[not json");
    expect(actionError(new Error("boom"))).toBe("boom");
  });
});
