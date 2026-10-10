import { StatusPill } from "@/components/ui/Pill";
import type { StepState } from "@/lib/types";

const PURPOSE: Record<string, string> = {
  validate: "Rules and findings drafted from the parsed model, with verified citations. Gates G1 and G2.",
  design: "Mapping from each TIBCO step to a C# file, class and method. Gate G3.",
  build: "dotnet build, tests per rule and branch, metrics and infrastructure validation. Gate G4.",
  verify: "Scenarios run against the service, parity field by field against the TIBCO model. Gate G5.",
  deploy: "Terragrunt plan, pipeline definition and the connect-later state. Gate G6.",
};

export function LaterStep({ step }: { step: StepState }) {
  return (
    <section className="card p-8">
      <p className="eyebrow">Step</p>
      <div className="mt-1 flex items-center gap-3">
        <h2 className="panel-title">{step.label}</h2>
        <StatusPill status={step.status} />
      </div>
      <p className="mt-3 max-w-[720px] text-slate">{PURPOSE[step.key]}</p>
      <p className="mt-4 text-[14px] text-slate">This tab is not available in this build yet.</p>
    </section>
  );
}
