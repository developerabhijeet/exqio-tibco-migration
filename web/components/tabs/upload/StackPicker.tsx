"use client";

import { useRef } from "react";
import { useStacks } from "@/lib/stacks";

/**
 * The target stack as a radio group (one tab stop, arrows move and select). A stack the API says
 * cannot be chosen is shown with its reason; `disabledReason` makes the whole group read only.
 */
export function StackPicker({
  value,
  onChange,
  disabledReason,
  pending = false,
}: {
  value: string | undefined;
  onChange: (key: string) => void;
  /** Why the whole picker is read only (the user may not change the stack); undefined when they may. */
  disabledReason?: string;
  /** A change is in flight: nothing can be picked until it settles. */
  pending?: boolean;
}) {
  const stacks = useStacks();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const list = stacks.data?.stacks ?? [];
  const usable = (key: string) => !!list.find((s) => s.key === key)?.selectable && !disabledReason && !pending;
  const enabled = list.filter((s) => usable(s.key));
  // Roving tabindex: the checked radio, else the first one that can be chosen, is the tab stop.
  const stop = list.find((s) => s.key === value)?.key ?? enabled[0]?.key ?? list[0]?.key;

  const move = (from: number, step: 1 | -1) => {
    for (let i = 1; i <= list.length; i++) {
      const next = list[(from + step * i + list.length * i) % list.length];
      if (usable(next.key)) {
        refs.current[next.key]?.focus();
        if (next.key !== value) onChange(next.key);
        return;
      }
    }
  };

  return (
    <div className="card p-5" data-testid="stack-picker" aria-busy={pending}>
      <p className="label" id="stack-picker-label">Target stack</p>
      {disabledReason ? (
        <p id="stack-picker-reason" className="mt-1 text-[13px] text-slate" data-testid="stack-picker-reason">
          {disabledReason}
        </p>
      ) : null}
      <div
        role="radiogroup"
        aria-labelledby="stack-picker-label"
        aria-describedby={disabledReason ? "stack-picker-reason" : undefined}
        className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
      >
        {list.map((s, i) => {
          const disabled = !usable(s.key);
          const checked = s.key === value;
          const [name, ...rest] = s.label.split(" · ");
          return (
            <button
              key={s.key}
              ref={(el) => {
                refs.current[s.key] = el;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-disabled={disabled}
              aria-describedby={s.reason ? `stack-reason-${s.key}` : undefined}
              tabIndex={s.key === stop ? 0 : -1}
              data-testid={`stack-${s.key}`}
              className={`rounded-card border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple/60 ${
                checked ? "border-purple bg-purple-lt" : "border-line"
              } ${disabled ? "opacity-60" : ""}`}
              onClick={() => {
                if (!disabled && !checked) onChange(s.key);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                  e.preventDefault();
                  move(i, 1);
                } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                  e.preventDefault();
                  move(i, -1);
                }
              }}
            >
              <span className="block text-[14px] font-semibold text-plum">{name}</span>
              <span className="block text-[12.5px] text-slate">{rest.join(" · ")}</span>
              {s.reason ? (
                <span id={`stack-reason-${s.key}`} className="mt-1 block text-[12px] text-slate">
                  {s.reason}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
