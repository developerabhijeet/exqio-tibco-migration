"use client";

import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Panel, errorText } from "@/components/tabs/build/Panel";
import { Pill } from "@/components/ui/Pill";
import type { SettingsView } from "@/lib/buildTypes";

/** The install's target stacks and the default for new projects. */
export function StackPanel({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const save = useMutation({
    mutationFn: (key: string) => api.putSettings({ default_stack: key }),
    onSuccess: onSaved,
  });
  return (
    <Panel title="Target stacks" eyebrow="What new projects build" testId="stacks-panel">
      <label className="label" htmlFor="default-stack">
        Default for new projects
      </label>
      <select
        id="default-stack"
        data-testid="default-stack"
        className="input mt-1 max-w-[320px]"
        value={s.default_stack}
        disabled={save.isPending}
        onChange={(e) => save.mutate(e.target.value)}
      >
        {s.stacks.map((st) => (
          <option key={st.key} value={st.key} disabled={!st.selectable}>
            {st.label}
            {st.reason ? ` (${st.reason})` : ""}
          </option>
        ))}
      </select>
      {save.error ? <p role="alert" className="mt-2 text-[13px] text-error">{errorText(save.error)}</p> : null}
      <ul className="mt-3 divide-y divide-line text-[13.5px]">
        {s.stacks.map((st) => (
          <li key={st.key} className="flex flex-wrap items-center gap-2 py-2" data-stack={st.key}>
            <span className="font-medium text-plum">{st.label}</span>
            {st.key === s.default_stack ? <Pill tone="purple">default</Pill> : null}
            <Pill tone={st.selectable ? "gate" : "error"}>{st.selectable ? "available" : "unavailable"}</Pill>
            {st.reason ? <span className="text-slate">{st.reason}</span> : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
