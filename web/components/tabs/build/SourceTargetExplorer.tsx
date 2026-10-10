"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Crosshair, Folder, Network, FolderTree } from "lucide-react";
import { ProcessCanvas } from "@/components/canvas/ProcessCanvas";
import { FileTree } from "@/components/tabs/upload/FileTree";
import { ErrorNote } from "@/components/ui/Empty";
import { useBuildFiles } from "@/lib/buildQueries";
import { useCanvas, useDesign, useFiles, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import type { BuildFileEntry, TibcoRef } from "@/lib/buildTypes";
import type { FileEntry, ProcessSummary } from "@/lib/types";
import { CodeViewer } from "./CodeViewer";
import { TibcoSourcePane } from "./TibcoSourcePane";

interface Dir {
  name: string;
  path: string;
  dirs: Dir[];
  files: BuildFileEntry[];
}

function tree(files: BuildFileEntry[]): Dir {
  const root: Dir = { name: "", path: "", dirs: [], files: [] };
  for (const f of files) {
    let cur = root;
    const parts = f.path.split("/");
    for (const part of parts.slice(0, -1)) {
      let next = cur.dirs.find((d) => d.name === part);
      if (!next) {
        next = { name: part, path: cur.path ? `${cur.path}/${part}` : part, dirs: [], files: [] };
        cur.dirs.push(next);
      }
      cur = next;
    }
    cur.files.push(f);
  }
  const sort = (d: Dir) => {
    d.dirs.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.path.localeCompare(b.path));
    d.dirs.forEach(sort);
  };
  sort(root);
  return root;
}

const DOT: Record<string, string> = { code: "bg-det", ai: "bg-ai", agent: "bg-decision", infra: "bg-purple" };

function TargetDir({
  dir,
  selected,
  onSelect,
  mine,
}: {
  dir: Dir;
  selected: string | null;
  onSelect: (p: string) => void;
  mine: Set<string>;
}) {
  const startOpen = /^(src|tests)(\/|$)/.test(dir.path) || (!!selected && selected.startsWith(`${dir.path}/`));
  return (
    <details open={startOpen}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-0.5 text-[13px] hover:bg-warm">
        <ChevronRight size={13} className="shrink-0 text-slate transition-transform [details[open]>summary>&]:rotate-90" aria-hidden />
        <Folder size={13} className="shrink-0 text-purple" aria-hidden />
        <span className="truncate font-medium text-plum">{dir.name}</span>
      </summary>
      <div className="ml-3 border-l border-line pl-1.5">
        <Entries dir={dir} selected={selected} onSelect={onSelect} mine={mine} />
      </div>
    </details>
  );
}

function Entries({ dir, selected, onSelect, mine }: { dir: Dir; selected: string | null; onSelect: (p: string) => void; mine: Set<string> }) {
  return (
    <>
      {dir.dirs.map((d) => (
        <TargetDir key={d.path} dir={d} selected={selected} onSelect={onSelect} mine={mine} />
      ))}
      {dir.files.map((f) => {
        const name = f.path.split("/").pop() ?? f.path;
        const active = f.path === selected;
        return (
          <button
            key={f.path}
            type="button"
            onClick={() => onSelect(f.path)}
            title={`${f.path} (${f.origin})`}
            aria-current={active ? "true" : undefined}
            className={`flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[12.5px] ${
              active ? "bg-purple-lt font-semibold text-purple-dk" : mine.has(f.path) ? "text-ink hover:bg-warm" : "text-slate hover:bg-warm"
            }`}
          >
            <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[f.origin] ?? "bg-line"}`} />
            <span className="truncate">{name}</span>
          </button>
        );
      })}
    </>
  );
}

/** The file that implements a step: the design mapping's file first, then production code, then tests. */
function fileForActivity(
  files: BuildFileEntry[],
  irId: string,
  activityId: string,
  mapped: string | null,
): BuildFileEntry | null {
  const hits = files.filter((f) => f.tibco_ref?.process_id === irId && f.tibco_ref.activity_ids.includes(activityId));
  return (
    (mapped ? hits.find((f) => f.path === mapped || f.path.endsWith(`/${mapped}`)) : undefined) ??
    hits.find((f) => f.path.startsWith("src/") && !f.path.includes("/Mappings/")) ??
    hits.find((f) => f.path.startsWith("src/")) ??
    hits[0] ??
    null
  );
}

const ORIGINS = ["code", "ai", "agent", "infra"] as const;
const PANE_HEIGHT = 520;

/**
 * The generated file a TIBCO process opens beside its source: production code that implements one
 * of its steps (service, then repository, then anything outside the mappings), else its service
 * class, else any production file of it.
 */
function fileForProcess(files: BuildFileEntry[], irId: string): BuildFileEntry | null {
  const hits = files.filter((f) => f.tibco_ref?.process_id === irId && f.path.startsWith("src/"));
  const steps = hits.filter((f) => (f.tibco_ref?.rows.length ?? 0) > 0);
  return (
    steps.find((f) => f.path.includes("/Services/")) ??
    steps.find((f) => f.path.includes("/Repositories/")) ??
    steps.find((f) => !f.path.includes("/Mappings/")) ??
    steps[0] ??
    hits.find((f) => f.path.includes("/Services/")) ??
    hits[0] ??
    null
  );
}

/** The TIBCO steps a generated file implements; a chip highlights that step in the source pane. */
function StepChips({
  entry,
  activeId,
  onPick,
}: {
  entry: BuildFileEntry | null;
  activeId: string | null;
  onPick: (activityId: string) => void;
}) {
  const ref: TibcoRef | null = entry?.tibco_ref ?? null;
  return (
    <div data-testid="tibco-ref" className="rounded-btn border border-line bg-page px-3 py-2 text-[12.5px]">
      {!entry ? (
        <p className="text-slate">Select a generated file to see the TIBCO steps it implements.</p>
      ) : ref ? (
        <>
          <p>
            <span className="label mr-1.5">Implements, from</span>
            <span className="font-mono">{ref.source_path}</span>
            <span className="ml-1.5 text-slate">(select a step to highlight it in the TIBCO source)</span>
          </p>
          <ul className="mt-1 flex min-w-0 flex-wrap gap-1.5">
            {ref.rows.map((r) => {
              const line = ref.source_lines[r.activity_id];
              const active = r.activity_id === activeId;
              return (
                <li key={`${r.activity_id}:${r.method}`}>
                  <button
                    type="button"
                    data-testid="step-chip"
                    aria-pressed={active}
                    disabled={!line}
                    title={line ? `Highlight line ${line} of the TIBCO source` : "The process has no source line for this step"}
                    onClick={() => onPick(r.activity_id)}
                    className={`inline-flex max-w-full flex-wrap items-center gap-x-1 rounded-full border px-2 py-0.5 text-left ${
                      active ? "border-purple bg-purple-lt text-purple-dk" : "border-line bg-white hover:border-purple hover:text-purple"
                    }`}
                  >
                    <Crosshair size={11} aria-hidden />
                    <span className="font-medium">{r.activity_name}</span>
                    <span className="text-slate">
                      {r.class_name}.{r.method}
                    </span>
                    {line ? <span className="text-slate">line {line}</span> : null}
                    {r.rule_ids.length ? <span className="text-gate">{r.rule_ids.join(", ")}</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p className="text-slate">
          No TIBCO step: {entry.origin === "infra" ? "infrastructure for the whole solution" : "shared by every workflow in the solution"}.
        </p>
      )}
    </div>
  );
}

/**
 * Source and target side by side. Above: the TIBCO estate (its files, or the process canvas) and
 * the generated solution. Below: the TIBCO process XML on the left, the selected generated file on
 * the right, at the same height. Selecting a generated file opens the TIBCO source it came from at
 * the step it implements; a TIBCO process file opens beside the service generated from it; a step
 * on the canvas opens the file that implements it.
 */
export function SourceTargetExplorer({ buildId, process }: { buildId: string; process: ProcessSummary }) {
  const { projectId, nodeId, selectNode } = useAppState();
  const design = useDesign(process.id);
  const sources = useFiles(projectId);
  const processes = useProcesses(projectId);
  const canvas = useCanvas(process.id);
  const files = useBuildFiles(buildId);
  const [left, setLeft] = useState<"files" | "canvas">("files");
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [note, setNote] = useState<string | null>(null);
  // The step to highlight in the TIBCO source, and a TIBCO file opened on its own (no generated file of it).
  const [activity, setActivity] = useState<string | null>(null);
  const [manualSource, setManualSource] = useState<string | null>(null);

  const list = useMemo(() => files.data?.files ?? [], [files.data]);
  const procs = useMemo(() => processes.data ?? [], [processes.data]);
  const procByPath = useMemo(() => new Map(procs.map((p) => [p.source_path, p])), [procs]);
  const mine = useMemo(
    () => new Set(list.filter((f) => f.tibco_ref?.process_id === process.ir_id).map((f) => f.path)),
    [list, process.ir_id],
  );
  const shown = useMemo(() => list.filter((f) => !hidden.has(f.origin)), [list, hidden]);
  const root = useMemo(() => tree(shown), [shown]);

  const openGenerated = (path: string) => {
    setSelected(path);
    setActivity(null);
    setManualSource(null);
    setNote(null);
  };

  // Open on the production file that implements a step of this process once the files arrive.
  useEffect(() => {
    if (selected || list.length === 0) return;
    const first = fileForProcess(list, process.ir_id) ?? list.find((f) => mine.has(f.path)) ?? null;
    if (first) setSelected(first.path);
  }, [list, mine, selected, process.ir_id]);

  // A step chosen on the canvas (here or in Discover) opens the file that implements it, at that step.
  useEffect(() => {
    if (!nodeId || list.length === 0) return;
    const row = design.data?.record.mapping.find((m) => m.activity_id === nodeId);
    const hit = fileForActivity(list, process.ir_id, nodeId, row ? `src/${row.project}/${row.file}` : null);
    if (hit) {
      setSelected(hit.path);
      setActivity(nodeId);
      setManualSource(null);
      setNote(null);
    } else {
      setNote(`No generated file names step ${nodeId}: it maps to shared code or has no mapping row.`);
    }
  }, [nodeId, list, process.ir_id, design.data]);

  const openSource = (f: FileEntry) => {
    const p = procByPath.get(f.path);
    if (!p) return;
    const gen = fileForProcess(list, p.ir_id);
    if (gen) openGenerated(gen.path);
    else {
      setManualSource(p.id);
      setNote(`No generated file names ${p.name} in this build; its TIBCO source is shown alone.`);
    }
  };

  const entry = list.find((f) => f.path === selected) ?? null;
  const ref = entry?.tibco_ref ?? null;
  const refProc = ref ? (procs.find((p) => p.ir_id === ref.process_id) ?? null) : null;
  const sourceProc = manualSource ? (procs.find((p) => p.id === manualSource) ?? null) : refProc;
  const activeId =
    ref && sourceProc && sourceProc === refProc
      ? activity && ref.activity_ids.includes(activity) && ref.source_lines[activity]
        ? activity
        : (ref.rows.find((r) => ref.source_lines[r.activity_id])?.activity_id ?? null)
      : null;
  const line = ref && activeId ? (ref.source_lines[activeId] ?? null) : null;
  const activeName = ref?.rows.find((r) => r.activity_id === activeId)?.activity_name ?? null;
  // The canvas of this process carries each step's SQL; with it the highlight lands on the statement.
  const activeConfig = sourceProc?.id === process.id ? canvas.data?.nodes.find((n) => n.id === activeId)?.data.config : undefined;
  const activeSql = typeof activeConfig?.sql === "string" ? activeConfig.sql : null;
  const counts = files.data?.counts ?? {};

  return (
    <section className="card p-5" data-testid="source-target-explorer">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label">Source and target</p>
          <h3 className="text-[18px] font-semibold text-plum">TIBCO estate and the generated solution</h3>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]" role="group" aria-label="Filter generated files by origin">
          {ORIGINS.map((o) => (
            <button
              key={o}
              type="button"
              aria-pressed={!hidden.has(o)}
              onClick={() =>
                setHidden((h) => {
                  const n = new Set(h);
                  if (n.has(o)) n.delete(o);
                  else n.add(o);
                  return n;
                })
              }
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 ${
                hidden.has(o) ? "border-line bg-white text-slate line-through" : "border-purple-lt bg-purple-lt text-plum"
              }`}
            >
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${DOT[o]}`} />
              {o} {counts[o] ?? 0}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 grid items-start gap-4 xl:grid-cols-2">
        <div className="min-w-0" data-testid="source-tree">
          <div className="mb-2 flex gap-1 rounded-btn bg-warm p-1 text-[13px]" role="tablist" aria-label="TIBCO view">
            {(
              [
                ["files", "TIBCO files", FolderTree],
                ["canvas", "Process canvas", Network],
              ] as const
            ).map(([k, label, Icon]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={left === k}
                onClick={() => setLeft(k)}
                className={`flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 ${
                  left === k ? "bg-white font-medium text-plum shadow-sm" : "text-slate"
                }`}
              >
                <Icon size={14} aria-hidden /> {label}
              </button>
            ))}
          </div>
          {left === "files" ? (
            sources.data ? (
              <>
                <FileTree
                  files={sources.data}
                  onOpen={openSource}
                  canOpen={(f) => procByPath.has(f.path)}
                  selected={sourceProc?.source_path ?? null}
                />
                <p className="mt-1 text-[12px] text-slate">
                  Select a process file to open its source below, beside the service generated from it. Other files have no
                  content view here.
                </p>
              </>
            ) : sources.error ? (
              <ErrorNote error={sources.error} />
            ) : null
          ) : canvas.data ? (
            <div>
              <ProcessCanvas canvas={canvas.data} loading={canvas.isLoading} selected={nodeId} onSelect={selectNode} />
              <p className="mt-1 text-[12px] text-slate">Select a step to open the generated file that implements it.</p>
            </div>
          ) : null}
        </div>

        <div className="min-w-0 rounded-btn border border-line bg-white p-2" data-testid="target-tree">
          <p className="px-1 pb-1 text-[12px] font-semibold uppercase tracking-wide text-slate">Generated solution</p>
          {files.error ? <ErrorNote error={files.error} /> : null}
          <div className="max-h-[480px] overflow-y-auto pr-1">
            <Entries dir={root} selected={selected} onSelect={openGenerated} mine={mine} />
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {note ? (
          <p role="status" className="rounded-btn bg-decision-bg px-3 py-1.5 text-[12.5px] text-decision">
            {note}
          </p>
        ) : null}
        <StepChips
          entry={entry}
          activeId={activeId}
          onPick={(id) => {
            setActivity(id);
            setManualSource(null);
          }}
        />
      </div>

      <div className="mt-3 grid items-start gap-4 xl:grid-cols-2" data-testid="side-by-side">
        <TibcoSourcePane proc={sourceProc} line={line} activityName={activeName} sql={activeSql} height={PANE_HEIGHT} />
        <CodeViewer buildId={buildId} entry={entry} showRef={false} height={PANE_HEIGHT} />
      </div>
    </section>
  );
}
