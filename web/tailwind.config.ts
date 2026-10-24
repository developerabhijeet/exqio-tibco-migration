import type { Config } from "tailwindcss";

// RSI tokens. The same values live as CSS variables in lib/theme.css for non-Tailwind consumers
// (Cytoscape styles, React Flow edges, Monaco decorations).
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        purple: { DEFAULT: "#9B5DE5", lt: "#F1E9FC", dk: "#7E3FCB" },
        plum: "#20063B",
        coral: "#FF495C",
        // Small uppercase labels need more contrast than the brand coral gives on the warm page
        // background, where 11px at #FF495C sits under the AA threshold for body-sized text.
        "coral-dk": "#D32238",
        warm: "#F4F3EE",
        page: "#FAF9F7",
        line: "#E2DFD6",
        "line-dk": "#CFCABB",
        ink: "#212529",
        slate: "#425466",
        footer: "#140811",
        lilac: "#D9C6F5",
        det: { DEFAULT: "#425466", bg: "#F4F3EE" },
        ai: { DEFAULT: "#0E7A6E", bg: "#E1F3F0" },
        gate: { DEFAULT: "#1E7B3C", bg: "#E2F3E6" },
        decision: { DEFAULT: "#9A5E0A", bg: "#FBF0DF" },
        error: { DEFAULT: "#A33A32", bg: "#F9E5E3" },
      },
      fontFamily: {
        // Inter for everything read as content; Poppins only where the brand speaks (see layout.tsx).
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        display: ["var(--font-poppins)", "var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { card: "12px", btn: "6px", node: "10px" },
      fontSize: {
        "screen-title": ["30px", { lineHeight: "1.2", letterSpacing: "-0.015em", fontWeight: "600" }],
        "panel-title": ["21px", { lineHeight: "1.3", letterSpacing: "-0.01em", fontWeight: "600" }],
        "card-title": ["16.5px", { lineHeight: "1.35", letterSpacing: "-0.005em", fontWeight: "600" }],
        body: ["15px", { lineHeight: "1.6" }],
        // The stepper's step name: readable at a glance without crowding seven of them onto one row.
        step: ["14.5px", { lineHeight: "1.35", letterSpacing: "-0.005em" }],
        eyebrow: ["11px", { lineHeight: "1.4", letterSpacing: "0.07em", fontWeight: "600" }],
      },
      boxShadow: {
        // One elevation for a resting surface and one for anything that floats over it. Enough to
        // separate a card from the page without the drop-shadow look.
        card: "0 1px 2px rgba(32, 6, 59, 0.04), 0 1px 3px rgba(32, 6, 59, 0.05)",
        pop: "0 4px 6px -2px rgba(32, 6, 59, 0.07), 0 12px 24px -6px rgba(32, 6, 59, 0.16)",
        inset: "inset 0 1px 0 rgba(255, 255, 255, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
