import { useEffect } from "react";
import { Pill } from "@/components/ui/Pill";

/** A titled card used by the Build, Verify and Deploy tabs. */
export function Panel({
  title,
  icon,
  eyebrow,
  actions,
  children,
  testId,
  id,
  className = "",
}: {
  title: string;
  /** A mark shown beside the title, e.g. the vendor a panel configures. */
  icon?: React.ReactNode;
  eyebrow?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  id?: string;
  className?: string;
}) {
  return (
    <section id={id} className={`card scroll-mt-4 p-5 ${className}`} data-testid={testId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          {eyebrow ? <p className="label">{eyebrow}</p> : null}
          <h3 className="flex items-center gap-2 text-[18px] font-semibold text-plum">
            {icon}
            {title}
          </h3>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export type Outcome = "passed" | "failed" | "skipped" | "warning" | "running" | "pending" | "not_run" | string;

const OUTCOME: Record<string, { tone: "gate" | "error" | "det" | "decision" | "purple"; text: string }> = {
  passed: { tone: "gate", text: "passed" },
  failed: { tone: "error", text: "failed" },
  skipped: { tone: "det", text: "skipped" },
  not_run: { tone: "det", text: "not run" },
  warning: { tone: "decision", text: "warning" },
  running: { tone: "purple", text: "running" },
  pending: { tone: "det", text: "pending" },
};

export function OutcomePill({ outcome, testId, text }: { outcome: Outcome; testId?: string; text?: string }) {
  const o = OUTCOME[outcome] ?? { tone: "det" as const, text: outcome.replace(/_/g, " ") };
  return (
    <Pill tone={o.tone} testId={testId}>
      {text ?? o.text}
    </Pill>
  );
}

/** A yes / no / unknown check as a pill. */
export function CheckPill({ ok, yes = "ok", no = "failed", unknown = "not run" }: { ok: boolean | null | undefined; yes?: string; no?: string; unknown?: string }) {
  if (ok === true) return <Pill tone="gate">{yes}</Pill>;
  if (ok === false) return <Pill tone="error">{no}</Pill>;
  return <Pill tone="det">{unknown}</Pill>;
}

export function TagPill({ tag }: { tag: "code" | "ai" | string }) {
  return (
    <span
      className={`h-fit shrink-0 rounded px-1.5 text-[10.5px] font-semibold uppercase leading-5 ${
        tag === "ai" ? "bg-ai-bg text-ai" : "bg-det-bg text-det"
      }`}
    >
      {tag}
    </span>
  );
}

export function shortHash(h: string | null | undefined, n = 12): string {
  return h ? h.slice(0, n) : "none";
}

export function errorText(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e && typeof (e as { detail: unknown }).detail === "string")
    return (e as { detail: string }).detail;
  return e instanceof Error ? e.message : String(e);
}

/** After a deep link (/#metrics), scroll to that panel once it has rendered, then drop the hash. */
export function useScrollToHash(): void {
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      const el = document.getElementById(id);
      tries += 1;
      if (el || tries > 40) {
        window.clearInterval(timer);
        if (el) {
          el.scrollIntoView({ block: "start", behavior: "smooth" });
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
        }
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, []);
}
