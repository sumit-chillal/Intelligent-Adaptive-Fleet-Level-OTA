"use client";

import { motion } from "motion/react";
import { usePathname } from "next/navigation";
import { ConvoyLogo } from "@/components/ConvoyLogo";

/**
 * Navigation, with the wordmark from the reference: CONVOY set wide, with a
 * tyre sitting beneath it.
 *
 * Three destinations, because there are three questions an operator asks —
 * how is the fleet, what is happening now, what happened before. More routes
 * than questions is how dashboards become mazes.
 *
 * The active indicator is a single shared element that slides between items
 * rather than one indicator per link appearing and disappearing. Motion's
 * layoutId does the interpolation, and the effect is that the eye tracks one
 * object moving instead of re-finding a new one.
 */

const LINKS = [
  { href: "/dashboard", label: "Dashboard", icon: HomeMark },
  { href: "/live", label: "Campaign", icon: FlagMark },
  { href: "/analytics", label: "Analytics", icon: ChartMark },
];

export function Nav({ right }: { right?: React.ReactNode }) {
  const path = usePathname();
  return (
    <header className="mb-5 flex flex-wrap items-center justify-between gap-4 border-b border-rule pb-4">
      <div className="flex items-center gap-9">
        <a href="/" aria-label="Convoy" className="block">
          <ConvoyLogo size={19} cut="var(--panel)" />
        </a>

        <nav className="flex gap-1">
          {LINKS.map((l) => {
            const active = path === l.href;
            const Icon = l.icon;
            return (
              <a
                key={l.href}
                href={l.href}
                className="relative flex items-center gap-2 rounded-xl px-3 py-2 font-mono text-data transition-colors"
                style={{ color: active ? "var(--ink)" : "var(--ink-mute)" }}
              >
                {active && (
                  <motion.span
                    layoutId="nav-active"
                    className="absolute inset-0 rounded-xl"
                    style={{ background: "var(--concrete)" }}
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                  />
                )}
                <span className="relative flex items-center gap-2">
                  <Icon />
                  {l.label}
                </span>
              </a>
            );
          })}
        </nav>
      </div>

      <div className="flex items-center gap-4">{right}</div>
    </header>
  );
}

function HomeMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M2 7 L8 2 L14 7 V14 H2 Z" stroke="currentColor"
            strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function FlagMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M4 2 V14" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" />
      <path d="M4 3 L13 3 L10.5 6 L13 9 L4 9 Z" stroke="currentColor"
            strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function ChartMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M2 14 V9 M6.5 14 V5 M11 14 V11 M15 14 V2" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}
