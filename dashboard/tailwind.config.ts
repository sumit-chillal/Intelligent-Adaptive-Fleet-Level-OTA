import type { Config } from "tailwindcss";

// Palette and type come from docs/Design.md — "Hangar Daylight".
// Tokens are declared as CSS variables in globals.css and referenced here, so
// the dark variant is a variable swap rather than a second set of classes.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        concrete: "var(--concrete)",
        panel: "var(--panel)",
        ink: "var(--ink)",
        "ink-mute": "var(--ink-mute)",
        rule: "var(--rule)",
        transit: "var(--transit)",
        verified: "var(--verified)",
        fault: "var(--fault)",
        govern: "var(--govern)",
        dormant: "var(--dormant)",
      },
      fontFamily: {
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        sans: ["var(--font-body)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        // The scale from Design.md. No arbitrary sizes elsewhere.
        legend: ["11px", { lineHeight: "1.2", letterSpacing: "0.14em" }],
        data: ["13px", { lineHeight: "1.4" }],
        body: ["15px", { lineHeight: "1.5" }],
        lead: ["18px", { lineHeight: "1.4" }],
        figure: ["24px", { lineHeight: "1.1" }],
        big: ["40px", { lineHeight: "1" }],
        state: ["72px", { lineHeight: "0.9", letterSpacing: "0.02em" }],
      },
      borderRadius: { none: "0", sm: "2px" },
    },
  },
  plugins: [],
};
export default config;
