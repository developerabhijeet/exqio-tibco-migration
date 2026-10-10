import type { DesignRecord } from "@/lib/types";

const HEAD = 46;
const ROW = 34;
const PAD = 20;

function clip(text: string, px: number): string {
  const max = Math.max(4, Math.floor(px / 6.2));
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * A vertical sequence drawn by code from the design record's `sequence[]` (no model drawing):
 * one lifeline per participant, one numbered arrow per call, and the full labels listed below.
 */
export function SequenceDiagram({ sequence }: { sequence: DesignRecord["sequence"] }) {
  const steps = sequence.map((s) => ({ from: s.from_ ?? s.from ?? "?", to: s.to, label: s.label }));
  const parts: string[] = [];
  for (const s of steps) for (const p of [s.from, s.to]) if (!parts.includes(p)) parts.push(p);
  if (steps.length === 0) return <p className="text-[14px] text-slate">The record has no sequence.</p>;

  const lane = Math.min(270, Math.max(170, Math.max(...parts.map((p) => p.length)) * 7.4 + 28));
  const x = (p: string) => PAD + parts.indexOf(p) * lane + lane / 2;
  const width = PAD * 2 + parts.length * lane;
  const height = HEAD + 22 + steps.length * ROW + 12;

  return (
    <div data-testid="sequence-diagram" className="space-y-3">
      <div className="overflow-x-auto rounded-btn border border-line bg-page">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Sequence of calls">
          <defs>
            <marker id="seq-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--plum)" />
            </marker>
            <marker id="seq-arrow-err" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--error)" />
            </marker>
          </defs>
          {parts.map((p) => (
            <g key={p}>
              <line x1={x(p)} x2={x(p)} y1={HEAD} y2={height - 6} stroke="var(--line)" strokeWidth={1.5} strokeDasharray="4 4" />
              <rect x={x(p) - lane / 2 + 8} y={8} width={lane - 16} height={HEAD - 14} rx={8} fill="var(--purple-lt)" stroke="var(--purple)" />
              <text x={x(p)} y={8 + (HEAD - 14) / 2 + 4} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--plum)">
                <title>{p}</title>
                {clip(p, lane - 24)}
              </text>
            </g>
          ))}
          {steps.map((s, i) => {
            const y = HEAD + 28 + i * ROW;
            const x1 = x(s.from);
            const x2 = x(s.to);
            const err = /^\[(error|catch)/.test(s.label);
            const color = err ? "var(--error)" : "var(--plum)";
            const badge = (
              <g>
                <circle cx={x1} cy={y} r={9} fill="white" stroke={color} />
                <text x={x1} y={y + 3.5} textAnchor="middle" fontSize={10} fontWeight={600} fill={color}>
                  {i + 1}
                </text>
              </g>
            );
            if (x1 === x2) {
              return (
                <g key={i}>
                  <title>{`${i + 1}. ${s.from} to itself: ${s.label}`}</title>
                  <path d={`M${x1 + 9},${y - 4} h26 v12 h-24`} fill="none" stroke={color} strokeWidth={1.3} markerEnd={`url(#seq-arrow${err ? "-err" : ""})`} />
                  {badge}
                  <text x={x1 + 42} y={y + 4} fontSize={11} fill={color}>
                    {clip(s.label, lane - 50)}
                  </text>
                </g>
              );
            }
            const dir = x2 > x1 ? 1 : -1;
            return (
              <g key={i}>
                <title>{`${i + 1}. ${s.from} to ${s.to}: ${s.label}`}</title>
                <line
                  x1={x1 + dir * 9}
                  x2={x2 - dir * 2}
                  y1={y}
                  y2={y}
                  stroke={color}
                  strokeWidth={1.3}
                  strokeDasharray={err ? "5 3" : undefined}
                  markerEnd={`url(#seq-arrow${err ? "-err" : ""})`}
                />
                {badge}
                <text x={(x1 + x2) / 2} y={y - 6} textAnchor="middle" fontSize={11} fill={color}>
                  {clip(s.label, Math.abs(x2 - x1) - 24)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <ol className="grid gap-x-6 gap-y-1 text-[12.5px] md:grid-cols-2">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="w-5 shrink-0 text-right font-semibold text-plum">{i + 1}</span>
            <span className="min-w-0 break-words">
              <span className="text-slate">
                {s.from} to {s.to}:
              </span>{" "}
              <span className="font-mono text-[12px] text-ink">{s.label}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
