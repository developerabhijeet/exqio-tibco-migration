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
        warm: "#F4F3EE",
        page: "#FAF9F7",
        line: "#E2DFD6",
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
        sans: ["var(--font-poppins)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { card: "12px", btn: "6px", node: "10px" },
      fontSize: {
        "screen-title": ["32px", { lineHeight: "1.25", fontWeight: "600" }],
        "panel-title": ["22px", { lineHeight: "1.3", fontWeight: "600" }],
        body: ["16px", { lineHeight: "1.6" }],
        eyebrow: ["11px", { lineHeight: "1.4", letterSpacing: "0.08em", fontWeight: "600" }],
      },
    },
  },
  plugins: [],
};

export default config;
