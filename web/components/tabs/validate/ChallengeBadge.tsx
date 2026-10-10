import type { Challenge } from "@/lib/types";
import { Pill } from "@/components/ui/Pill";

/** The challenger model's verdict on a drafted rule: agree (green) or disagree (amber) with its reason. */
export function ChallengeBadge({ challenge }: { challenge: Challenge | null | undefined }) {
  if (!challenge) return <Pill tone="det">not challenged</Pill>;
  const agree = challenge.verdict === "agree";
  return (
    <Pill tone={agree ? "gate" : "decision"} title={challenge.model ? `Challenger ${challenge.model}` : undefined} testId="challenge">
      challenger {agree ? "agrees" : "disagrees"}
    </Pill>
  );
}
