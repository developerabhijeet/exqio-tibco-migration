# Inline Citations and UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every phase's citations inline at the sentences they support (replacing the separate Cites rows, evidence rows and evidence blocks) with the chip and preview ExiQoian uses, and give the app a roomier, token-driven SaaS layout with correct light and dark themes.

**Architecture:** Pure anchoring functions in `engine/citations/claims.py` turn each phase's text plus its existing citations (or deterministic entity mentions) into SP3's `CitedText`; `api/app/services/citations.py` adds `cited` fields to the Discover, Validate, Design, Build, Verify and Readiness payloads on read. On the web, colours become RGB-channel CSS variables with light and dark values, a type scale and layout tokens replace arbitrary sizes through a one-time codemod guarded by `check-tokens.mjs`, the shell, cards, tables and stepper are restyled, and each phase renders `cited` through the shared `CitedText` component.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, Next.js 15, React 19, Tailwind 3.4, TanStack Query 5, Cytoscape 3.34, React Flow 12, Monaco 0.52, Recharts 3.2, Playwright 1.56, Node 22 (scripts).

**Spec:** `docs/superpowers/specs/2026-10-25-inline-citations-ui-design.md`

**Depends on (verified by Task 1, which fails fast):**
- Sub-project 2: `engine/memory/model.py::Citation`; `engine/memory/projectors/{estate,analysis,design,build}.py` (`project_estate`, `project_analysis`, `project_design`, `project_build`, uid conventions `activity:<ir>/<id>`, `chunk:<ir>:rule:<key>`, `chunk:<ir>:finding:<key>`, `chunk:<ir>:design:decision:<slug>`, `method:dotnet:<ir>/<project>/<class>.<method>`); `GET /memory/{project_id}/node`; `web/app/memory/page.tsx` reading `?uid=`.
- Sub-project 3: `engine/citations/{model,spans}.py` (`CiteTarget`, `CiteRef`, `CiteSpan`, `CitedText`, `sentence_bounds`), `web/lib/citations.ts` (`segments`, `useCiteNavigate`, `PHASE_LABEL`, `PHASE_TONE`), `web/components/citations/{CitationChip,CitationPreview,CitedText}.tsx`, `web/components/exiqoian/ExiQoianPanel.tsx`.

## Global Constraints

- Branch: work and commit on `feat/gmf-deploy` only; never create a branch; never push. Commit with `git add <the task's files>` only (another agent may share the tree): never `git add -A`, `git stash`, `git reset` or `git checkout -- <path>`.
- No outbound hosts and no new runtime dependency (npm or Python); `make egress-audit` passes.
- Viewer read-only server-side: no new write route; `cited` fields are read-only additions to GET payloads.
- Additive API: every existing field stays with its current shape; `cited` is added beside it.
- `CitedText` invariants hold on every payload; offsets are Unicode code points; no source citation is dropped (tail anchor).
- Token values exactly the spec's section 3 tables; contrast pairs ≥ 4.5:1 in both themes; type scale and codemod mapping exactly the spec's (nearest step, ties upward, floor 11 px).
- Layout values: content `max-w-[1600px]`; gutter 16 / 24 / 40 px (`<640`, `<1280`, `≥1280`); section gap 32 px; card padding 24 px (20 px `<640`); radius `btn 8`, `node 10`, `card 14`, `panel 20`; chip preview delay 150 ms; panel motion 200 ms; `motion-reduce` disables transitions.
- `localStorage` key `exiqo.theme` with values `system | light | dark`; `data-theme` on `<html>`.
- Test ids kept: `step-<key>`, `user-menu`, `process-view`, `mapping-row`, `domain-select`, `process-list`, `file-tree`, `scan-progress`, `counter-processes`; renamed deliberately: `citation-chip` → `cite-chip`.
- Existing suites stay green apart from selectors this plan updates: `.venv/bin/pytest`, `make e2e`; `.venv/bin/ruff check .` clean; `cd web && npm run typecheck && npm run lint` clean (lint now includes `check-tokens.mjs`).

## Review Focus

- A rule text edited by the SME so that it no longer names its cited activity: the chip moves to the last sentence (tail anchor), still clickable, never disappears. Test in Task 2 (`test_unmatched_ref_goes_to_tail`) and Task 3 (`test_edited_rule_keeps_every_citation`).
- An activity id that is a prefix of another (`Lookup` vs `LookupDealer`, `R1` vs `R10`): the chip lands on the sentence naming the exact one. Test in Task 2 (`test_hints_match_whole_words_only`).
- A user with OS dark mode who never touched the toggle, and a reload after choosing Light on a dark OS: the right theme from first paint, no flash. Test in Task 5 (Playwright `theme follows the system and the choice persists`).
- A 390 px phone viewport on Validate with long rule texts and the mapping table on Design: no horizontal page scroll; tables scroll inside their card. Test in Task 11 (`no horizontal scroll at 390 px`).
- Cytoscape domain graph and React Flow canvas after switching theme at runtime: node labels and edges recoloured without reload. Test in Task 6 (`graph and canvas recolour on theme change`).

## File Structure

| Path | Responsibility |
| --- | --- |
| `engine/citations/claims.py`, `engine/citations/uids.py` (create) | `HintedRef`, `anchor`, `link_entities`, `tail`; memory uid mirror |
| `engine/tests/test_citation_claims.py` (create) | builders and uid mirror |
| `api/app/services/citations.py` (create) | per-phase `cited` builders |
| `api/app/schemas/analysis.py`, `api/app/routers/{analysis,domains,design,build,verify,readiness}.py` or the services that shape those payloads (modify) | attach `cited` |
| `api/tests/test_cited_payloads.py`, `api/tests/test_citations_prereqs.py` (create) | payload invariants; prerequisites |
| `web/lib/theme.css`, `web/tailwind.config.ts`, `web/app/layout.tsx`, `web/lib/theme.ts` (create), `web/components/shell/UserMenu.tsx` | tokens, theme switch |
| `web/scripts/check-tokens.mjs`, `web/scripts/codemod-tokens.mjs` (create), `web/package.json` | guard and one-time codemod |
| `web/components/**`, `web/app/**` (modify, codemod) | token classes |
| `web/components/shell/{Chrome,TopBar,SecondaryScreen}.tsx`, `web/components/ui/{Empty,Pill,PageHeader}.tsx` | shell, header, empty states |
| `web/components/stepper/{Stepper,NextStep}.tsx`, `web/app/page.tsx` | calmer stepper, compacting header |
| `web/components/tabs/**` (modify) | inline citations per phase |
| `web/components/tabs/validate/CitationChip.tsx`, `web/components/evidence/EvidenceDrawer.tsx` (delete) | replaced / dead |
| `web/e2e/citations.spec.ts`, `web/e2e/visual.spec.ts` (create), existing specs (modify), `web/playwright.config.ts`, `Makefile` (`e2e-visual`) | tests |

---

### Task 1: Verify sub-project 2 and 3 prerequisites (fail fast)

**Files:**
- Create: `api/tests/test_citations_prereqs.py`

**Interfaces:**
- Consumes: the header's dependency list.

- [ ] **Step 1: Write the test**

```python
def test_sp3_citation_model_exists():
    from engine.citations.model import CiteRef, CiteSpan, CitedText, CiteTarget  # noqa: F401
    from engine.citations.spans import sentence_bounds
    assert sentence_bounds("A. B.") == [(0, 2), (3, 5)]
    with pytest.raises(ValueError):
        CitedText(text="ab", spans=[CiteSpan(start=0, end=3, refs=[])], refs=[])

def test_sp2_projectors_and_routes_exist(app):
    from engine.memory.projectors.analysis import project_analysis  # noqa: F401
    from engine.memory.projectors.design import project_design  # noqa: F401
    from engine.memory.projectors.build import project_build  # noqa: F401
    assert "/memory/{project_id}/node" in {r.path for r in app.routes}

def test_web_prerequisites_exist():
    for p in ("web/lib/citations.ts", "web/components/citations/CitationChip.tsx", "web/components/citations/CitationPreview.tsx",
              "web/components/citations/CitedText.tsx", "web/components/exiqoian/ExiQoianPanel.tsx", "web/app/memory/page.tsx"):
        assert Path(p).exists(), f"missing prerequisite {p} (sub-project 2 or 3 not done)"
    assert "useCiteNavigate" in Path("web/lib/citations.ts").read_text()
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_citations_prereqs.py -v` — expect 3 passed. On any failure stop and report the missing sub-project task.
- [ ] **Step 3: Commit** `test(citations): fail fast on missing sub-project 2 and 3 interfaces`.

---

### Task 2: Claim anchoring and the memory uid mirror (engine)

**Files:**
- Create: `engine/citations/claims.py`, `engine/citations/uids.py`, `engine/tests/test_citation_claims.py`

**Interfaces:**
- Consumes: SP3 `CiteRef`, `CitedText`, `CiteSpan`, `sentence_bounds`.
- Produces in `claims.py`: `HintedRef(BaseModel)` (`ref: CiteRef`, `hints: list[str] = []`); `anchor(text: str, hinted: list[HintedRef]) -> CitedText`; `link_entities(text: str, entities: dict[str, CiteRef]) -> list[HintedRef]`; `tail(text: str, refs: list[CiteRef]) -> CitedText`; `hint_variants(hint: str) -> list[str]` (`"LookupDealer"` → `["LookupDealer", "Lookup Dealer"]`, `"num_of_pulls"` → `["num_of_pulls", "num of pulls"]`). `anchor("", refs)` with refs raises `ValueError("cannot anchor citations on empty text")`; callers anchor on the item's key or label instead.
- Produces in `uids.py`: `activity_uid(ir_id, activity_id)`, `process_uid(ir_id)`, `rule_chunk_uid(ir_id, key)`, `finding_chunk_uid(ir_id, key)`, `decision_chunk_uid(ir_id, title)`, `method_uid(ir_id, project, cls, method)`, `slug(title)` (SP2's rule, imported from the design projector if it exports it, else mirrored).

- [ ] **Step 1: Write the failing tests**

```python
def test_chip_goes_on_the_sentence_naming_the_activity():
    ct = anchor("Funding starts at the queue. LookupDealer rejects inactive dealers.", [HintedRef(ref=r("LookupDealer"), hints=["LookupDealer"])])
    assert [(s.start, s.end, s.refs) for s in ct.spans] == [(29, 66, ["c1"])]

def test_refs_on_one_sentence_merge_in_order():
    ct = anchor("LookupDealer then CheckFunding.", [HintedRef(ref=r("a"), hints=["CheckFunding"]), HintedRef(ref=r("b"), hints=["LookupDealer"])])
    assert len(ct.spans) == 1 and ct.spans[0].refs == ["c1", "c2"]

def test_unmatched_ref_goes_to_tail():
    ct = anchor("First claim. Second claim.", [HintedRef(ref=r("x"), hints=["Nowhere"])])
    assert ct.spans[-1].start == 13 and ct.refs[0].id == "c1"

def test_hints_match_whole_words_only():
    ct = anchor("R10 applies. R1 applies.", [HintedRef(ref=r("R1"), hints=["R1"])])
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "R1 applies"
    ct = anchor("Lookup runs. LookupDealer runs.", [HintedRef(ref=r("LookupDealer"), hints=["LookupDealer"])])
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "LookupDealer runs"

def test_camel_case_hint_matches_spaced_prose():
    ct = anchor("The lookup dealer step checks status.", [HintedRef(ref=r("a"), hints=["LookupDealer"])])
    assert ct.spans[0].start == 0

def test_link_entities_links_only_known_names():
    found = link_entities("Map LookupDealer per R1; ignore Foo.", {"LookupDealer": r("act"), "R1": r("rule")})
    assert [h.ref.id for h in found] == ["act", "rule"]

def test_no_ref_is_ever_lost():
    refs = [HintedRef(ref=r(f"x{i}"), hints=[f"none{i}"]) for i in range(5)]
    ct = anchor("Only one sentence.", refs)
    assert {i for s in ct.spans for i in s.refs} == {f"c{i}" for i in range(1, 6)}

def test_uids_match_sp2_projectors():
    d = project_analysis(REF, [chunk_input(kind="rule", key="R1", activities=["a1"])])
    assert rule_chunk_uid(REF.ir_id, "R1") in {n.uid for n in d.nodes}
    assert activity_uid(REF.ir_id, "a1") in {r.dst for r in d.rels}
    dd = project_design(REF, design_record_with_decision("Use Dapper", "Dapper", ["EF Core"], "simple SQL"), "dr1")
    assert decision_chunk_uid(REF.ir_id, "Use Dapper") == dd.nodes[0].uid
```

(`REF`, `chunk_input`, `design_record_with_decision` are the helpers of SP2's projector tests; import them from that test module.)

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_citation_claims.py -q` — expect FAIL (module missing).
- [ ] **Step 3: Implement** both modules; matching is `re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", sentence, re.IGNORECASE)` over `hint_variants`.
- [ ] **Step 4: Run** — expect all passed.
- [ ] **Step 5: Commit** `feat(citations): sentence anchoring, entity linking and memory uid mirror`.

---

### Task 3: `cited` on Discover and Validate payloads

**Files:**
- Create: `api/app/services/citations.py`, `api/tests/test_cited_payloads.py`
- Modify: `api/app/schemas/analysis.py` (`cited: CitedText | None = None` on `DomainOut`, `RuleOut`, `FindingOut`), the code that builds those out-models (`api/app/routers/domains.py`, `api/app/routers/analysis.py` or their services)

**Interfaces:**
- Consumes: Task 2; SP3 `CiteRef`, `CiteTarget`.
- Produces in `services/citations.py`:
  - `activity_ref(ref_id: str, process_id: str, ir_id: str, activity_id: str, activity_name: str, quote: str, *, verified: bool | None, reason: str | None, phase: UiPhase) -> CiteRef` (label `activity_name or activity_id` cut to 60; preview = masked `quote` cut to 280; `memory_uid = activity_uid(ir_id, activity_id)`; target `activity`);
  - `cite_domain(domain: Domain) -> CitedText` — rationale sentences per evidence kind, in the order of first appearance: `table` → "Shares the tables {a, b}.", `queue`/`topic` → "Uses the queues {…}.", `call` → "Calls between {…}.", `module` → "Lives in the modules {…}.", `name` → "Named alike: {…}.", any other kind → "{Kind}: {texts}."; one ref per evidence item (kind `evidence`, phase `discover`, target `step` discover, preview = the item text), hinted by its text;
  - `cite_rule(rule, process: Process) -> CitedText` and `cite_finding(finding, process: Process) -> CitedText` — one `activity_ref` per citation in order, hints `[activity_id, activity_name] + quoted identifiers in citation.text`; empty text → `tail(rule.key, refs)`.

- [ ] **Step 1: Write the failing tests** (sample scan + analysis through the fake provider, like `api/tests/test_analysis_gates.py`)

```python
async def test_rules_and_findings_carry_valid_cited_text(client, analysed):
    a = (await client.get(f"/processes/{analysed.proc}/analysis")).json()
    for item in a["rules"] + a["findings"]:
        ct = CitedText.model_validate(item["cited"])           # invariants hold
        assert ct.text == item["text"]
        assert len(ct.refs) == len(item["citations"])           # nothing lost
        assert {r.target.activity_id for r in ct.refs} == {c["activity_id"] for c in item["citations"]}

async def test_unverified_citation_keeps_its_reason(client, analysed):
    # make one citation unverifiable: point it at an activity id the IR lacks, as test_analysis_gates does for failures
    await break_first_citation(client, analysed)
    refs = [r for x in (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"] for r in x["cited"]["refs"]]
    bad = next(r for r in refs if r["verified"] is False)
    assert bad["reason"] and bad["target"]["kind"] == "activity"

async def test_edited_rule_keeps_every_citation(client, analysed):
    rule = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"][0]
    await client.patch(f"/rules/{rule['id']}", json={"text": "Completely reworded."}, headers=SME)  # the existing rule-edit route
    after = next(r for r in (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"] if r["id"] == rule["id"])
    ct = CitedText.model_validate(after["cited"])
    assert len(ct.refs) == len(after["citations"]) and ct.spans[-1].end == len("Completely reworded")

async def test_domain_rationale_cites_each_evidence_item(client, scanned):
    for d in (await client.get(f"/projects/{scanned}/domains")).json():
        ct = CitedText.model_validate(d["cited"])
        assert len(ct.refs) == len(d["evidence"]) and all(r.phase == "discover" for r in ct.refs)

async def test_memory_uids_follow_sp2_conventions(client, analysed):
    ref = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"][0]["cited"]["refs"][0]
    assert ref["memory_uid"] == f"activity:{analysed.ir_id}/{ref['target']['activity_id']}"
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_cited_payloads.py -q` — expect FAIL (`KeyError: 'cited'`).
- [ ] **Step 3: Implement** the builders and attach them where the out-models are built; activity names come from `Process.ir["activities"]`.
- [ ] **Step 4: Run** — expect all passed; `.venv/bin/pytest api/tests/test_analysis_gates.py -q` green.
- [ ] **Step 5: Commit** `feat(api): inline citation spans on domains, rules and findings`.

---

### Task 4: `cited` on Design, Build, Verify and Readiness payloads

**Files:**
- Modify: `api/app/services/citations.py`, `api/app/routers/design.py` (`GET /processes/{id}/design`), `api/app/routers/build.py` (`GET /builds/{id}/reviews`), `api/app/routers/verify.py` (`GET /builds/{id}/parity`), `api/app/services/readiness.py` (rows of `GET /projects/{id}/readiness`), `api/tests/test_cited_payloads.py`

**Interfaces:**
- Produces in `services/citations.py`:
  - `design_entities(process: Process, design: dict, rules: list, findings: list) -> dict[str, CiteRef]` — activity ids and names (activity refs, phase `design`), rule keys and finding keys (phase `validate`, target `step` validate with the process, `memory_uid` `rule_chunk_uid`/`finding_chunk_uid`), table and queue names from the IR (phase `discover`);
  - `cite_design(design: dict, entities) -> dict` — adds `narrative_cited` and `decisions[i].cited` (text `f"{choice}. {reason}"` via `link_entities` + `anchor`; a decision naming nothing gets no spans and `refs == []`, rendered as plain text);
  - `cite_review(finding: dict, build_id: str, process: Process) -> CitedText` — a `file` ref (`path`, `line`, label `f"{basename}:{line}"`, phase `build`) hinted by the basename, class and method names, plus linked activities;
  - `cite_diff(diff: dict, run: dict) -> CitedText` — text `f"{path}: expected {e}, got {a}."` (+ `" " + note` when set), a `verify` ref for the scenario run (target `step` verify) and, with `finding_id`, a `validate` ref for the finding; hinted by `path`;
  - `cite_readiness_row(row: dict) -> CitedText` — the row's status sentence with `tail` over one ref per evidence item (target from its `kind`/`ref`, as `web/app/readiness/page.tsx`'s `target()` resolves today).

- [ ] **Step 1: Write the failing tests** (append)

```python
async def test_design_decisions_link_named_activities_and_rules(client, designed):
    d = (await client.get(f"/processes/{designed.proc}/design")).json()
    ct = CitedText.model_validate(d["decisions"][0]["cited"])
    assert ct.text == f'{d["decisions"][0]["choice"]}. {d["decisions"][0]["reason"]}'
    CitedText.model_validate(d["narrative_cited"])
    named = [r for r in CitedText.model_validate(d["narrative_cited"]).refs if r.target.kind == "activity"]
    assert all(r.label in d["narrative"] or r.target.activity_id in d["narrative"] for r in named)

async def test_review_findings_cite_their_file(client, built):
    for f in (await client.get(f"/builds/{built.build}/reviews")).json():
        if f["file"]:
            ref = next(r for r in f["cited"]["refs"] if r["target"]["kind"] == "file")
            assert ref["target"]["path"] == f["file"] and ref["target"]["line"] == f["line"]

async def test_parity_differences_cite_their_finding(client, verified_with_defect):
    view = (await client.get(f"/builds/{verified_with_defect.build}/parity")).json()
    diff = next(d for d in all_field_diffs(view) if d["status"] == "defect")  # all_field_diffs walks the view's runs and their FieldDiff lists
    kinds = {r["phase"] for r in diff["cited"]["refs"]}
    assert "verify" in kinds and ("validate" in kinds) == bool(diff["finding_id"])

async def test_readiness_rows_cite_every_evidence_item(client, scanned):
    for row in (await client.get(f"/projects/{scanned}/readiness")).json()["rows"]:
        assert len(row["cited"]["refs"]) == len(row["evidence"])
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_cited_payloads.py -q` — expect the four new tests FAIL.
- [ ] **Step 3: Implement** and attach.
- [ ] **Step 4: Run** `.venv/bin/pytest api -q` — expect green; `.venv/bin/ruff check .` clean.
- [ ] **Step 5: Commit** `feat(api): inline citation spans on design, reviews, parity and readiness`.

---

### Task 5: Design tokens, dark theme and the theme switch

**Files:**
- Create: `web/lib/theme.ts`, `web/scripts/check-tokens.mjs`
- Modify: `web/lib/theme.css`, `web/tailwind.config.ts`, `web/app/layout.tsx`, `web/components/shell/UserMenu.tsx`, `web/package.json` (`"lint": "eslint . && node scripts/check-tokens.mjs"`, `"lint:tokens": "node scripts/check-tokens.mjs"`), `web/e2e/personas.spec.ts` (theme test appended)

**Interfaces:**
- Produces CSS variables `--c-<token>` (RGB channels) for every token of spec section 3 in `:root`, `[data-theme="dark"]` and the `prefers-color-scheme` block; legacy names (`warm`, `line-dk`, `purple-lt`, `purple`, `purple.dk`) kept as Tailwind aliases of the new tokens until Task 6's codemod removes their uses; `--gutter`, `--section-gap`, `--card-pad`, `--ease`, `--dur-fast: 120ms`, `--dur-panel: 200ms`.
- Produces Tailwind theme: `colors` mapped `rgb(var(--c-x) / <alpha-value>)`; `fontSize` replaced by the spec scale (`2xs` … `4xl`) plus the semantic aliases; `borderRadius` `btn 8px`, `node 10px`, `card 14px`, `panel 20px`; `boxShadow` `e1`, `e2`, `e3` (and `card`, `pop` as aliases of `e1`, `e2`); `maxWidth.content: "1600px"`.
- Produces `theme.ts`: `type ThemeChoice = "system" | "light" | "dark"`; `THEME_KEY = "exiqo.theme"`; `readTheme(): ThemeChoice` (try/catch, default `system`); `applyTheme(choice)` (sets or removes `data-theme`, dispatches `window` event `themechange` with `{ dark: boolean }`); `useTheme(): { choice, dark, setChoice }`; `NO_FLASH_SCRIPT: string` (inline in `<head>` of `layout.tsx` via `dangerouslySetInnerHTML`).
- UserMenu: a "Theme" `radiogroup` with `menuitemradio` items "System", "Light", "Dark".
- `check-tokens.mjs`: the five rules of spec section 5; exits 1 listing `file:line rule`; prints `check-tokens: ok (<n> files)` on success.

- [ ] **Step 1: Write the failing check** — create `check-tokens.mjs` first and run `cd web && node scripts/check-tokens.mjs` — expect FAIL listing the current arbitrary sizes, `bg-white`, `text-plum`, `text-purple` (rule 5 passes once tokens exist; before, it fails with `missing token`).
- [ ] **Step 2: Implement** tokens, Tailwind config, theme switch and no-flash script; temporarily run the check with `--rules=5` (contrast only) — expect `ok`.
- [ ] **Step 3: Write the Playwright test** in `personas.spec.ts`

```ts
test("theme follows the system and the choice persists", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" }); await page.goto("/");
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(15, 11, 20)");
  await page.getByTestId("user-menu").click(); await page.getByRole("menuitemradio", { name: "Light" }).click();
  await page.reload();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(250, 249, 247)");
});
```

- [ ] **Step 4: Run** `cd web && npm run typecheck` — clean; `make e2e` — green (full lint waits for Task 6).
- [ ] **Step 5: Commit** `feat(web): design tokens with light and dark themes and a theme switch`.

---

### Task 6: Codemod to tokens; theme-aware canvas, graphs, charts, code

**Files:**
- Create: `web/scripts/codemod-tokens.mjs`
- Modify: every file under `web/components`, `web/app`, `web/lib` the codemod touches; `web/components/canvas/{ProcessCanvas,FlowEdge,ActivityNode,CodeView}.tsx`, `web/components/tabs/discover/DomainGraph.tsx`, `web/components/tabs/build/{MetricsRadar,CodeViewer,TibcoSourcePane}.tsx` and any other Monaco, Cytoscape or Recharts consumer found by `rg -l "monaco|cytoscape|recharts" web/components web/app`

**Interfaces:**
- Consumes: Task 5 tokens, `useTheme`, `themechange`.
- Produces the codemod (idempotent, prints a summary per rule): `text-[<n>px]` → scale per spec (9.5, 10, 10.5, 11 → `2xs`; 11.5, 12 → `xs`; 12.5, 13 → `sm`; 13.5, 14 → `md`; 14.5, 15 → `base`; 16 → `lg`; 18 → `xl`; 21, 22 → `2xl`; 24, 26 → `3xl`; ≥ 28 → `4xl`; any other value → nearest, ties up); `bg-white` (no modifier) → `bg-surface`; `text-plum` → `text-heading`; `text-purple` → `text-accent-ink`; `bg-purple-lt` → `bg-accent-soft`; `bg-warm` → `bg-sunken`; `border-line-dk` → `border-line-strong`; `shadow-2xl`/`shadow-xl` → `shadow-e3`; `bg-white/NN`, `text-white`, `bg-plum`, `border-plum` unchanged (brand bar and filled buttons).
- Produces `DomainGraph` setting `window.__cy` to its Cytoscape instance when `process.env.NODE_ENV !== "production"` (test hook).
- Produces `cssColor(token: string): string` in `theme.ts` (reads `getComputedStyle(document.documentElement)` and returns `rgb(r g b)`); Cytoscape and Recharts colours built from it and rebuilt on `themechange`; Monaco `theme={dark ? "vs-dark" : "vs"}`.

- [ ] **Step 1: Run** `cd web && node scripts/codemod-tokens.mjs` — expect a summary with counts per rule (about 640 size replacements, 35 `bg-white`); run it again — expect all counts 0 (idempotent).
- [ ] **Step 2: Fix** what the codemod cannot (hex colours in `style`, conditional class strings built with template literals) until `node scripts/check-tokens.mjs` prints `check-tokens: ok`.
- [ ] **Step 3: Write the Playwright test** `graph and canvas recolour on theme change` (in `upload-discover.spec.ts`, after the scan): switch to Dark from the user menu; expect the Cytoscape container's computed node label colour (via `cy.nodes()[0].style("color")` on `window.__cy` exposed in dev only) to equal `rgb(236, 232, 242)` and a React Flow edge path's computed `stroke` to differ from its light value.
- [ ] **Step 4: Run** `cd web && npm run typecheck && npm run lint` — clean; `make e2e` — green.
- [ ] **Step 5: Commit** `refactor(web): token classes everywhere; theme-aware canvas, graphs, charts and code`.

---

### Task 7: Shell, page headers, cards, tables, empty states

**Files:**
- Create: `web/components/ui/PageHeader.tsx`
- Modify: `web/components/shell/{Chrome,TopBar,SecondaryScreen,ProjectSelector}.tsx`, `web/components/ui/{Empty,Pill}.tsx`, `web/lib/theme.css` (`.card`, `.card-header`, `.table`, `.page`, `.section` component classes), the secondary pages `web/app/{settings,history,tokenomics,readiness,help}/page.tsx` and `web/app/memory/page.tsx` (adopt `PageHeader` and `.table`)

**Interfaces:**
- Produces `PageHeader({ eyebrow, title, description?, actions? })` (`h1` with `screen-title`, wraps under 768 px).
- Produces `Chrome` column `mx-auto w-full max-w-content px-[var(--gutter)] py-8`; `TopBar` "More" menu (`aria-label="More"`, `data-testid="topbar-more"`) holding the secondary links below 1280 px, Memory added to the links, the ExiQoian launcher kept visible at every width; the second bar row below 768 px.
- `.table`: spec section 4 values; wide tables wrapped in `.table-scroll` (`overflow-x-auto`).

- [ ] **Step 1: Implement** the components and classes; replace each secondary page's own header markup with `PageHeader`; wrap every `<table>` in `.table-scroll` and give it `.table` (`rg -n "<table" web/components web/app` lists them).
- [ ] **Step 2: Run** `cd web && npm run typecheck && npm run lint` — clean; `make e2e` — green (secondary-screen specs: `help.spec.ts`, `standards-identity.spec.ts`).
- [ ] **Step 3: Commit** `feat(web): roomier shell, page headers, cards and tables`.

---

### Task 8: The calmer stepper and compacting project header

**Files:**
- Modify: `web/components/stepper/Stepper.tsx`, `web/components/stepper/NextStep.tsx`, `web/app/page.tsx`, the e2e specs that read stepper status text (`rg -n "step-" web/e2e`)

**Interfaces:**
- Produces `Stepper` per spec section 4: `role="tablist"` (`aria-label="Migration steps"`), each step `role="tab"` `aria-selected` with `data-testid="step-<key>"`, `data-status="<status>"`, an 8 px status dot, the status pill (`data-testid="step-status-pill"`) only on the active step, `title` tooltip `"<label>: <status label>"`, Left/Right/Home/End keys; connector filled to the last finished step; below 1024 px a `button` `data-testid="stepper-compact"` "Step <n> of 7 · <label>" opening a `listbox` of the steps.
- Produces in `page.tsx`: the pinned header compacts after 48 px of scroll (`data-compact="true"`), `--pinned-h` still measured.

- [ ] **Step 1: Write the Playwright test** in `personas.spec.ts`

```ts
test("the stepper shows one status pill and works from the keyboard", async ({ page }) => {
  await page.goto("/"); await newProjectAndScan(page);
  await expect(page.getByRole("tablist", { name: "Migration steps" }).getByTestId("step-status-pill")).toHaveCount(1);
  await page.getByTestId("step-discover").focus(); await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("step-validate")).toBeFocused();
  await page.setViewportSize({ width: 900, height: 900 });
  await page.getByTestId("stepper-compact").click(); await page.getByRole("option", { name: /Design/ }).click();
  await expect(page.getByTestId("step-design")).toHaveAttribute("aria-selected", "true");
});
```

- [ ] **Step 2: Run** it — expect FAIL.
- [ ] **Step 3: Implement**; update any existing spec that asserted per-step status pills to read `data-status` instead (deliberate selector change, noted in the commit body).
- [ ] **Step 4: Run** `make e2e` — expect green.
- [ ] **Step 5: Commit** `feat(web): calmer stepper with keyboard and compact modes`.

---

### Task 9: Inline citations in Discover and Validate

**Files:**
- Modify: `web/components/tabs/discover/DiscoverTab.tsx` (replace the "Domain evidence" chip row with `<CitedText value={current.cited} />` under the domain select), `web/components/tabs/validate/{RuleCard,FindingCard}.tsx` (render `rule.cited`/`finding.cited` in place of the text paragraph; remove the "Cites" row, keeping owner and memory-refs pills in the card footer), `web/components/citations/CitationPreview.tsx` (lazy `GET /memory/{p}/node?uid=` for `memory_uid`: revision line, "Not in memory yet" on 404), `web/lib/types.ts` (`cited?: CitedText` on `Domain`, `Rule`, `Finding`), `web/e2e/validate-design.spec.ts` (`citation-chip` → `cite-chip`)
- Delete: `web/components/tabs/validate/CitationChip.tsx`

**Interfaces:**
- Consumes: Task 3 payloads; SP3 `CitedText`, `useCiteNavigate`.
- Editing a rule still shows the raw text in the textarea; after save the card re-renders `cited` from the refetched analysis.

- [ ] **Step 1: Write the Playwright tests** in `web/e2e/citations.spec.ts`

```ts
test("Validate shows citations inside the rule text and they open the canvas", async ({ page }) => {
  // scan + analyse as sme.vicky (helpers); open DealerFundingRequest in Validate
  const rule = page.getByTestId("rule-card").first();
  await expect(rule.getByText("Cites", { exact: true })).toHaveCount(0);
  const chip = rule.locator('[data-testid="cited-segment"] + * [data-testid="cite-chip"], [data-testid="cite-chip"]').first();
  await expect(chip).toHaveAttribute("data-phase", "validate");
  const step = await chip.getAttribute("data-step");
  await chip.click();
  await expect(page.locator(".react-flow__node.selected")).toHaveAttribute("data-id", step!);
});

test("the preview shows the memory revision and the not-verified reason", async ({ page }) => {
  // hover a chip: cite-preview visible with /r\d+/ or "Not in memory yet"; an unverified ref shows "Not verified:"
});

test("Discover shows the domain rationale with a chip per evidence item", async ({ page }) => {
  // choose Dealer Funding; expect no aria-label="Domain evidence" row; cite-chip count == evidence count from the API
});
```

- [ ] **Step 2: Run** `cd web && npx playwright test e2e/citations.spec.ts` (with `make e2e`'s env) — expect FAIL.
- [ ] **Step 3: Implement**; update `validate-design.spec.ts` line 45 to `firstRule.getByTestId("cite-chip").first()`.
- [ ] **Step 4: Run** `make e2e` — expect green.
- [ ] **Step 5: Commit** `feat(web): inline citations in Discover and Validate`.

---

### Task 10: Inline citations in Design, Build, Verify, Deploy and Readiness

**Files:**
- Modify: `web/components/tabs/design/{DesignTab,DecisionList}.tsx` (narrative and each decision's choice-and-reason rendered from `cited`; the Choice and Alternatives labels stay, Reason merges into the cited sentence), `web/components/tabs/build/ReviewPanel.tsx` (finding text from `cited`; the separate file:line column removed, the file chip opens the Build code viewer at the line), `web/components/tabs/verify/ParityTable.tsx` (difference row's note cell from `cited`; the separate finding-link column removed), `web/components/tabs/deploy/SecurityPanel.tsx` (the evidence `<pre>` replaced by a chip on the check sentence via `tail()` from `web/lib/citations.ts`, preview = the evidence text in mono), `web/app/readiness/page.tsx` (evidence list replaced by `row.cited`), `web/lib/citations.ts` (`tail(text, refs)` mirror), `web/lib/buildTypes.ts` (`cited?` fields)
- Delete: `web/components/evidence/EvidenceDrawer.tsx`

**Interfaces:**
- Consumes: Task 4 payloads; `useCiteNavigate` `file` target opens the Build tab's code viewer at `path` and `line` (extend the existing file-open state with a line if it has none).
- Produces `tail(text: string, refs: CiteRef[]): CitedText` in `citations.ts` (same rule as the Python `tail`).

- [ ] **Step 1: Write the Playwright tests** (append to `citations.spec.ts`, reusing the build-verify-deploy flow helpers)

```ts
test("a design decision's chip opens the rule it names", async ({ page }) => { /* chip data-phase="validate" → click → step-validate aria-selected */ });
test("a review finding's chip opens the file at its line", async ({ page }) => { /* data-target-kind="file" → click → code viewer shows the path */ });
test("a parity difference's chip opens its finding", async ({ page }) => { /* uses e2e/fixtures/parity.json defect */ });
test("Deploy and Readiness show no evidence blocks", async ({ page }) => {
  // Deploy: no <pre> inside security checks; each check with evidence has one cite-chip whose preview shows the evidence
  // Readiness: rows have cite-chips and no "no evidence yet" list items
});
```

- [ ] **Step 2: Run** them — expect FAIL.
- [ ] **Step 3: Implement**; `rg -n "EvidenceDrawer|aria-label=\"Domain evidence\"|>Cites<" web` — expect no output.
- [ ] **Step 4: Run** `make e2e` — expect green.
- [ ] **Step 5: Commit** `feat(web): inline citations in Design, Build, Verify, Deploy and Readiness`.

---

### Task 11: Responsive pass, visual suite, final verification

**Files:**
- Create: `web/e2e/visual.spec.ts` and its committed baselines `web/e2e/visual.spec.ts-snapshots/*`
- Modify: `web/playwright.config.ts` (`grepInvert: process.env.E2E_VISUAL === "1" ? undefined : /@visual/`; `expect.toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" }`), `Makefile` (`e2e-visual`: same as `e2e` with `E2E_VISUAL=1 npx playwright test e2e/visual.spec.ts`; `UPDATE=1` adds `--update-snapshots`), `docs/deploy/RUNBOOK.md` (run `make e2e-visual` before a release), `web/e2e/citations.spec.ts` (responsive test)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the responsive test** (functional suite)

```ts
test("no horizontal scroll at 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const step of ["upload", "discover", "validate", "design"]) {
    await gotoStep(page, step);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await page.evaluate(() => document.querySelector("main")!.scrollWidth <= document.querySelector("main")!.clientWidth)).toBe(true);
  }
});
```

- [ ] **Step 2: Write the visual spec**: `test.describe("@visual", ...)` over `[["light", 1440, 1000], ["dark", 1440, 1000], ["light", 390, 844], ["dark", 390, 844]]` × screens Upload (empty project), Discover (Dealer Funding), Validate (first rule card), Design (decisions), ExiQoian panel open with one answer, Help; `page.emulateMedia({ colorScheme })`, `mask: [page.locator("time, [data-testid=job-log], [data-mask]")]`, name `${screen}-${theme}-${width}.png`.
- [ ] **Step 3: Fix** layout until the responsive test passes; then `make e2e-visual UPDATE=1` — expect baselines written; `make e2e-visual` — expect all passed.
- [ ] **Step 4: Run** the full verification: `make test` — green; `.venv/bin/ruff check .` — clean; `cd web && npm run typecheck && npm run lint` — clean, `check-tokens: ok`; `make e2e` — green with the visual tests skipped; `make egress-audit` (after `make images` if stale) — `no unexpected hosts`.
- [ ] **Step 5: Commit** `test(web): responsive check and visual baselines; runbook step`.
