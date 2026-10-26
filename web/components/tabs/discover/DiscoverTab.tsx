"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Info } from "lucide-react";
import { useDomainList } from "@/lib/domains";
import { useCanvas, useGraph, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { STEP_LABELS } from "@/lib/steps";
import { BandPill, Pill } from "@/components/ui/Pill";
import { Empty } from "@/components/ui/Empty";
import { CitedText } from "@/components/citations/CitedText";
import { ProcessCanvas } from "@/components/canvas/ProcessCanvas";
import { Inspector } from "@/components/canvas/Inspector";
import { ALL, DomainSelect } from "./DomainSelect";
import { graphElements, type GraphFilters } from "./DomainGraph";
import { IOCard } from "./IOCard";

const DomainGraph = dynamic(() => import("./DomainGraph"), { ssr: false });

/** Display label for a stored migration_status (the stored value `analysed` stays as the API sends it). */
function statusLabel(s: string): string {
  return s === "analysed" ? "analyzed" : s.replace(/_/g, " ");
}

function ViewToggle() {
  return (
    <div className="inline-flex rounded-btn border border-line bg-surface p-0.5 text-md" role="group" aria-label="View">
      <button type="button" aria-pressed="true" className="rounded-[5px] bg-heading px-3 py-1 font-medium text-page">
        TIBCO view
      </button>
      <span title="Available after code generation">
        <button type="button" disabled aria-pressed="false" className="cursor-not-allowed rounded-[5px] px-3 py-1 text-slate/60">
          Target view
        </button>
      </span>
    </div>
  );
}

export function DiscoverTab() {
  const {
    projectId,
    processId,
    domainId,
    openProcess,
    setDomainId,
    nodeId,
    selectNode: setNodeId,
    inspectorTab,
    setInspectorTab,
    returnTo,
    setStep,
  } = useAppState();
  const processView = useRef<HTMLDivElement>(null);
  const processes = useProcesses(projectId);
  const graph = useGraph(projectId);
  const { domains, editable } = useDomainList(projectId);
  const [filters, setFilters] = useState<GraphFilters>({ band: "all", status: "all" });
  const canvas = useCanvas(processId);

  const all = useMemo(() => processes.data ?? [], [processes.data]);
  const domainValue = domainId && (domainId === ALL || domains.some((d) => d.id === domainId)) ? domainId : null;

  // Default domain: the open process's, else the first one.
  useEffect(() => {
    if (domainValue || domains.length === 0) return;
    const fromProcess = all.find((p) => p.id === processId)?.domain_id;
    setDomainId(fromProcess && domains.some((d) => d.id === fromProcess) ? fromProcess : domains[0].id);
  }, [domainValue, domains, all, processId, setDomainId]);

  // Arriving from a citation (Validate or Ask): bring the canvas and the XML into view.
  useEffect(() => {
    if (returnTo && nodeId) processView.current?.scrollIntoView({ block: "start" });
  }, [returnTo, nodeId]);

  const current = domains.find((d) => d.id === domainValue) ?? null;
  const inDomain = useMemo(
    () => (current ? all.filter((p) => current.process_ids.includes(p.id) || p.domain_id === current.id) : all),
    [all, current],
  );
  const elements = useMemo(() => graphElements(graph.data ?? null, inDomain, all, filters), [graph.data, inDomain, all, filters]);
  const statuses = useMemo(() => [...new Set(all.map((p) => p.migration_status))].sort(), [all]);
  const proc = all.find((p) => p.id === processId) ?? null;
  const node = canvas.data?.nodes.find((n) => n.id === nodeId) ?? null;

  if (processes.data && processes.data.length === 0) {
    return <Empty title="Nothing discovered yet">Upload and scan an estate first.</Empty>;
  }

  return (
    <div className="space-y-5">
      <div className="sticky top-[var(--pinned-h,0px)] z-10 -mx-[var(--gutter)] bg-page px-[var(--gutter)] py-3 print:static print:m-0 print:p-0 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 2</p>
          <h2 className="panel-title">Discover</h2>
        </div>
        <p className="flex items-center gap-1.5 text-sm text-slate">
          <Info size={14} aria-hidden /> Domains are grouped by code from modules, shared tables and queues, calls and
          names.
        </p>
      </div>

      {projectId ? (
        <DomainSelect
          projectId={projectId}
          domains={domains}
          value={domainValue ?? ALL}
          onChange={(id) => setDomainId(id)}
          editable={editable}
        />
      ) : null}
      {current?.cited?.text ? (
        <p className="max-w-[80ch] text-md leading-relaxed text-ink" data-testid="domain-rationale">
          <CitedText value={current.cited} />
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <section className="card flex p-0 h-[440px] flex-col overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
            <h3 className="text-lg font-semibold text-heading">Domain graph</h3>
            <label className="ml-auto flex items-center gap-1.5 text-sm text-slate">
              Complexity
              <select
                className="select py-1 text-sm"
                value={filters.band}
                onChange={(e) => setFilters((f) => ({ ...f, band: e.target.value }))}
              >
                {["all", "low", "medium", "high"].map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-sm text-slate">
              Status
              <select
                className="select py-1 text-sm"
                value={filters.status}
                onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
              >
                {["all", ...statuses].map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="relative min-h-0 flex-1 bg-page">
            {graph.data === null ? (
              <p className="p-4 text-md text-slate">The estate graph is not available from this API build.</p>
            ) : (
              <DomainGraph elements={elements} selectedProcessId={processId} onOpen={(id) => openProcess(id)} />
            )}
            <div className="pointer-events-none absolute bottom-2 left-3 flex gap-3 text-2xs text-slate">
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-4 rounded bg-accent-strong" /> process
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-4 rounded border border-accent bg-accent-soft" /> other domain
              </span>
              <span className="flex items-center gap-1">
                <span className="h-3 w-2.5 rounded-sm border border-slate/40 bg-sunken" /> table
              </span>
              <span>queue, topic, endpoint: small gray</span>
            </div>
          </div>
        </section>
        <section className="card flex p-0 h-[440px] flex-col overflow-hidden">
          <div className="border-b border-line px-4 py-2.5">
            <h3 className="text-lg font-semibold text-heading">Processes</h3>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto p-2" data-testid="process-list">
            {inDomain.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => openProcess(p.id)}
                  aria-current={p.id === processId ? "true" : undefined}
                  className={`w-full rounded-btn px-3 py-2 text-left ${p.id === processId ? "bg-accent-soft" : "hover:bg-sunken"}`}
                >
                  <span className={`block truncate text-base font-semibold ${p.id === processId ? "text-accent-ink" : "text-heading"}`}>
                    {p.name}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-xs text-slate">
                    <Pill tone="det">{p.starter_kind ?? "no starter"}</Pill>
                    <BandPill band={p.complexity_band} />
                    <span>{p.activity_count} steps</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {proc ? (
        <div ref={processView} className="scroll-mt-4 space-y-4" data-testid="process-view">
          {returnTo ? (
            <button type="button" className="btn-secondary" onClick={() => setStep(returnTo)}>
              <ArrowLeft size={16} aria-hidden /> Back to {STEP_LABELS[returnTo]}
            </button>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0">
              <p className="label">{proc.application} · {proc.module} · {proc.dialect}</p>
              <h3 className="truncate text-2xl font-semibold text-heading">{proc.name}</h3>
            </div>
            <Pill tone="det">{proc.starter_kind ?? "no starter"}</Pill>
            <BandPill band={proc.complexity_band} />
            <span className="font-mono text-xs text-slate" title="Spec hash">
              {proc.spec_hash.slice(0, 12)}
            </span>
            <div className="ml-auto">
              <ViewToggle />
            </div>
          </div>
          <ProcessCanvas canvas={canvas.data} loading={canvas.isLoading} selected={nodeId} onSelect={setNodeId} />
          <Inspector processId={proc.id} node={node} tab={inspectorTab} onTab={setInspectorTab} />
          <IOCard processId={proc.id} />
        </div>
      ) : (
        <Empty title="Select a process">Pick one from the list or the graph to open its canvas and I/O card.</Empty>
      )}
    </div>
  );
}
