import { FileCode2, Folder, Package } from "lucide-react";
import type { DesignRecord } from "@/lib/types";

const KIND: Record<string, string> = {
  api: "host, endpoints and consumers",
  core: "contracts, services and rules",
  infrastructure: "data access and messaging",
  tests: "rule, branch and scenario tests",
};

function grouped(files: string[]): [string, string[]][] {
  const dirs = new Map<string, string[]>();
  for (const f of files) {
    const i = f.lastIndexOf("/");
    const dir = i < 0 ? "" : f.slice(0, i);
    dirs.set(dir, [...(dirs.get(dir) ?? []), i < 0 ? f : f.slice(i + 1)]);
  }
  return [...dirs.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** The generated solution: one .sln, four projects, files grouped by folder. */
export function SolutionTree({ solution }: { solution: DesignRecord["solution"] }) {
  return (
    <div className="font-mono text-sm" data-testid="solution-tree">
      <p className="flex items-center gap-1.5 font-semibold text-heading">
        <Package size={15} aria-hidden /> {solution.name ?? "Solution"}.sln
      </p>
      <ul className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
        {solution.projects.map((p) => (
          <li key={p.name} className="rounded-btn border border-line bg-page p-3">
            <p className="font-semibold text-heading">{p.name}</p>
            <p className="font-sans text-xs text-slate">{KIND[p.kind] ?? p.kind}</p>
            <ul className="mt-2 space-y-1">
              {grouped(p.files).map(([dir, names]) => (
                <li key={dir}>
                  {dir ? (
                    <p className="flex items-center gap-1 text-slate">
                      <Folder size={13} aria-hidden /> {dir}/
                    </p>
                  ) : null}
                  <ul className={dir ? "ml-4" : ""}>
                    {names.map((n) => (
                      <li key={n} className="flex items-center gap-1 text-ink">
                        <FileCode2 size={12} className="text-slate" aria-hidden /> {n}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}
