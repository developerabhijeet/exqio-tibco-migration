"use client";

import { useEffect, useState } from "react";
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip } from "recharts";
import { Gauge, X } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { Metrics } from "@/lib/buildTypes";
import { Panel } from "./Panel";

export const AXES = [
  { key: "rule_carry_forward", label: "Rule carry-forward" },
  { key: "test_pass_rate", label: "Test pass rate" },
  { key: "branch_coverage", label: "Branch coverage" },
  { key: "code_coverage", label: "Code coverage" },
  { key: "standards_adherence", label: "Standards adherence" },
] as const;

type AxisKey = (typeof AXES)[number]["key"] | "compile_ok" | "infra_ok";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "no value" : `${Math.round(v * 100)}%`);

function MetricDrawer({ metrics, axis, onClose }: { metrics: Metrics; axis: AxisKey; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const label = AXES.find((a) => a.key === axis)?.label ?? (axis === "compile_ok" ? "Compile" : "Infrastructure validity");
  const value = metrics[axis as keyof Metrics];
  return (
    <div className="fixed inset-0 z-50 flex justify-end print:hidden" role="presentation">
      <button type="button" aria-label="Dismiss metric details" className="absolute inset-0 bg-plum/20" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${label} details`}
        data-testid="metric-drawer"
        className="relative flex h-full w-[460px] max-w-full flex-col border-l border-line bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-line px-6 py-5">
          <div>
            <p className="eyebrow">Metric</p>
            <h2 className="panel-title mt-1">{label}</h2>
          </div>
          <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="space-y-5 px-6 py-5 text-[14px]">
          <div>
            <p className="label">Value</p>
            <p className="mt-1 text-[28px] font-semibold text-plum">
              {typeof value === "boolean" ? (value ? "pass" : "fail") : typeof value === "number" ? pct(value) : "no value"}
            </p>
          </div>
          <div>
            <p className="label">Formula</p>
            <p className="mt-1 leading-relaxed text-ink">{metrics.formulas[axis] ?? "No formula reported."}</p>
          </div>
          <div>
            <p className="label">Counts from this build</p>
            <p className="mt-1 rounded-btn bg-page px-3 py-2 font-mono text-[12.5px] text-ink">
              {metrics.details[axis] ?? "No counts reported."}
            </p>
          </div>
          <p className="text-[12.5px] text-slate">Computed by code from the build outputs; no model is involved.</p>
        </div>
      </aside>
    </div>
  );
}

/** Five quality axes from 0 to 100%, with compile and infrastructure validity as pills. */
export function MetricsRadar({ metrics }: { metrics: Metrics | null }) {
  const [open, setOpen] = useState<AxisKey | null>(null);
  if (!metrics) {
    return (
      <Panel title="Metrics" eyebrow="Computed by code" testId="metrics-radar" id="metrics">
        <p className="text-[14px] text-slate">Metrics appear when the build reaches its metrics stage.</p>
      </Panel>
    );
  }
  const data = AXES.map((a) => ({
    key: a.key,
    axis: a.label,
    value: Math.round((metrics[a.key] ?? 0) * 100),
    shown: pct(metrics[a.key]),
  }));
  return (
    <Panel
      title="Metrics"
      eyebrow="Computed by code"
      testId="metrics-radar"
      id="metrics"
      actions={
        <>
          <button type="button" onClick={() => setOpen("compile_ok")} data-testid="compile-pill">
            <Pill tone={metrics.compile_ok ? "gate" : "error"}>
              {metrics.compile_ok ? "compile passed" : "compile failed"}
              {metrics.warnings ? `, ${metrics.warnings} warnings` : ""}
            </Pill>
          </button>
          <button type="button" onClick={() => setOpen("infra_ok")} data-testid="infra-pill">
            <Pill tone={metrics.infra_ok ? "gate" : "error"}>{metrics.infra_ok ? "infra valid" : "infra not validated"}</Pill>
          </button>
        </>
      }
    >
      <div className="grid items-center gap-4 md:grid-cols-[minmax(0,1fr)_240px]">
        <div className="h-[320px] min-w-0" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={data} outerRadius="68%" margin={{ top: 16, right: 72, bottom: 16, left: 72 }}>
              <PolarGrid stroke="#E2DFD6" />
              <PolarAngleAxis
                dataKey="axis"
                tick={({ x, y, payload, textAnchor }) => (
                  <text
                    x={x}
                    y={y}
                    textAnchor={textAnchor}
                    dominantBaseline="central"
                    fontSize={12}
                    fill="#425466"
                    style={{ cursor: "pointer" }}
                    onClick={() => setOpen(AXES.find((a) => a.label === payload.value)?.key ?? null)}
                  >
                    {payload.value}
                  </text>
                )}
              />
              <PolarRadiusAxis domain={[0, 100]} tickCount={5} tick={false} axisLine={false} />
              <Radar dataKey="value" stroke="#9B5DE5" strokeWidth={2} fill="#9B5DE5" fillOpacity={0.18} dot={{ r: 4, fill: "#9B5DE5" }} />
              <Tooltip
                formatter={(_v, _n, item) => [(item?.payload as { shown: string }).shown, "value"]}
                contentStyle={{ borderRadius: 6, borderColor: "#E2DFD6", fontSize: 12 }}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        <ul className="space-y-1.5 text-[13.5px]">
          {data.map((d) => (
            <li key={d.key}>
              <button
                type="button"
                data-testid="radar-axis"
                onClick={() => setOpen(d.key)}
                className="flex w-full items-center gap-2 rounded-btn border border-line px-2.5 py-1.5 text-left hover:border-purple"
              >
                <Gauge size={14} className="shrink-0 text-purple" aria-hidden />
                <span className="flex-1 text-ink">{d.axis}</span>
                <span className="font-semibold text-plum">{d.shown}</span>
              </button>
            </li>
          ))}
          <li className="pt-1 text-[12px] text-slate">Select a metric for its formula and counts.</li>
        </ul>
      </div>
      {open ? <MetricDrawer metrics={metrics} axis={open} onClose={() => setOpen(null)} /> : null}
    </Panel>
  );
}
