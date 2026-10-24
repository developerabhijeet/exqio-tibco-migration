"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FolderPlus } from "lucide-react";
import { api } from "@/lib/api";
import { useProjects } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import type { Project } from "@/lib/types";

export function ProjectSelector() {
  const { projectId, setProjectId, setStep } = useAppState();
  const projects = useProjects();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  // Open on real data: with no remembered project (or a stale one) pick the newest project.
  //
  // Only when the list is settled. Creating a project selects it and invalidates this query, so for
  // the moment between those two the list in hand is the one from before it existed. Judged against
  // that list the new project looks stale, and this would switch away from it to whatever is newest
  // among the old ones: the upload then goes to the project just created and the scan to another, on
  // a screen that names the second. With no other project it self-corrects on the refetch, which is
  // why it only ever showed up on an estate that already had one.
  useEffect(() => {
    const list = projects.data;
    if (!list || projects.isFetching) return;
    if (list.length === 0) {
      if (projectId) setProjectId(null);
      return;
    }
    if (!projectId || !list.some((p) => p.id === projectId)) setProjectId(list[0].id);
  }, [projects.data, projects.isFetching, projectId, setProjectId]);

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
      // The new project goes into the cached list before it is selected, so there is never a render
      // where the selected id is absent from the list beside it. Without that, the effect above sees
      // the old list for one render, reads the new project as stale and switches to whichever other
      // project is newest: the upload then lands on the project that was created and the scan on the
      // one the screen switched to, which answers 404 because it has no upload.
      qc.setQueryData(["projects"], (old: Project[] | undefined) => [p, ...(old ?? [])]);
      setProjectId(p.id);
      setStep("upload");
      setCreating(false);
      setName("");
      await qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <div className="relative flex items-center gap-2" ref={boxRef}>
      <label className="sr-only" htmlFor="project-select">
        Project
      </label>
      {/* A real select, so the keyboard and the tests keep working; the native chrome is off because
          the platform draws it on a light background that reads as broken on the plum bar. The
          options themselves stay on white, which is where the browser draws them. */}
      <div className="relative">
        <select
          id="project-select"
          data-testid="project-select"
          className="w-[220px] max-w-[260px] cursor-pointer appearance-none truncate rounded-btn border border-white/20 bg-white/10 py-1.5 pl-3 pr-9 text-[13.5px] font-medium text-white outline-none transition-colors hover:bg-white/15 focus:border-lilac"
          value={projectId ?? ""}
          onChange={(e) => setProjectId(e.target.value || null)}
        >
          {(projects.data ?? []).length === 0 ? (
            <option value="" className="bg-white text-ink">
              No projects yet
            </option>
          ) : null}
          {(projects.data ?? []).map((p) => (
            <option key={p.id} value={p.id} className="bg-white text-ink">
              {p.name}
            </option>
          ))}
        </select>
        <ChevronDown
          size={15}
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-lilac"
        />
      </div>
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-btn border border-white/20 px-3 py-1.5 text-[13.5px] font-medium text-white transition-colors hover:bg-white/10"
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
