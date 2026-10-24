"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, GitMerge, Loader2, Pencil, Sparkles, X } from "lucide-react";
import { api } from "@/lib/api";
import { subscribeJob } from "@/lib/sse";
import type { Domain } from "@/lib/types";
import { ErrorNote } from "@/components/ui/Empty";

export const ALL = "__all__";

export function DomainSelect({
  projectId,
  domains,
  value,
  onChange,
  editable,
}: {
  projectId: string;
  domains: Domain[];
  value: string;
  onChange: (id: string) => void;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [merging, setMerging] = useState(false);
  const [target, setTarget] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = domains.find((d) => d.id === value) ?? null;
  const total = domains.reduce((n, d) => n + d.process_ids.length, 0);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const refresh = () =>
    Promise.all(
      ["domains", "processes", "graph"].map((k) => qc.invalidateQueries({ queryKey: [k, projectId] })),
    );

  const rename = useMutation({
    mutationFn: (name: string) => api.renameDomain(value, name),
    onSuccess: async () => {
      await refresh();
      setRenaming(false);
    },
  });
  const [naming, setNaming] = useState<"idle" | "running" | "done" | "failed">("idle");
  const [namingNote, setNamingNote] = useState<string | null>(null);
  const namingSub = useRef<(() => void) | null>(null);
  useEffect(() => () => namingSub.current?.(), []);
  const suggest = async () => {
    setNaming("running");
    setNamingNote(null);
    try {
      const { job_id } = await api.nameDomains(projectId);
      namingSub.current?.();
      namingSub.current = subscribeJob(job_id, (ev) => {
        if (ev.event === "done") {
          setNaming("done");
          const r = ev.data ?? {};
          setNamingNote(r.ai_available === false ? "No model configured: names stay as grouped by code." : null);
          void refresh();
        } else if (ev.event === "error") {
          setNaming("failed");
          setNamingNote(ev.text);
        }
      });
    } catch (e) {
      setNaming("failed");
      setNamingNote((e as Error).message);
    }
  };
  const merge = useMutation({
    mutationFn: (into: string) => api.mergeDomain(value, into),
    onSuccess: async (d) => {
      await refresh();
      setMerging(false);
      onChange(d.id);
    },
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {renaming && current ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) rename.mutate(draft.trim());
            }}
          >
            <label htmlFor="domain-name" className="sr-only">
              Domain name
            </label>
            <input id="domain-name" autoFocus className="input w-[300px]" value={draft} onChange={(e) => setDraft(e.target.value)} />
            <button type="submit" className="btn-primary" disabled={!draft.trim() || rename.isPending}>
              <Check size={16} aria-hidden /> Save
            </button>
            <button type="button" className="btn-secondary" onClick={() => setRenaming(false)}>
              Cancel
            </button>
          </form>
        ) : (
          <div className="relative" ref={ref}>
            <button
              type="button"
              data-testid="domain-select"
              aria-haspopup="listbox"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="flex min-w-[320px] items-center justify-between gap-3 rounded-btn border border-line bg-white px-4 py-2 text-left hover:border-purple"
            >
              <span>
                <span className="block text-[16px] font-semibold text-plum">{current ? current.name : "All domains"}</span>
                <span className="block text-[12.5px] text-slate">
                  {current ? `${current.process_ids.length} processes` : `${domains.length} domains, ${total} processes`}
                </span>
              </span>
              <ChevronDown size={18} className="text-slate" aria-hidden />
            </button>
            {open ? (
              <ul role="listbox" aria-label="Domains" className="card absolute left-0 top-[60px] z-40 w-[380px] py-1 shadow-lg">
                {[{ id: ALL, name: "All domains", count: total, suggested: "" }, ...domains.map((d) => ({
                  id: d.id,
                  name: d.name,
                  count: d.process_ids.length,
                  suggested: d.renamed_by && d.suggested_name !== d.name ? d.suggested_name : "",
                }))].map((d) => (
                  <li
                    key={d.id}
                    role="option"
                    aria-selected={d.id === value}
                    tabIndex={0}
                    onClick={() => {
                      onChange(d.id);
                      setOpen(false);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onChange(d.id);
                        setOpen(false);
                      }
                    }}
                    className={`flex cursor-pointer items-center justify-between px-4 py-2 hover:bg-warm ${
                      d.id === value ? "bg-purple-lt" : ""
                    }`}
                  >
                    <span>
                      <span className={`block text-[14.5px] ${d.id === value ? "font-semibold text-purple" : "text-ink"}`}>
                        {d.name}
                      </span>
                      {d.suggested ? <span className="block text-[12px] text-slate">suggested: {d.suggested}</span> : null}
                    </span>
                    <span className="rounded-full bg-warm px-2 text-[12px] text-slate">{d.count}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        )}
        {current && !renaming ? (
          <>
            <button
              type="button"
              className="btn-secondary"
              disabled={!editable}
              title={editable ? "Rename this domain" : "Renaming needs the domains API"}
              onClick={() => {
                setDraft(current.name);
                setRenaming(true);
              }}
            >
              <Pencil size={15} aria-hidden /> Rename
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!editable || domains.length < 2}
              title={editable ? "Merge into another domain" : "Merging needs the domains API"}
              onClick={() => {
                setTarget(domains.find((d) => d.id !== current.id)?.id ?? "");
                setMerging(true);
              }}
            >
              <GitMerge size={15} aria-hidden /> Merge
            </button>
          </>
        ) : null}
        {editable && !renaming ? (
          <button
            type="button"
            className="btn border border-ai/30 bg-ai-bg text-ai hover:bg-ai-bg/70"
            disabled={naming === "running"}
            onClick={suggest}
            title="The model proposes a business name per domain from its evidence; names set by an SME are kept"
          >
            {naming === "running" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Sparkles size={15} aria-hidden />}
            Suggest names
          </button>
        ) : null}
        {current?.renamed_by ? <span className="text-[12.5px] text-slate">Renamed by {current.renamed_by}</span> : null}
        {namingNote ? <span className={`text-[12.5px] ${naming === "failed" ? "text-error" : "text-slate"}`}>{namingNote}</span> : null}
      </div>
      {rename.error ? <ErrorNote error={rename.error} /> : null}
      {merging && current ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-plum/25" role="presentation">
          <div role="dialog" aria-modal="true" aria-label="Merge domain" className="card w-[440px] p-6 shadow-2xl">
            <div className="flex items-start justify-between">
              <div>
                <p className="eyebrow">Merge domain</p>
                <h3 className="panel-title mt-1">{current.name}</h3>
              </div>
              <button type="button" className="btn-ghost" aria-label="Close" onClick={() => setMerging(false)}>
                <X size={18} />
              </button>
            </div>
            <label htmlFor="merge-target" className="label mt-4 block">
              Merge into
            </label>
            <select id="merge-target" className="input select mt-1" value={target} onChange={(e) => setTarget(e.target.value)}>
              {domains
                .filter((d) => d.id !== current.id)
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.process_ids.length})
                  </option>
                ))}
            </select>
            <p className="mt-3 text-[14px] text-slate">
              The {current.process_ids.length} processes of {current.name} move to the chosen domain and {current.name} is
              removed. The decision is recorded.
            </p>
            {merge.error ? (
              <div className="mt-3">
                <ErrorNote error={merge.error} />
              </div>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setMerging(false)}>
                Cancel
              </button>
              <button type="button" className="btn-primary" disabled={!target || merge.isPending} onClick={() => merge.mutate(target)}>
                Merge
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
