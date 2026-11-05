# Inline citations and the enterprise UI refresh

Date: 2026-10-25. Status: approved design, sub-project 4 of the roadmap in
`docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`. The user asked for
everything to be finished without further approval stops, so every open choice is recorded as a
ruling (`Ruling: ... — why — cost if wrong`).

Builds on sub-project 2 (Neo4j memory: uid conventions of its projectors, `Citation`, the
`/memory` explorer and `GET /memory/{project_id}/node`) and sub-project 3 (ExiQoian: the shared
`engine/citations/` model `CitedText`/`CiteRef`/`CiteSpan`/`CiteTarget` and the web components
`CitationChip`, `CitationPreview`, `CitedText` in `web/components/citations/`). Sub-project 3 is
implemented first.

## Purpose

The user's words: "let it show all citations of each phase instead of showing a separate box of
citations. and make UI bit smooth and open like enterprise/SaaS instead of compacted and less
UX friendly". The roadmap: "Citations shown inline at the claims they support in each phase
(replacing the separate evidence box), and a roomier, smoother SaaS-style layout across the
stepper and secondary screens."

Today, citations and evidence sit apart from the claims they support:

- Validate: each rule and finding card has a separate "Cites" row of activity chips under its
  text (`web/components/tabs/validate/CitationChip.tsx`).
- Discover: a domain's evidence is a row of chips above the graph, cut to three with "n more".
- Design: decisions and the narrative cite nothing, although they name activities, rules and
  tables.
- Build: review findings name a file and line in a separate column.
- Verify: a parity difference links its finding by id in a separate column.
- Deploy and Readiness: evidence is a `<pre>` block or a list under each check.
- `web/components/evidence/EvidenceDrawer.tsx` is no longer imported anywhere.

The look is dense: 77 files set arbitrary pixel font sizes (about 640 occurrences of 20
different sizes), 35 opaque `bg-white` surfaces, no dark mode, a full-width content column with
a 24 px gutter, and a stepper that shows seven status pills at once.

Success criteria:

1. Every phase screen shows its claims with inline citation chips at the sentence they support,
   built by the API (`cited` fields); no "Cites" row, evidence row, evidence `<pre>` block or
   citation list remains.
2. One chip component serves the phases and ExiQoian; hover or focus previews, click or Enter
   navigates to the cited artifact (canvas step, step, file, or `/memory?uid=`).
3. Every `cited` payload satisfies the `CitedText` invariants, and no citation an existing
   payload carries is lost (each one is on some span).
4. Design tokens (colour, type scale, spacing, radius, elevation, motion) are the only source of
   those values in components; a check fails the lint on arbitrary font sizes, opaque
   `bg-white`, raw hex colours in class names and on token pairs under WCAG AA 4.5:1 in either
   theme.
5. Light and dark themes (System by default, switchable), correct on every screen including the
   canvas, graphs, charts and code views.
6. Layout reads roomier: centred content up to 1600 px with 16/24/40 px gutters, 32 px between
   sections, 24 px card padding, one calm stepper; usable from 390 px wide without horizontal
   page scroll.
7. Existing Playwright specs pass with deliberately updated selectors; a visual suite captures
   key screens in both themes at 1440 and 390 px.
8. No outbound host and no new runtime dependency; `make egress-audit` passes.

## 1. The claims contract

Every cited claim travels as SP3's `CitedText` (`text`, `spans` with code-point offsets, `refs`
of `CiteRef` with a `CiteTarget`). The API adds a `cited` field next to the field it cites; the
old fields stay, so older clients and the agents keep working.

| Phase | Payload (schema) | `cited` built from | Refs | Hints (where the chip goes) |
| --- | --- | --- | --- | --- |
| Discover | `DomainOut.cited` | a rationale sentence per evidence kind ("Shares the tables FUNDING and DEAL.", "Calls DealerLookup.", "Named like Dealer*."), joined | one per evidence item, phase `discover`, kind `evidence`, target `step` discover | the evidence text inside its sentence |
| Validate | `RuleOut.cited`, `FindingOut.cited` | the rule or finding text | one per verified or unverified citation (activity), phase `validate`, kind `activity`, target `activity`; `memory_uid` `activity:<ir>/<id>`; `verified`/`reason` carried | activity id, activity name, words of the citation's quoted text |
| Design | `GET /processes/{id}/design`: `decisions[i].cited`, `narrative_cited` | `"<choice>. <reason>"` for a decision; the narrative | activities, rules and findings named in the text (entity linking, below), and the mapping rows' `rule_ids` for rows whose activity is named; phase `design` (rule refs phase `validate`) | the mention |
| Build | `GET /builds/{id}/reviews`: each finding's `cited` | the finding text | the file and line (`file` target, `path`, `line`), plus named activities, phase `build` | file name, class, method, else tail |
| Verify | `GET /builds/{id}/parity`: each field difference's `cited` | the difference sentence `"<path>: expected <e>, got <a>."` plus its note | the finding (`finding_id`) as a `validate` ref; the scenario run, phase `verify` | the path |
| Deploy | Security and plan checks (web-built, below) | the check sentence | one ref per evidence item, preview = the evidence text | tail |
| Readiness | `GET /projects/{id}/readiness`: each row's `cited` | the row's status sentence | one ref per evidence item, its existing `ref`/`kind` mapped to a target | tail |

### Builders (`engine/citations/claims.py`)

```python
class HintedRef(BaseModel):
    ref: CiteRef
    hints: list[str] = []          # strings whose first mention places the chip

def anchor(text: str, hinted: list[HintedRef]) -> CitedText: ...
def link_entities(text: str, entities: dict[str, CiteRef]) -> list[HintedRef]: ...
def tail(text: str, refs: list[CiteRef]) -> CitedText: ...
```

- `anchor`: split `text` into sentences (SP3 `sentence_bounds`); for each ref take the first
  sentence containing any hint (case-insensitive, word-bounded; identifiers also matched with
  `_` and camel-case parts as spaces); the span covers that sentence without its closing
  punctuation; refs on the same sentence merge in ref order; a ref whose hints match nowhere goes
  on the last sentence ("tail anchor"), so no citation is lost. Ref ids are renumbered `c1..cn`.
- `link_entities`: the deterministic mention linker for prose that carries no citations of its
  own (design narrative and decisions): the entity dictionary is the process's activity ids and
  names, rule keys (`R1`), finding keys (`S1`), table and queue names; each found entity becomes
  a `HintedRef` with itself as the hint. Unknown words are never linked.
- `tail`: one span over the last sentence carrying all refs.

Ruling: a chip sits at the end of the sentence that names what it cites, not on the mention
itself — the user asked for citations "at the claims", a claim is a sentence, and a chip inside a
sentence breaks reading — cost if wrong: a long sentence naming two activities shows both chips
at its end; the preview names each one.

Ruling: claims are anchored deterministically by mention, not by asking a model to place
citations — anchoring runs on every read, must be free, offline and stable, and the citations
themselves are already verified by the analysis — cost if wrong: a citation whose activity is
not named in the text lands at the end of the text (tail anchor), still visible.

Ruling: `cited` is computed on read in the API, not stored — the source fields stay the single
truth (an SME edit to a rule text re-anchors at once) and anchoring is linear in the text —
cost if wrong: a few milliseconds per card on large analyses.

Ruling: the Deploy security checks are assembled in the web today (`SecurityPanel.tsx`), so the
web builds their `CitedText` with a TypeScript `tail()` mirror (`web/lib/citations.ts`) — moving
the checks server-side is out of scope — cost if wrong: one small duplicate function, covered by
the same invariants in Playwright.

### Memory links (`engine/citations/uids.py`)

`activity_uid(ir_id, activity_id)`, `process_uid(ir_id)`, `rule_chunk_uid(ir_id, key)`,
`finding_chunk_uid(ir_id, key)`, `decision_chunk_uid(ir_id, title)` (SP2's slug rule),
`method_uid(ir_id, project, cls, method)` mirror SP2's projector conventions, and a test asserts
they equal what `engine/memory/projectors/*` produce for the same input. A `CiteRef` with a
`memory_uid` offers "Open in Memory"; its preview fetches `GET /memory/{p}/node?uid=` lazily on
first open (TanStack Query, `staleTime` 60 s) to show the current revision; 404 shows "Not in
memory yet", 503 shows nothing extra.

Ruling: memory links are optimistic (built from uid conventions, checked lazily) rather than
resolved for every chip on the server — a Validate page holds hundreds of citations and most
are never opened — cost if wrong: a chip may offer a node that is not ingested yet; the preview
says so.

## 2. The chip and preview (shared with ExiQoian)

SP3's components gain the phase visuals of the token set (section 3) and these rules:

- Chip: `Validate 2` (phase label and number by first appearance) in the phase tone, 20 px
  high, radius full, `text-2xs` semibold; unverified refs use the `error` tone and a dotted
  outline; `data-testid="cite-chip"`, `data-ref`, `data-phase`, `data-target-kind`,
  `data-step` (activity id or empty).
- Several refs on one span render as one chip group with a 4 px gap.
- Preview: elevation `e2`, radius `lg`, max width 360 px, opens after 150 ms hover or at once on
  focus, stays while the pointer is over chip or preview, closes on Escape or outside click; on
  touch, tap opens and a second tap (or "Open") navigates. Content: phase and kind eyebrow,
  label, preview text (`line-clamp-6`), revision `r<n>`, the not-verified reason, buttons
  "Open" and "Open in Memory".
- The cited segment gets a faint underline in the phase tone while its chip is hovered or
  focused (`data-testid="cited-segment"`), so the reader sees exactly which claim the chip
  supports.

## 3. Design tokens

Colours are CSS custom properties holding RGB channels (`--c-surface: 255 255 255`) in
`web/lib/theme.css`, under `:root` (light), `[data-theme="dark"]`, and
`@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ... } }`. Tailwind
maps each to `rgb(var(--c-x) / <alpha-value>)`, so `/40` opacity modifiers keep working.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `page` | `#FAF9F7` | `#0F0B14` | app background |
| `surface` | `#FFFFFF` | `#17121F` | cards, panels, popovers |
| `sunken` | `#F4F3EE` | `#1E1828` | wells, table headers, code background (`warm` alias) |
| `line` | `#E2DFD6` | `#2E2738` | borders |
| `line-strong` | `#CFCABB` | `#3D3548` | inputs, dividers that must read (`line-dk` alias) |
| `ink` | `#212529` | `#ECE8F2` | body text |
| `heading` | `#20063B` | `#F5F0FB` | titles (was `text-plum`) |
| `slate` | `#425466` | `#A9A3B5` | secondary text |
| `accent` | `#9B5DE5` | `#9B5DE5` | primary buttons, focus ring, selection |
| `accent-ink` | `#7E3FCB` | `#C9A8F5` | links and accent text (was `text-purple`) |
| `accent-soft` | `#F1E9FC` | `#2A1C40` | selected rows, hover (`purple-lt` alias) |
| `plum` | `#20063B` | `#20063B` | brand bar background only |
| `coral`, `coral-dk` | `#FF495C`, `#D32238` | `#FF495C`, `#FF7A88` | brand accents, eyebrows |
| `ai`, `ai-bg` | `#0E7A6E`, `#E1F3F0` | `#5FD3C2`, `#0F2E2A` | AI tone |
| `gate`, `gate-bg` | `#1E7B3C`, `#E2F3E6` | `#6FD08C`, `#12301C` | approved |
| `decision`, `decision-bg` | `#9A5E0A`, `#FBF0DF` | `#F0B860`, `#33240C` | needs a decision |
| `error`, `error-bg` | `#A33A32`, `#F9E5E3` | `#F28B82`, `#3A1714` | failure |
| `det`, `det-bg` | `#425466`, `#F4F3EE` | `#A9A3B5`, `#1E1828` | deterministic tone |
| `highlight`, `highlight-strong` | `#FFF2B8`, `#FFE27A` | `#4A3F12`, `#6B5A14` | XML line highlight |

Phase tones (chips, stepper accents): discover `coral-dk`, validate `ai`, design `accent-ink`,
build `det`, verify `gate`, deploy `decision`, memory `heading`, upload `slate`.

Type scale (replaces Tailwind's default `fontSize`, which no component uses today):

| Token | Size / line height | Replaces |
| --- | --- | --- |
| `text-2xs` | 11 / 16 | 9.5–11 px |
| `text-xs` | 12 / 18 | 11.5–12 px |
| `text-sm` | 13 / 20 | 12.5–13 px |
| `text-md` | 14 / 22 | 13.5–14 px |
| `text-base` | 15 / 24 | 14.5–15 px |
| `text-lg` | 17 / 26 | 16 px |
| `text-xl` | 19 / 28 | 18 px |
| `text-2xl` | 22 / 30 | 21–22 px |
| `text-3xl` | 26 / 34 | 24–26 px |
| `text-4xl` | 30 / 38 | 28 px and up |

The semantic aliases stay (`screen-title` 30, `panel-title` 22, `card-title` 17, `step` 14.5,
`eyebrow` 11, `body` 15).

Spacing and layout: Tailwind's 4 px scale, plus `--gutter` (16 px under 640, 24 px to 1279,
40 px from 1280), `--section-gap` 32 px, `--card-pad` 24 px (20 px under 640), content
`max-width` 1600 px centred. Radius: `btn` 8, `node` 10, `card` 14, `panel` 20, `full`.
Elevation: `e1` (resting card; the current `card`), `e2` (popovers, menus; the current `pop`),
`e3` (slide-overs and drawers: `0 24px 48px -12px rgb(16 6 32 / 0.28)`), each with a dark
variant using black at higher opacity and a 1 px `line` border. Motion: `--ease`
`cubic-bezier(0.2, 0, 0, 1)`, durations 120 ms (hover), 200 ms (panels), all under
`motion-reduce` set to 0.

Ruling: tokens as RGB-channel CSS variables rather than Tailwind's `darkMode: "class"` with
`dark:` variants on every class — one class per colour keeps the 12 000 lines of components
unchanged apart from renames, and non-Tailwind consumers (Cytoscape, React Flow, Monaco,
Recharts) read the same variables — cost if wrong: none found; Tailwind 3.4 supports the
`<alpha-value>` form.

Ruling: the codemod maps each arbitrary size to the nearest scale step (ties upward, floor
11 px) instead of bumping everything a step — the roomier feel comes from spacing, width and
line height, and a global size bump would re-wrap the seven-step stepper and the dense tables
the personas rely on — cost if wrong: a few labels at 9.5–10.5 px grow to 11 px.

Theme: a "Theme" radio group (System, Light, Dark) in the user menu writes
`localStorage["exiqo.theme"]` and `data-theme` on `<html>`; a small inline script in
`layout.tsx` applies it before first paint (no flash). Monaco switches between `vs` and
`vs-dark`; Cytoscape styles and Recharts colours are recomputed from the CSS variables on a
`themechange` event; React Flow edges use `rgb(var(--c-…))` directly.

Ruling: the brand bar stays plum in both themes — it carries the brand and already has white
text, and a bar that changes colour with the theme reads as a different product — cost if
wrong: none.

## 4. Layout

- Shell (`Chrome`): content in a centred column `max-w-[1600px]` with the gutter tokens; the
  top bar spans the window; below 1280 px the secondary links (Settings, History, Tokenomics,
  Readiness, Help & Support, Memory) collapse into a "More" menu; below 768 px the project
  selector and phase pill move into a second bar row.
- Page header: eyebrow, title, one-line description and actions on one row, wrapping under
  768 px. The pinned project header keeps its behaviour (`--pinned-h`) with 20 px vertical
  padding, and compacts (title to `text-2xl`, description hidden) after 48 px of scroll.
- Stepper (calmer): one row of seven steps; each step is its icon, its name and a 8 px status
  dot (tone per status), joined by a 1 px connector that fills in `gate` up to the last finished
  step; only the active step shows its status pill under its name; hover shows the status in a
  tooltip; keyboard arrows move between steps (`role="tablist"`, steps `role="tab"`). Under
  1024 px the stepper becomes a "Step 3 of 7 · Validate" button opening a list of the seven
  steps. `data-testid="step-<key>"` and the status attributes stay.
- Cards: `.card` = surface, `line` border, radius `card`, `e1`, padding `--card-pad`;
  `.card-header` (title, description, actions) with 16 px bottom margin; sections separated by
  `--section-gap`.
- Tables: `.table` class: header row in `sunken` with `text-xs` uppercase `slate` labels,
  rows 44 px min height, `line` separators, zebra off, hover `accent-soft/40`, numbers
  right-aligned and tabular; wide tables scroll inside their card (`overflow-x-auto`), never the
  page.
- Slide-overs and drawers (ExiQoian, gate audit, node drawer): `surface`, `e3`, radius `panel`
  on the leading corners, scrim only for modal ones.
- Empty states: icon, title, one sentence and the next action, centred in the card.
- Responsive breakpoints: Tailwind's `sm 640`, `md 768`, `lg 1024`, `xl 1280`, `2xl 1536`;
  every screen works at 390 × 844 with no horizontal page scroll (the canvas pans inside its
  card).

Ruling: the content column is capped at 1600 px instead of staying full-width — "wider content"
in the brief means more room inside the column, which the gutter and spacing tokens give, while
lines over 1600 px wide on a 34-inch monitor are harder to read — cost if wrong: on ultrawide
screens the canvas has less width; it already pans and zooms.

Ruling: the secondary screens (Settings, History, Tokenomics, Readiness, Help, Memory) get the
same shell, header, card and table classes, not bespoke layouts — consistency is what makes the
product read as one SaaS — cost if wrong: none.

## 5. Guardrails

`web/scripts/check-tokens.mjs`, run by `npm run lint` after ESLint, fails on:

1. an arbitrary font size class (`text-[<n>px]`) anywhere under `components/`, `app/`, `lib/`;
2. `bg-white` without an opacity modifier, and `shadow-2xl`/`shadow-xl`;
3. a hex colour (`#[0-9a-fA-F]{3,8}`) inside a `className` string or a `style` object, except in
   `components/ui/BrandIcons.tsx`;
4. `text-plum` and `text-purple` (renamed `text-heading`, `text-accent-ink`);
5. any listed token pair under 4.5:1 in either theme, read from `lib/theme.css`: `ink/page`,
   `ink/surface`, `heading/surface`, `slate/surface`, `slate/page`, `slate/sunken`,
   `accent-ink/surface`, `ai/ai-bg`, `gate/gate-bg`, `decision/decision-bg`, `error/error-bg`,
   `coral-dk/page`, `det/det-bg`.

Ruling: the guard is a small Node script, not a Stylelint or ESLint plugin dependency — it needs
no new package (no network at install) and its rules are a dozen regular expressions plus the
WCAG formula — cost if wrong: the script is maintained by hand.

## 6. Testing

- Python: `anchor`, `link_entities`, `tail` (sentence placement, merge, tail anchor, no lost
  ref, code-point offsets, word boundaries: `R1` does not match `R10`); the uid mirror against
  SP2's projectors; each phase payload's `cited` satisfies the invariants and carries one ref per
  source citation (Discover, Validate, Design, Build, Verify, Readiness).
- Playwright, functional (`make e2e`): `web/e2e/citations.spec.ts` (Validate: chips inside the
  rule text, the "Cites" row gone, chip click selects the activity on the canvas, preview shows
  "Not verified" for an unverified citation; Discover: the domain rationale with chips; Design: a
  decision's chip opens Validate or the canvas; Build: a review chip opens the file; Verify: a
  difference's chip opens its finding; theme switch persists over a reload; 390 px width has no
  horizontal scroll on Upload, Discover, Validate). Existing specs get deliberate selector
  updates (`citation-chip` → `cite-chip`; the stepper's status text moved to the active step and
  its tooltip).
- Playwright, visual (`make e2e-visual`, tag `@visual`, excluded from `make e2e`):
  `toHaveScreenshot` of Upload (empty), Discover, Validate (one rule card), Design, the
  ExiQoian panel and Help, in light and dark, at 1440 × 1000 and 390 × 844; animations
  disabled, timestamps, ids and the job log masked, `maxDiffPixelRatio: 0.01`; baselines
  committed per platform (Playwright's `{platform}` suffix).

Ruling: the visual suite is a separate target with committed baselines rather than part of
`make e2e` — font rasterisation differs between macOS and the Linux CI image, so pixel baselines
from one fail on the other, and functional e2e must stay green everywhere — cost if wrong: a
visual regression is caught only when `make e2e-visual` runs (before each release, per the
runbook).

## Out of scope

- Moving client-built checks (Deploy security) to the API.
- A component library or Storybook.
- Re-writing the canvas and graph renderers (they only take theme colours here).
