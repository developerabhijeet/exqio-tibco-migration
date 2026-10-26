import { Hand } from "lucide-react";
import { Pill } from "@/components/ui/Pill";

/**
 * A file a person edited (origin `hand`): the build applied it from an edit set, no agent or repair
 * may change it, and the review panel still reviews it.
 */
export function HandBadge({ detail }: { detail?: string }) {
  return (
    <Pill
      tone="decision"
      testId="hand-badge"
      title={detail || "Edited by hand: applied from an edit set; no model or repair may change it"}
    >
      <Hand size={11} aria-hidden />
      hand edit
    </Pill>
  );
}
