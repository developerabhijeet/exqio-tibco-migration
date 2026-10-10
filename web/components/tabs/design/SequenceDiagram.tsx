import type { DesignRecord } from "@/lib/types";

const HEAD = 46;
const ROW = 34;
const PAD = 20;
const FRAME_TOP = 18;

function clip(text: string, px: number): string {
  const max = Math.max(4, Math.floor(px / 6.2));
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const isOtherwise = (label: string) => /^\[otherwise\]/.test(label);
const isGuard = (label: string) => /^\[/.test(label) && !/^\[(otherwise|error|catch)/.test(label);

/**
 * A vertical sequence drawn by code from the design record's `sequence[]` (no model drawing):
 * one lifeline per participant and one numbered arrow per call. A guarded call followed by its
 * `[otherwise]` exit is drawn inside an `alt` frame; a step the service runs on itself (a log
 * step, for example) is an activation bar on its lifeline, not a call. Full labels are listed below.
 * In print the SVG scales to the page width (see the print stylesheet).
 */
export function SequenceDiagram({ sequence }: { sequence: DesignRecord["sequence"] }) {
  const steps = sequence.map((s) => ({ from: s.from_ ?? s.from ?? "?", to: s.to, label: s.label }));
  const parts: string[] = [];
  for (const s of steps) for (const p of [s.from, s.to]) if (!parts.includes(p)) parts.push(p);
  if (steps.length === 0) return <p className="text-[14px] text-slate">The record has no sequence.</p>;

  const lane = Math.min(270, Math.max(170, Math.max(...parts.map((p) => p.length)) * 7.4 + 28));
  const x = (p: string) => PAD + parts.indexOf(p) * lane + lane / 2;
  const width = PAD * 2 + parts.length * lane;

  // alt frames: a guarded call immediately followed by its [otherwise] exit.
  const frames: { start: number; end: number }[] = [];
  steps.forEach((s, i) => {
    if (isGuard(s.label) && i + 1 < steps.length && isOtherwise(steps[i + 1].label)) frames.push({ start: i, end: i + 1 });
  });
  const ys: number[] = [];
  let y = HEAD + 28;
  steps.forEach((_, i) => {
    if (frames.some((f) => f.start === i)) y += FRAME_TOP;
    ys.push(y);
    y += ROW;
    if (frames.some((f) => f.end === i)) y += 6;
  });
  const height = y + 6;

  return (
    <div data-testid="sequence-diagram" className="space-y-3">
      <div className="sequence-scroll overflow-x-auto rounded-btn border border-line bg-page">
        <svg
          className="sequence-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="xMinYMin meet"
          role="img"
          aria-label="Sequence of calls"
        >
          <defs>
            <marker id="seq-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--plum)" />
            </marker>
            <marker id="seq-arrow-alt" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--decision)" />
            </marker>
            <marker id="seq-arrow-err" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="var(--error)" />
            </marker>
          </defs>
          {parts.map((p) => (
            <g key={p}>
              <line x1={x(p)} x2={x(p)} y1={HEAD} y2={height - 4} stroke="var(--line)" strokeWidth={1.5} strokeDasharray="4 4" />
              <rect x={x(p) - lane / 2 + 8} y={8} width={lane - 16} height={HEAD - 14} rx={8} fill="var(--purple-lt)" stroke="var(--purple)" />
              <text x={x(p)} y={8 + (HEAD - 14) / 2 + 4} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--plum)">
                <title>{p}</title>
                {clip(p, lane - 24)}
              </text>
            </g>
          ))}
          {frames.map((f) => {
            const involved = [steps[f.start], steps[f.end]].flatMap((s) => [x(s.from), x(s.to)]);
            const left = Math.max(4, Math.min(...involved) - 46);
            const right = Math.min(width - 4, Math.max(...involved) + 34);
            const top = ys[f.start] - FRAME_TOP - 10;
            const bottom = ys[f.end] + 12;
            const divider = (ys[f.start] + ys[f.end]) / 2 - 4;
            return (
              <g key={`frame-${f.start}`} data-testid="sequence-alt">
                <rect x={left} y={top} width={right - left} height={bottom - top} fill="none" stroke="var(--decision)" strokeDasharray="6 3" rx={4} />
                <path d={`M${left},${top} h30 v12 l-5,5 h-25 z`} fill="var(--decision-bg)" stroke="var(--decision)" />
                <text x={left + 5} y={top + 12} fontSize={10} fontWeight={700} fill="var(--decision)">
                  alt
                </text>
                <line x1={left} x2={right} y1={divider} y2={divider} stroke="var(--decision)" strokeDasharray="3 3" />
                <text x={left + 5} y={divider + 12} fontSize={10} fontStyle="italic" fontWeight={600} fill="var(--decision)">
                  else
                </text>
              </g>
            );
          })}
          {steps.map((s, i) => {
            const yy = ys[i];
            const x1 = x(s.from);
            const x2 = x(s.to);
            const err = /^\[(error|catch)/.test(s.label);
            const alt = isOtherwise(s.label);
            const color = err ? "var(--error)" : alt ? "var(--decision)" : "var(--plum)";
            const marker = `url(#seq-arrow${err ? "-err" : alt ? "-alt" : ""})`;
            const badge = (
              <g>
                <circle cx={x1} cy={yy} r={9} fill="white" stroke={color} />
                <text x={x1} y={yy + 3.5} textAnchor="middle" fontSize={10} fontWeight={600} fill={color}>
                  {i + 1}
                </text>
              </g>
            );
            if (x1 === x2) {
              // A local step on the same participant: an activation bar, not a call to itself.
              return (
                <g key={i} data-testid="sequence-local">
                  <title>{`${i + 1}. local step in ${s.from}: ${s.label}`}</title>
                  <rect x={x1 + 12} y={yy - 9} width={8} height={18} rx={2} fill="var(--warm)" stroke={color} />
                  {badge}
                  <text x={x1 + 28} y={yy + 4} fontSize={11} fill={color}>
                    {clip(s.label, lane - 40)}
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
                  y1={yy}
                  y2={yy}
                  stroke={color}
                  strokeWidth={1.3}
                  strokeDasharray={err || alt ? "5 3" : undefined}
                  markerEnd={marker}
                />
                {badge}
                <text x={(x1 + x2) / 2} y={yy - 6} textAnchor="middle" fontSize={11} fill={color}>
                  {clip(s.label, Math.abs(x2 - x1) - 24)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="text-[12px] text-slate">
        Dashed amber frames pair a guarded call with its <span className="font-mono">[otherwise]</span> exit. A bar on a
        lifeline is a step the service runs itself.
      </p>
      <ol className="grid gap-x-6 gap-y-1 text-[12.5px] md:grid-cols-2">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="w-5 shrink-0 text-right font-semibold text-plum">{i + 1}</span>
            <span className="min-w-0 break-words">
              <span className="text-slate">{s.from === s.to ? `${s.from} (local step):` : `${s.from} to ${s.to}:`}</span>{" "}
              <span className="font-mono text-[12px] text-ink">{s.label}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
