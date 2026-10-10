import type { StepStatus } from "@/lib/types";

type Tone = "det" | "ai" | "gate" | "decision" | "error" | "purple" | "plum" | "coral";

const TONES: Record<Tone, string> = {
  det: "bg-det-bg text-det",
  ai: "bg-ai-bg text-ai",
  gate: "bg-gate-bg text-gate",
  decision: "bg-decision-bg text-decision",
  error: "bg-error-bg text-error",
  purple: "bg-purple-lt text-purple-dk",
  plum: "bg-plum text-white",
  coral: "bg-coral text-white",
};

export function Pill({
  tone = "det",
  children,
  className = "",
  title,
  testId,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  title?: string;
  testId?: string;
}) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-medium leading-5 ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

const STATUS: Record<StepStatus, { tone: Tone; label: string }> = {
  not_started: { tone: "det", label: "not started" },
  running: { tone: "purple", label: "running" },
  needs_review: { tone: "decision", label: "needs review" },
  complete: { tone: "det", label: "complete" },
  approved: { tone: "gate", label: "approved" },
  failed: { tone: "error", label: "failed" },
};

export function StatusPill({ status }: { status: StepStatus }) {
  const s = STATUS[status];
  return <Pill tone={s.tone}>{s.label}</Pill>;
}

export function BandPill({ band }: { band: string | null | undefined }) {
  if (!band) return <Pill tone="det">unscored</Pill>;
  const tone: Tone = band === "high" ? "error" : band === "medium" ? "decision" : "det";
  return <Pill tone={tone}>{band}</Pill>;
}
