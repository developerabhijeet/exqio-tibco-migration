// No "use client": the root layout (a server component) imports NO_FLASH_SCRIPT from here. React is
// imported as a namespace so the server build accepts the module; useTheme runs only in client
// components.
import * as React from "react";

/**
 * The theme: System (follow the operating system), Light or Dark. The choice is kept per browser
 * in localStorage and applied as `data-theme` on <html>; with no stored choice the attribute is
 * absent and lib/theme.css follows `prefers-color-scheme`. NO_FLASH_SCRIPT runs inline in <head>
 * before first paint so a stored choice never flashes the other theme.
 */
export type ThemeChoice = "system" | "light" | "dark";

export const THEME_KEY = "exiqo.theme";

/** Fired on `window` whenever the effective theme may have changed; detail says whether it is dark. */
export const THEME_EVENT = "themechange";
export type ThemeChangeDetail = { dark: boolean };

const DARK_QUERY = "(prefers-color-scheme: dark)";

function isChoice(v: unknown): v is ThemeChoice {
  return v === "system" || v === "light" || v === "dark";
}

/** The stored choice; `system` when there is none or storage is unavailable. */
export function readTheme(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return isChoice(v) ? v : "system";
  } catch {
    return "system";
  }
}

function systemDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

/** Whether `choice` renders dark right now. */
export function resolveDark(choice: ThemeChoice): boolean {
  return choice === "dark" || (choice === "system" && systemDark());
}

/** Set (or, for System, remove) `data-theme` on <html> and announce the change. */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  window.dispatchEvent(new CustomEvent<ThemeChangeDetail>(THEME_EVENT, { detail: { dark: resolveDark(choice) } }));
}

function storeTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Storage blocked: the choice still applies to this page.
  }
}

/** The current choice and whether the page is dark; follows the system, other tabs and other hook users. */
export function useTheme(): { choice: ThemeChoice; dark: boolean; setChoice: (choice: ThemeChoice) => void } {
  // The server renders "system"; the stored choice is read after mount (the attribute itself was
  // already set by NO_FLASH_SCRIPT, so nothing repaints).
  const [choice, setChoiceState] = React.useState<ThemeChoice>("system");
  const [dark, setDark] = React.useState(false);

  React.useEffect(() => {
    const sync = () => {
      const c = readTheme();
      setChoiceState(c);
      setDark(resolveDark(c));
    };
    sync();
    const media = window.matchMedia(DARK_QUERY);
    const onSystem = () => {
      // Only a System choice changes with the OS; tell the canvas and graphs to recolour.
      if (readTheme() === "system") applyTheme("system");
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === THEME_KEY) applyTheme(readTheme());
    };
    media.addEventListener("change", onSystem);
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      media.removeEventListener("change", onSystem);
      window.removeEventListener(THEME_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const setChoice = React.useCallback((next: ThemeChoice) => {
    storeTheme(next);
    applyTheme(next);
  }, []);

  return { choice, dark, setChoice };
}

/**
 * Inline in <head> (layout.tsx): applies a stored Light or Dark choice before first paint. System
 * needs nothing, since the stylesheet follows `prefers-color-scheme` on its own.
 */
export const NO_FLASH_SCRIPT = `(function(){try{var v=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)});if(v==="light"||v==="dark")document.documentElement.setAttribute("data-theme",v)}catch(e){}})();`;

/**
 * A colour token (lib/theme.css `--c-<token>`) as the browser resolves it right now, for consumers
 * that cannot read CSS variables (Cytoscape, Recharts props): `rgb(r, g, b)`, or `rgba(r, g, b, a)`
 * with `alpha`. The comma form is the one Cytoscape parses. Outside a browser it returns the light
 * value from `LIGHT_FALLBACK`, which no screen paints: the themed consumers wait for the mount.
 */
export function cssColor(token: string, alpha?: number): string {
  let channels = "";
  if (typeof document !== "undefined") {
    channels = getComputedStyle(document.documentElement).getPropertyValue(`--c-${token}`).trim();
    // In a browser every real token resolves; an empty value is a typo that would paint slate.
    if (!channels && process.env.NODE_ENV !== "production") console.warn(`cssColor: unknown colour token "${token}" (no --c-${token})`);
  }
  const [r, g, b] = (channels || LIGHT_FALLBACK).split(/\s+/);
  return alpha === undefined ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const LIGHT_FALLBACK = "66 84 102";

/** Whether the page paints dark right now (the resolved `color-scheme` lib/theme.css sets). */
export function isDarkNow(): boolean {
  return typeof document !== "undefined" && getComputedStyle(document.documentElement).colorScheme === "dark";
}

/**
 * A value built from the resolved theme (colours from `cssColor`, `isDarkNow`), rebuilt on every
 * `themechange` and system change. `null` until mounted, so a consumer never paints a frame in the
 * wrong theme: the server cannot know a System choice and the first client render must match it.
 */
export function useThemed<T>(build: () => T): T | null {
  const [value, setValue] = React.useState<T | null>(null);
  const ref = React.useRef(build);
  ref.current = build;
  React.useEffect(() => {
    const rebuild = () => setValue(() => ref.current());
    rebuild();
    const media = window.matchMedia(DARK_QUERY);
    window.addEventListener(THEME_EVENT, rebuild);
    media.addEventListener("change", rebuild);
    return () => {
      window.removeEventListener(THEME_EVENT, rebuild);
      media.removeEventListener("change", rebuild);
    };
  }, []);
  return value;
}

/** Calls `onChange` after every theme change (for imperative consumers such as a Cytoscape instance). */
export function useThemeChange(onChange: () => void): void {
  const ref = React.useRef(onChange);
  ref.current = onChange;
  React.useEffect(() => {
    const fire = () => ref.current();
    const media = window.matchMedia(DARK_QUERY);
    window.addEventListener(THEME_EVENT, fire);
    media.addEventListener("change", fire);
    return () => {
      window.removeEventListener(THEME_EVENT, fire);
      media.removeEventListener("change", fire);
    };
  }, []);
}

/** The chart colours (Recharts takes them as SVG attributes, which cannot read CSS variables). */
export function chartColors() {
  return {
    grid: cssColor("line"),
    axis: cssColor("slate"),
    series: cssColor("accent"),
    cursor: cssColor("accent-soft"),
    tooltip: { borderRadius: 6, fontSize: 12, borderColor: cssColor("line"), backgroundColor: cssColor("surface"), color: cssColor("ink") },
  };
}
