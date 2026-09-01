"use client";

import type { Device } from "@/lib/api";

/**
 * Version fragmentation.
 *
 * The question every fleet operator actually has: how much of my fleet is on
 * what? A donut rather than a bar chart because the meaningful quantity is the
 * SHARE of the whole -- "two thirds are current" is the insight, not the
 * absolute count -- and because a fleet split across many versions looks
 * visibly fragmented in a way a bar chart flattens.
 */

const PALETTE = [
  "var(--verified)",
  "var(--transit)",
  "var(--govern)",
  "var(--dormant)",
  "var(--fault)",
];

export function VersionDonut({ devices }: { devices: Device[] }) {
  const counts = devices.reduce<Record<string, number>>((acc, d) => {
    const v = d.current_version ?? "unknown";
    acc[v] = (acc[v] ?? 0) + 1;
    return acc;
  }, {});
  const entries = Object.entries(counts).sort((a, b) => {
    // Newest version first, so the "current" slice leads. Numeric compare, not
    // lexicographic: "1.10.0" is newer than "1.9.0" and a string sort disagrees.
    const num = (v: string) =>
      v.split(".").reduce((n, part) => n * 1000 + (parseInt(part) || 0), 0);
    return num(b[0]) - num(a[0]);
  });

  const total = devices.length;
  if (!total) {
    return (
      <section className="panel p-4">
        <div className="legend mb-2">Version spread</div>
        <p className="text-body text-ink-mute">No devices connected.</p>
      </section>
    );
  }

  const R = 52;
  const C = 2 * Math.PI * R;
  let offset = 0;

  return (
    <section className="panel p-4">
      <div className="legend mb-3">Version spread</div>
      <div className="flex items-center gap-6">
        <svg width="130" height="130" viewBox="0 0 130 130" role="img"
             aria-label={`${entries.length} versions across ${total} devices`}>
          <g transform="rotate(-90 65 65)">
            {entries.map(([version, n], i) => {
              const frac = n / total;
              const dash = `${frac * C} ${C - frac * C}`;
              const el = (
                <circle
                  key={version}
                  cx="65" cy="65" r={R}
                  fill="none"
                  stroke={PALETTE[i % PALETTE.length]}
                  strokeWidth="18"
                  strokeDasharray={dash}
                  strokeDashoffset={-offset * C}
                />
              );
              offset += frac;
              return el;
            })}
          </g>
          <text x="65" y="62" textAnchor="middle"
                className="font-mono" fontSize="22" fill="var(--ink)">
            {entries.length}
          </text>
          <text x="65" y="78" textAnchor="middle"
                className="font-mono" fontSize="9" fill="var(--ink-mute)">
            versions
          </text>
        </svg>

        <div className="flex-1 space-y-1">
          {entries.map(([version, n], i) => (
            <div key={version} className="flex items-center gap-2 font-mono text-data">
              <span className="h-[9px] w-[9px] shrink-0"
                    style={{ background: PALETTE[i % PALETTE.length] }} />
              <span className="flex-1">{version}</span>
              <span className="text-ink-mute">
                {n} · {Math.round((n / total) * 100)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}