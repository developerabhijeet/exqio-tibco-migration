"use client";

// The memory canvas's node cards: an entity (a type icon on a tinted tile, the name, the type and
// a revision or iteration badge, over an accent bar in the type's colour) and a container (a
// process or class holding its activities or methods, and the knowledge about only them).
// Selection, hover, dimming and diff outlines come from MemoryCanvasState so the node objects stay
// stable while the reader moves the pointer.
import { createContext, memo, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Pin } from "lucide-react";
import { displayName, type MemNodeOut } from "@/lib/memory";
import { kindOf, tok } from "./kinds";
import type { MemFlowNode } from "./layout";

export interface CanvasState {
  /** The nodes lit by the hovered or selected node (null: nothing picked, nothing dims). */
  lit: Set<string> | null;
  /** The edges lit with them (drawn strong and animated; internal edges are drawn only then). */
  litEdges: Set<string> | null;
  selected?: string;
  hovered: string | null;
  added: Set<string>;
  changed: Set<string>;
  removed: Set<string>;
}

export const CanvasStateContext = createContext<CanvasState>({
  lit: null,
  litEdges: null,
  hovered: null,
  added: new Set(),
  changed: new Set(),
  removed: new Set(),
});

function ringOf(uid: string, s: CanvasState): string | undefined {
  if (s.added.has(uid)) return tok("gate");
  if (s.changed.has(uid)) return tok("decision");
  if (s.removed.has(uid)) return tok("error");
  return undefined;
}

/** A small count badge: the iteration a node came from, or the revision it was last written in. */
function badgeOf(n: MemNodeOut): { text: string; title: string } | null {
  const it = n.props?.iteration;
  if (typeof it === "number" && it > 0) return { text: `it ${it}`, title: `From iteration ${it}` };
  if (n.valid_from > 1) return { text: `r${n.valid_from}`, title: `Written in revision ${n.valid_from}` };
  return null;
}

function Badge({ n }: { n: MemNodeOut }) {
  const b = badgeOf(n);
  if (!b) return null;
  return (
    <span
      title={b.title}
      className="tnum shrink-0 rounded-full bg-sunken px-1.5 text-2xs font-medium leading-4 text-slate ring-1 ring-inset ring-line"
    >
      {b.text}
    </span>
  );
}

function Card({ n, compact, isFocus, uid }: { n: MemNodeOut; compact: boolean; isFocus: boolean; uid: string }) {
  const s = useContext(CanvasStateContext);
  const kind = kindOf(n.label);
  const Icon = kind.icon;
  const picked = s.selected === uid;
  const dim = s.lit !== null && !s.lit.has(uid);
  const ring = ringOf(uid, s);
  const colour = tok(kind.token);
  const name = displayName(n);
  return (
    <div
      className={`mem-card group relative flex h-full w-full items-center gap-2.5 overflow-hidden rounded-node border bg-surface pl-3 pr-2.5 transition-[opacity,box-shadow,transform] duration-fast ${
        picked ? "border-transparent shadow-e2" : "border-line shadow-e1 hover:-translate-y-px hover:shadow-e2"
      } ${dim ? "opacity-30" : ""} ${n.obsolete ? "border-dashed opacity-60" : ""}`}
      style={{
        boxShadow: picked
          ? `0 0 0 2px ${colour}, 0 0 0 6px ${tok(kind.token, 0.18)}, var(--shadow-e2)`
          : ring
            ? `0 0 0 2.5px ${ring}, var(--shadow-e1)`
            : isFocus
              ? `0 0 0 1.5px ${tok(kind.token, 0.7)}, var(--shadow-e2)`
              : undefined,
      }}
      data-kind={n.label}
      data-side={n.side}
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: colour }} />
      <span
        aria-hidden
        className={`flex shrink-0 items-center justify-center rounded-btn ${compact ? "h-7 w-7" : "h-8 w-8"}`}
        style={{ background: tok(kind.token, 0.12), color: colour }}
      >
        <Icon size={compact ? 14 : 16} strokeWidth={2} />
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span
          className={`block truncate font-semibold text-heading ${compact ? "text-xs" : isFocus ? "text-md" : "text-sm"}`}
          title={name}
        >
          {name}
        </span>
        <span className="mt-0.5 flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-slate">
          {kind.label}
          {n.curated ? (
            <span title="Curated" className="inline-flex items-center text-accent-ink">
              <Pin size={10} aria-hidden />
            </span>
          ) : null}
        </span>
      </span>
      <Badge n={n} />
    </div>
  );
}

export const EntityNode = memo(function EntityNode({ id, data }: NodeProps<MemFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <Card n={data.node} compact={data.compact} isFocus={data.isFocus} uid={id} />
      <Handle type="source" position={Position.Right} />
    </>
  );
});

export const ContainerNode = memo(function ContainerNode({ id, data }: NodeProps<MemFlowNode>) {
  const s = useContext(CanvasStateContext);
  const n = data.node;
  const kind = kindOf(n.label);
  const Icon = kind.icon;
  const picked = s.selected === id;
  // The lit set holds a container whenever anything inside it is lit.
  const dim = s.lit !== null && !s.lit.has(id);
  const ring = ringOf(id, s);
  const colour = tok(kind.token);
  const name = displayName(n);
  return (
    <div
      className={`mem-container relative h-full w-full rounded-card border transition-[opacity,box-shadow] duration-fast ${
        picked ? "border-transparent" : "border-line"
      } ${dim ? "opacity-40" : ""} ${n.obsolete ? "border-dashed" : ""}`}
      style={{
        background: `linear-gradient(180deg, ${tok(kind.token, 0.09)} 0, ${tok(kind.token, 0.03)} 62px, ${tok("surface", 0.55)} 62px)`,
        boxShadow: picked
          ? `0 0 0 2px ${colour}, 0 0 0 6px ${tok(kind.token, 0.16)}, var(--shadow-e2)`
          : ring
            ? `0 0 0 2.5px ${ring}, var(--shadow-e1)`
            : data.isFocus
              ? `0 0 0 1.5px ${tok(kind.token, 0.7)}, var(--shadow-e1)`
              : "var(--shadow-e1)",
      }}
      data-kind={n.label}
      data-side={n.side}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex h-[62px] items-center gap-2.5 border-b border-line/70 px-3.5">
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-btn"
          style={{ background: colour, color: "rgb(var(--c-surface))" }}
        >
          <Icon size={18} strokeWidth={2} />
        </span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className={`block truncate font-semibold text-heading ${data.isFocus ? "text-lg" : "text-md"}`} title={name}>
            {name}
          </span>
          <span className="mt-0.5 block text-2xs font-medium uppercase tracking-wide text-slate">
            {kind.label}
            {data.members ? ` · ${data.members} ${data.members === 1 ? "step" : "steps"}` : ""}
          </span>
        </span>
        {n.curated ? (
          <span title="Curated" className="text-accent-ink">
            <Pin size={12} aria-hidden />
          </span>
        ) : null}
        <Badge n={n} />
      </div>
      {data.knowledgeTop !== undefined ? (
        <p
          className="absolute left-3.5 text-2xs font-semibold uppercase tracking-wider text-decision"
          style={{ top: data.knowledgeTop + 6 }}
        >
          Knowledge · {data.knowledge}
        </p>
      ) : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export const memoryNodeTypes = { entity: EntityNode, container: ContainerNode };
