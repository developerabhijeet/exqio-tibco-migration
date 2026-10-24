"use client";

import { useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";

/** The process this tab works on; the same open process as Discover. */
export function ProcessPicker() {
  const { projectId, processId, openProcess } = useAppState();
  const processes = useProcesses(projectId);
  const list = [...(processes.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <label className="flex items-center gap-2 text-[13.5px] text-slate">
      Process
      <select
        data-testid="process-picker"
        className="select"
        value={processId ?? ""}
        onChange={(e) => openProcess(e.target.value || null)}
      >
        {!processId ? <option value="">Select a process</option> : null}
        {list.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} ({p.domain_name ?? p.module})
          </option>
        ))}
      </select>
    </label>
  );
}
