"use client";

import { Gauge, Info } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { Metrics } from "@/lib/buildTypes";
import { AXES } from "./MetricsRadar";
import { Panel } from "./Panel";

/** What each metric contributes, mirroring engine/dotnet_gen/metrics.py ACCURACY_WEIGHTS. */
const WEIGHTS: Record<string, number> = {
  rule_carry_forward: 0.3,
  test_pass_rate: 0.25,
  branch_coverage: 0.2,
  code_coverage: 0.15,
  standards_adherence: 0.1,
};

const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

function tone(score: number): { ring: string; text: string; word: string } {
  if (score >= 0.9) return { ring: "text-gate", text: "text-gate", word: "strong" };
  if (score >= 0.75) return { ring: "text-ai", text: "text-ai", word: "fair" };
  if (score >= 0.5) return { ring: "text-decision", text: "text-decision", word: "weak" };
  return { ring: "text-error", text: "text-error", word: "poor" };
}

/** A ring drawn with two SVG circles; no chart library and no animation to mislead. */
function Dial({ score, colour }: { score: number; colour: string }) {
  const r = 52;
  const circumference = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 128 128" className="h-[128px] w-[128px] shrink-0" role="img" aria-label={`${pct(score)} accuracy`}>
      <circle cx="64" cy="64" r={r} fill="none" stroke="var(--line)" strokeWidth="11" />
      <circle
        cx="64"
        cy="64"
        r={r}
        fill="none"
        strokeWidth="11"
        strokeLinecap="round"
        className={colour}
        stroke="currentColor"
        strokeDasharray={`${circumference * score} ${circumference}`}
        transform="rotate(-90 64 64)"
      />
      <text x="64" y="62" textAnchor="middle" className="fill-plum text-[26px] font-semibold" dominantBaseline="middle">
        {Math.round(score * 100)}
      </text>
      <text x="64" y="82" textAnchor="middle" className="fill-slate text-[11px]" dominantBaseline="middle">
        percent
      </text>
    </svg>
  );
}

/**
 * One number for how accurately this process was converted, over the five quality metrics the
 * build already computes. It is a weighted mean of what was actually measured: a metric with no
 * denominator is left out and the rest renormalised, and the breakdown below says which, so a high
 * score over two metrics can never be read as a high score over five. A solution that does not
 * compile scores 0, whatever the other numbers say.
 *
 * Computed by code in the engine and stored with the build, so the number here is the number in the
 * build record, not a figure this screen worked out on its own.
 */
export function AccuracyScore({ metrics }: { metrics: Metrics | null }) {
  const score = metrics?.conversion_accuracy;
  const detail = metrics?.details?.conversion_accuracy;
  const formula = metrics?.formulas?.conversion_accuracy;

  if (!metrics) {
    return (
      <Panel title="Conversion accuracy" eyebrow="Computed by code" testId="accuracy-score" id="accuracy">
        <p className="text-[14px] text-slate">The score appears when the build reaches its metrics stage.</p>
      </Panel>
    );
  }

  // A build made before this metric existed carries no value; say that rather than showing 0%.
  if (score === null || score === undefined) {
    return (
      <Panel title="Conversion accuracy" eyebrow="Computed by code" testId="accuracy-score" id="accuracy">
        <p data-testid="accuracy-absent" className="text-[14px] text-slate">
          {detail ?? "This build recorded no accuracy score. Generate and build again to compute one."}
        </p>
      </Panel>
    );
  }

  const t = tone(score);
  const measured = AXES.filter((a) => typeof metrics[a.key] === "number");
  const missing = AXES.filter((a) => typeof metrics[a.key] !== "number");
  const weightTotal = measured.reduce((sum, a) => sum + WEIGHTS[a.key], 0);
  const failedCompile = metrics.compile_ok === false;

  return (
    <Panel
      title="Conversion accuracy"
      eyebrow="One number over every quality metric of this build"
      testId="accuracy-score"
      id="accuracy"
      actions={
        <Pill tone={score >= 0.9 ? "gate" : score >= 0.75 ? "ai" : score >= 0.5 ? "decision" : "error"}>{t.word}</Pill>
      }
    >
      <div className="flex flex-wrap items-center gap-6">
        <Dial score={score} colour={t.ring} />
        <div className="min-w-[280px] flex-1">
          <p className={`text-[34px] font-semibold leading-none ${t.text}`} data-testid="accuracy-value">
            {pct(score)}
          </p>
          <p className="mt-1.5 text-[13.5px] text-slate">
            {failedCompile ? (
              <span className="font-medium text-error">
                The solution does not compile, so the score is 0 whatever the other metrics say: none of this code runs.
              </span>
            ) : (
              <>
                A weighted average of {measured.length} of {AXES.length} quality metrics.
                {missing.length > 0 ? (
                  <>
                    {" "}
                    {missing.map((a) => a.label).join(" and ")} {missing.length === 1 ? "has" : "have"} no denominator in
                    this build, so {missing.length === 1 ? "it is" : "they are"} left out rather than counted as zero.
                  </>
                ) : null}
              </>
            )}
          </p>
        </div>
      </div>

      {!failedCompile ? (
        <table className="mt-5 w-full text-[13px]">
          <thead>
            <tr className="table-head">
              <th scope="col" className="rounded-l-btn px-3 py-1.5">
                Metric
              </th>
              <th scope="col" className="px-3 py-1.5 text-right">
                Value
              </th>
              <th scope="col" className="px-3 py-1.5 text-right">
                Weight
              </th>
              <th scope="col" className="rounded-r-btn px-3 py-1.5 text-right">
                Contribution
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {AXES.map((a) => {
              const value = metrics[a.key];
              const has = typeof value === "number";
              const weight = has ? WEIGHTS[a.key] / weightTotal : 0;
              return (
                <tr key={a.key} data-testid={`accuracy-row-${a.key}`} className="row-hover">
                  <td className="px-3 py-1.5 text-ink">{a.label}</td>
                  <td className={`px-3 py-1.5 text-right font-medium ${has ? "text-plum" : "text-slate"}`}>
                    {has ? pct(value as number) : "not measured"}
                  </td>
                  <td className="px-3 py-1.5 text-right text-slate">{has ? pct(weight) : "left out"}</td>
                  <td className="px-3 py-1.5 text-right font-medium text-plum">
                    {has ? pct((value as number) * weight) : "0%"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}

      <p className="mt-4 flex items-start gap-2 text-[12.5px] text-slate">
        <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          {formula ??
            "Weighted over rule carry-forward 30%, test pass rate 25%, branch coverage 20%, code coverage 15%, standards adherence 10%."}{" "}
          Infrastructure validity is not in the score: it says nothing about the converted code. Computed by code from
          this build&apos;s own outputs; no model is involved.
        </span>
      </p>
      {detail ? (
        <p className="mt-2 rounded-btn bg-page px-3 py-2 font-mono text-[11.5px] leading-5 text-ink" data-testid="accuracy-detail">
          <Gauge size={12} className="mr-1 inline" aria-hidden />
          {detail}
        </p>
      ) : null}
    </Panel>
  );
}
