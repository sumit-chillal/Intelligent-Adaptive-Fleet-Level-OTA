"use client";

import { usePathname } from "next/navigation";

/**
 * Shared navigation.
 *
 * Three destinations, because there are three questions an operator asks:
 * how is the fleet (Home), what is happening right now (Live), and what
 * happened before (Analytics). More pages than questions is how dashboards
 * become mazes.
 */
const LINKS = [
  { href: "/", label: "Home" },
  { href: "/live", label: "Live" },
  { href: "/analytics", label: "Analytics" },
];

export function Nav({ right }: { right?: React.ReactNode }) {
  const path = usePathname();
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 pb-4">
      <div className="flex items-baseline gap-6">
        <a href="/" className="font-display text-lead font-bold tracking-[0.18em]">
          CONVOY
        </a>
        <nav className="flex gap-4">
          {LINKS.map((l) => {
            const active = path === l.href;
            return (
              <a
                key={l.href}
                href={l.href}
                className="font-mono text-data"
                style={{
                  color: active ? "var(--ink)" : "var(--ink-mute)",
                  borderBottom: active ? "2px solid var(--ink)" : "2px solid transparent",
                  paddingBottom: 2,
                }}
              >
                {l.label}
              </a>
            );
          })}
        </nav>
      </div>
      <div className="flex items-center gap-4">{right}</div>
    </header>
  );
}