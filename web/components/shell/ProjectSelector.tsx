"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderPlus } from "lucide-react";
import { api } from "@/lib/api";
import { useProjects } from "@/lib/queries";
import { useAppState } from "@/lib/state";

export function ProjectSelector() {
  const { projectId, setProjectId, setStep } = useAppState();
  const projects = useProjects();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  // Open on real data: with no remembered project (or a stale one) pick the newest project.
  useEffect(() => {
    const list = projects.data;
    if (!list) return;
    if (list.length === 0) {
      if (projectId) setProjectId(null);
      return;
    }
    if (!projectId || !list.some((p) => p.id === projectId)) setProjectId(list[0].id);
  }, [projects.data, projectId, setProjectId]);

  useEffect(() => {
    if (!creating) return;
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setCreating(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [creating]);

  const create = useMutation({
    mutationFn: (n: string) => api.createProject(n),
    onSuccess: async (p) => {
      await qc.invalidateQueries({ queryKey: ["projects"] });
      setProjectId(p.id);
      setStep("upload");
      setCreating(false);
      setName("");
    },
  });

  return (
    <div className="relative flex items-center gap-2" ref={boxRef}>
      <label className="sr-only" htmlFor="project-select">
        Project
      </label>
      <select
        id="project-select"
        data-testid="project-select"
        className="max-w-[260px] rounded-btn border border-white/20 bg-white/10 px-3 py-1.5 text-[14px] text-white outline-none focus:border-lilac"
        value={projectId ?? ""}
        onChange={(e) => setProjectId(e.target.value || null)}
      >
        {(projects.data ?? []).length === 0 ? <option value="">No projects yet</option> : null}
        {(projects.data ?? []).map((p) => (
          <option key={p.id} value={p.id} className="text-ink">
            {p.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-btn border border-white/20 px-3 py-1.5 text-[14px] font-medium text-white hover:bg-white/10"
        onClick={() => setCreating((v) => !v)}
      >
        <FolderPlus size={16} aria-hidden />
        New project
      </button>
      {creating ? (
        <form
          className="card absolute left-0 top-11 z-50 w-[320px] p-4 shadow-lg"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate(name.trim());
          }}
        >
          <label htmlFor="new-project-name" className="label">
            Project name
          </label>
          <input
            id="new-project-name"
            autoFocus
            className="input mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="GMF credit estate"
          />
          {create.error ? <p className="mt-2 text-[13px] text-error">{(create.error as Error).message}</p> : null}
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn-secondary" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={!name.trim() || create.isPending}>
              Create
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
