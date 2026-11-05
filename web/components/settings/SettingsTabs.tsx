"use client";

// The Settings tabs (the WAI-ARIA tabs pattern): General holds the existing panels, Connectors
// the connector cards. The tab lives in the query string (?tab=connectors) so a link opens it;
// General is the bare /settings.
import { useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type SettingsTab = "general" | "connectors";

const TABS: { key: SettingsTab; label: string }[] = [
  { key: "general", label: "General" },
  { key: "connectors", label: "Connectors" },
];

export function useSettingsTab(): [SettingsTab, (tab: SettingsTab) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab: SettingsTab = params.get("tab") === "connectors" ? "connectors" : "general";
  const set = (next: SettingsTab) => {
    const q = new URLSearchParams(params.toString());
    if (next === "general") q.delete("tab");
    else q.set("tab", next);
    router.replace(`${pathname}${q.size ? `?${q.toString()}` : ""}`, { scroll: false });
  };
  return [tab, set];
}

export function SettingsTabs({ panels }: { panels: Record<SettingsTab, React.ReactNode> }) {
  const [tab, setTab] = useSettingsTab();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const move = (from: number, delta: number) => {
    const next = TABS[(from + delta + TABS.length) % TABS.length].key;
    setTab(next);
    refs.current[next]?.focus();
  };
  return (
    <div>
      <div role="tablist" aria-label="Settings sections" className="mb-5 flex gap-1 border-b border-line">
        {TABS.map(({ key, label }, i) => {
          const selected = tab === key;
          return (
            <button
              key={key}
              ref={(el) => {
                refs.current[key] = el;
              }}
              type="button"
              role="tab"
              id={`settings-tab-${key}`}
              aria-selected={selected}
              aria-controls={`settings-panel-${key}`}
              tabIndex={selected ? 0 : -1}
              data-testid={`settings-tab-${key}`}
              className={`-mb-px border-b-2 px-4 py-2 text-md font-semibold transition-colors ${
                selected ? "border-accent text-accent-ink" : "border-transparent text-slate hover:text-heading"
              }`}
              onClick={() => setTab(key)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") move(i, 1);
                else if (e.key === "ArrowLeft") move(i, -1);
                else if (e.key === "Home") move(0, 0);
                else if (e.key === "End") move(TABS.length - 1, 0);
                else return;
                e.preventDefault();
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      {TABS.map(({ key }) => (
        <div
          key={key}
          role="tabpanel"
          id={`settings-panel-${key}`}
          aria-labelledby={`settings-tab-${key}`}
          hidden={tab !== key}
          tabIndex={0}
        >
          {tab === key ? panels[key] : null}
        </div>
      ))}
    </div>
  );
}
