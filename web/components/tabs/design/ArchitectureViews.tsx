"use client";

// The target architecture, for an architect to read before approving G3.
//
// Every view here is derived by code from the design record and the parsed process
// (engine/dotnet_gen/datamodel.py): a class box comes from the mapping row that already chose the
// class and method, a table comes from the SQL the TIBCO activity already carries, a component edge
// comes from the solution layout and the declared messaging. No model draws any of it, so what the
// architect approves is the service the builder is about to emit rather than a picture of it.
import { useState } from "react";
import { Boxes, Database, FileCode2, Share2, Table2 } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { DesignRecord } from "@/lib/types";

type Views = NonNullable<DesignRecord["views"]>;

const ROLE_TONE: Record<string, "gate" | "decision" | "det"> = {
  read: "det",
  written: "gate",
  filter: "decision",
};

/** UML-style class boxes: name, stereotype, then the methods the mapping gives the class. */
function ClassDiagram({ classes }: { classes: Views["classes"] }) {
  if (classes.length === 0) return <p className="text-[14px] text-slate">No classes in this design.</p>;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="class-diagram">
      {classes.map((c) => (
        <figure key={c.name} className="overflow-hidden rounded-btn border border-plum/25 bg-white">
          <figcaption className="border-b border-plum/20 bg-warm px-3 py-2">
            {c.stereotype ? <p className="text-[11.5px] text-slate">{`«${c.stereotype}»`}</p> : null}
            <p className="font-semibold text-plum">{c.name}</p>
            <p className="truncate font-mono text-[11px] text-slate" title={`${c.project}/${c.file}`}>
              {c.project}
            </p>
          </figcaption>
          <ul className="divide-y divide-line/60">
            {c.methods.map((m) => (
              <li key={m} className="px-3 py-1.5 font-mono text-[12px] text-ink">
                + {m}()
              </li>
            ))}
            {c.methods.length === 0 ? (
              <li className="px-3 py-1.5 text-[12.5px] text-slate">no methods</li>
            ) : null}
          </ul>
          {c.rule_ids.length > 0 ? (
            <p className="flex flex-wrap gap-1 border-t border-line/60 px-3 py-1.5">
              {c.rule_ids.slice(0, 6).map((r) => (
                <Pill key={r} tone="purple">
                  {r.split(":").pop()}
                </Pill>
              ))}
            </p>
          ) : null}
        </figure>
      ))}
    </div>
  );
}

/** The database shape the process implies: one box per table, columns with how they are used. */
function TableDiagram({ tables }: { tables: Views["tables"] }) {
  if (tables.length === 0) return <p className="text-[14px] text-slate">This process touches no tables.</p>;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="er-diagram">
      {tables.map((t) => (
        <figure key={t.name} className="overflow-hidden rounded-btn border border-purple/30 bg-white">
          <figcaption className="flex flex-wrap items-center gap-2 border-b border-purple/20 bg-purple-lt px-3 py-2">
            <Table2 size={15} className="text-purple" aria-hidden />
            <span className="font-mono font-semibold text-plum">{t.name}</span>
            <span className="ml-auto flex gap-1">
              {t.verbs.map((v) => (
                <Pill key={v} tone="det">
                  {v}
                </Pill>
              ))}
            </span>
          </figcaption>
          <table className="w-full text-[12.5px]">
            <tbody>
              {t.columns.map((c) => (
                <tr key={c.name} className="border-b border-line/50 last:border-0">
                  <td className="px-3 py-1.5 font-mono text-ink">{c.name}</td>
                  <td className="px-2 py-1.5 text-slate">{c.type || "n/a"}</td>
                  <td className="px-3 py-1.5 text-right">
                    <Pill tone={ROLE_TONE[c.role] ?? "det"}>{c.role}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {t.columns.length === 0 ? (
            <p className="px-3 py-2 text-[12.5px] text-slate">no columns named in the SQL</p>
          ) : null}
        </figure>
      ))}
    </div>
  );
}

/** Every statement the service will run, with the parameters it binds. */
function SqlList({ sql }: { sql: Views["sql"] }) {
  if (sql.length === 0) return <p className="text-[14px] text-slate">This process runs no SQL.</p>;
  return (
    <ul className="space-y-3" data-testid="sql-list">
      {sql.map((q) => (
        <li key={q.activity_id} className="rounded-btn border border-line">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-warm px-3 py-2 text-[13px]">
            <Pill tone="purple">{q.verb}</Pill>
            <span className="font-semibold text-plum">{q.activity_name}</span>
            <span className="text-slate">
              {q.tables.length > 0 ? `on ${q.tables.join(", ")}` : "no table resolved"}
            </span>
            {q.parameters.length > 0 ? (
              <span className="ml-auto font-mono text-[11.5px] text-slate">
                {q.parameters.map((p) => `@${p}`).join(", ")}
              </span>
            ) : null}
          </div>
          <pre className="overflow-x-auto px-3 py-2 font-mono text-[12px] leading-5 text-ink">{q.statement}</pre>
        </li>
      ))}
    </ul>
  );
}

/** Projects, the database and the queues, with the direction of every connection. */
function ComponentDiagram({ components }: { components: Views["components"] }) {
  if (components.length === 0) return <p className="text-[14px] text-slate">No components to draw.</p>;
  const KIND_TONE: Record<string, "det" | "gate" | "decision" | "purple"> = {
    project: "purple",
    database: "det",
    queue: "gate",
    topic: "gate",
    subscription: "gate",
    process: "decision",
  };
  return (
    <ul className="space-y-2" data-testid="component-diagram">
      {components.map((e, i) => (
        <li key={i} className="flex flex-wrap items-center gap-2 rounded-btn border border-line px-3 py-2 text-[13px]">
          <span className="font-mono font-medium text-plum">{e.source}</span>
          <span aria-hidden className="text-slate">
            →
          </span>
          <span className="font-mono font-medium text-plum">{e.target}</span>
          <span className="text-slate">{e.label}</span>
          <Pill tone={KIND_TONE[e.kind] ?? "det"} className="ml-auto">
            {e.kind}
          </Pill>
        </li>
      ))}
    </ul>
  );
}

const TABS = [
  { key: "classes", label: "Classes", icon: Boxes },
  { key: "tables", label: "Data model", icon: Database },
  { key: "sql", label: "SQL", icon: FileCode2 },
  { key: "components", label: "Components", icon: Share2 },
] as const;

export function ArchitectureViews({ views }: { views: DesignRecord["views"] }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("classes");
  if (!views) {
    return (
      <p className="text-[14px] text-slate">
        This design record was generated before the diagrams were added. Run the design again to draw them.
      </p>
    );
  }
  const counts: Record<string, number> = {
    classes: views.classes.length,
    tables: views.tables.length,
    sql: views.sql.length,
    components: views.components.length,
  };
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Architecture views">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            data-testid={`view-${key}`}
            className={`flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-[13.5px] font-medium ${
              tab === key ? "bg-purple-lt text-purple" : "text-slate hover:bg-warm"
            }`}
            onClick={() => setTab(key)}
          >
            <Icon size={15} aria-hidden /> {label}
            <span className="text-[12px] text-slate">{counts[key]}</span>
          </button>
        ))}
      </div>
      {tab === "classes" ? <ClassDiagram classes={views.classes} /> : null}
      {tab === "tables" ? <TableDiagram tables={views.tables} /> : null}
      {tab === "sql" ? <SqlList sql={views.sql} /> : null}
      {tab === "components" ? <ComponentDiagram components={views.components} /> : null}
    </div>
  );
}
