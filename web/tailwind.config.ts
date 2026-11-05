import type { Config } from "tailwindcss";

// Design tokens (spec section 3). Every colour is a CSS variable of RGB channels defined in
// lib/theme.css for the light theme, the dark theme and the system preference, so one class serves
// both themes and `/40` opacity modifiers keep working. Non-Tailwind consumers (Cytoscape, React
// Flow, Monaco decorations, SVG) read the same variables.
const token = (name: string) => `rgb(var(--c-${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    // The type scale replaces Tailwind's default sizes: no component used those, and one scale
    // keeps the screens consistent. Sizes / line heights in px.
    fontSize: {
      "2xs": ["11px", { lineHeight: "16px" }],
      xs: ["12px", { lineHeight: "18px" }],
      sm: ["13px", { lineHeight: "20px" }],
      md: ["14px", { lineHeight: "22px" }],
      base: ["15px", { lineHeight: "24px" }],
      lg: ["17px", { lineHeight: "26px" }],
      xl: ["19px", { lineHeight: "28px" }],
      "2xl": ["22px", { lineHeight: "30px" }],
      "3xl": ["26px", { lineHeight: "34px" }],
      "4xl": ["30px", { lineHeight: "38px" }],
      // Semantic aliases.
      "screen-title": ["30px", { lineHeight: "38px", letterSpacing: "-0.015em", fontWeight: "600" }],
      "panel-title": ["22px", { lineHeight: "30px", letterSpacing: "-0.01em", fontWeight: "600" }],
      "card-title": ["17px", { lineHeight: "26px", letterSpacing: "-0.005em", fontWeight: "600" }],
      body: ["15px", { lineHeight: "24px" }],
      // The stepper's step name: readable at a glance without crowding seven of them onto one row.
      step: ["14.5px", { lineHeight: "1.35", letterSpacing: "-0.005em" }],
      eyebrow: ["11px", { lineHeight: "16px", letterSpacing: "0.07em", fontWeight: "600" }],
    },
    extend: {
      colors: {
        page: token("page"),
        surface: token("surface"),
        sunken: token("sunken"),
        line: { DEFAULT: token("line"), strong: token("line-strong") },
        ink: token("ink"),
        heading: token("heading"),
        slate: token("slate"),
        accent: { DEFAULT: token("accent"), ink: token("accent-ink"), soft: token("accent-soft"), strong: token("accent-strong") },
        // The brand bar's background, plum in both themes.
        plum: token("plum"),
        coral: token("coral"),
        "coral-dk": token("coral-dk"),
        ai: { DEFAULT: token("ai"), bg: token("ai-bg") },
        gate: { DEFAULT: token("gate"), bg: token("gate-bg") },
        decision: { DEFAULT: token("decision"), bg: token("decision-bg") },
        error: { DEFAULT: token("error"), bg: token("error-bg") },
        det: { DEFAULT: token("det"), bg: token("det-bg") },
        highlight: { DEFAULT: token("highlight"), strong: token("highlight-strong") },
        // Earlier names, aliases of the tokens until the codemod (plan Task 6) removes their uses.
        warm: token("sunken"),
        "line-dk": token("line-strong"),
        purple: { DEFAULT: token("accent"), lt: token("accent-soft"), dk: token("accent-ink") },
        // Brand-only colours: the footer and the brand bar's secondary text.
        footer: "#140811",
        lilac: "#D9C6F5",
        // The memory explorer's three node groups: old code plum, new code teal, knowledge amber.
        teal: { DEFAULT: "#0F766E", lt: "#DDF2EF", dk: "#0B5A54" },
        amber: { DEFAULT: "#B45309", lt: "#FCEFD9", dk: "#8A3F06" },
      },
      fontFamily: {
        // Inter for everything read as content; Poppins only where the brand speaks (see layout.tsx).
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        display: ["var(--font-poppins)", "var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { btn: "8px", node: "10px", card: "14px", panel: "20px" },
      boxShadow: {
        // e1 a resting card, e2 popovers and menus, e3 slide-overs and drawers; the dark theme
        // swaps in black at higher opacity and a 1 px line ring (lib/theme.css).
        e1: "var(--shadow-e1)",
        e2: "var(--shadow-e2)",
        e3: "var(--shadow-e3)",
        card: "var(--shadow-e1)",
        pop: "var(--shadow-e2)",
        inset: "inset 0 1px 0 rgba(255, 255, 255, 0.08)",
      },
      maxWidth: { content: "1600px" },
      transitionDuration: { fast: "var(--dur-fast)", panel: "var(--dur-panel)" },
      transitionTimingFunction: { DEFAULT: "var(--ease)" },
    },
  },
  plugins: [],
};

export default config;
