"use client";

import { useMemo } from "react";
import { useDomains, useProcesses } from "./queries";
import type { Domain } from "./types";

/** Domains from the API; until the domains route exists they are read off the process list
 * (domain_id, domain_name) and cannot be renamed or merged. */
export function useDomainList(projectId: string | null): {
  domains: Domain[];
  editable: boolean;
  loading: boolean;
} {
  const d = useDomains(projectId);
  const p = useProcesses(projectId);
  return useMemo(() => {
    if (d.data) {
      const sorted = [...d.data].sort((a, b) => a.name.localeCompare(b.name));
      return { domains: sorted, editable: true, loading: false };
    }
    const byId = new Map<string, Domain>();
    for (const proc of p.data ?? []) {
      if (!proc.domain_id) continue;
      const cur = byId.get(proc.domain_id) ?? {
        id: proc.domain_id,
        name: proc.domain_name ?? proc.domain_id,
        suggested_name: proc.domain_name ?? proc.domain_id,
        process_ids: [],
        evidence: [],
        renamed_by: null,
      };
      cur.process_ids.push(proc.id);
      byId.set(proc.domain_id, cur);
    }
    return {
      domains: [...byId.values()].sort((a, b) => a.name.localeCompare(b.name)),
      editable: false,
      loading: d.isLoading || p.isLoading,
    };
  }, [d.data, d.isLoading, p.data, p.isLoading]);
}
