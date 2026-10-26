"use client";

import { useStacks } from "@/lib/stacks";

/** The target stack as a radio group. A stack the API reports unavailable is shown but cannot be chosen. */
export function StackPicker({
  value,
  onChange,
  disabledReason,
}: {
  value: string;
  onChange: (key: string) => void;
  /** Why the whole picker is read only (the user may not change the stack); undefined when they may. */
  disabledReason?: string;
}) {
  const stacks = useStacks();
  return (
    <div className="card p-5" data-testid="stack-picker">
      <p className="label">Target stack</p>
      <div role="radiogroup" aria-label="Target stack" className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" title={disabledReason}>
        {(stacks.data?.stacks ?? []).map((s) => {
          const unavailable = !s.selectable;
          const disabled = unavailable || !!disabledReason;
          const checked = s.key === value;
          return (
            <button
              key={s.key}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-disabled={disabled}
              data-testid={`stack-${s.key}`}
              title={unavailable ? (s.reason ?? "not available") : disabledReason}
              className={`rounded-card border p-3 text-left ${checked ? "border-purple bg-purple-lt" : "border-line"} ${disabled ? "opacity-60" : ""}`}
              onClick={() => {
                if (!disabled && !checked) onChange(s.key);
              }}
            >
              <span className="block text-[14px] font-semibold text-plum">{s.label.split(" · ")[0]}</span>
              <span className="block text-[12.5px] text-slate">{s.label.split(" · ").slice(1).join(" · ")}</span>
              {s.reason ? <span className="mt-1 block text-[12px] text-slate">{s.reason}</span> : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
