"use client";

import { motion } from "motion/react";

/**
 * A single measurement, as a card with its own icon.
 *
 * The stats used to be bare label-and-number pairs in a flex row, which read
 * as a wall of digits with nothing separating one from the next. Giving each
 * a bordered card and a mark makes them individually findable — the eye goes
 * to the battery icon rather than scanning captions.
 *
 * `tone` colours only the number, never the whole card. A card that turns red
 * shouts; a red number states.
 */

interface Props {
  icon: React.ReactNode;
  label: string;
  value: string;
  note?: string;
  tone?: "normal" | "good" | "warn" | "bad";
  delay?: number;
}

const TONE: Record<string, string | undefined> = {
  normal: undefined,
  good: "var(--verified)",
  warn: "var(--govern)",
  bad: "var(--fault)",
};

export function StatCard({ icon, label, value, note, tone = "normal", delay = 0 }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
      className="flex min-w-[9.5rem] flex-1 items-center gap-3 rounded-xl border border-rule bg-panel px-4 py-3"
    >
      <span className="shrink-0 text-ink-mute" style={{ color: TONE[tone] }}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="legend block">{label}</span>
        <span
          className="block font-mono text-figure font-medium leading-tight"
          style={{ color: TONE[tone] }}
        >
          {value}
        </span>
        {note && (
          <span className="block font-mono text-[10px] text-ink-mute">{note}</span>
        )}
      </span>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ marks */
const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.6,
            strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const IconFleet = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <path d="M3 15 l1.6-4.4A2 2 0 0 1 6.5 9.2h11a2 2 0 0 1 1.9 1.4L21 15" />
    <path d="M3 15h18v3H3z" />
    <circle cx="7" cy="18.6" r="1.6" />
    <circle cx="17" cy="18.6" r="1.6" />
  </svg>
);

export const IconSignal = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <path d="M4 20v-4M9.3 20v-8M14.7 20v-12M20 20V4" />
  </svg>
);

export const IconCloud = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <path d="M6.5 18h11a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6-1.3A3.6 3.6 0 0 0 6.5 18z" />
    <path d="M12 15V9.5M12 9.5 9.8 11.7M12 9.5l2.2 2.2" />
  </svg>
);

export const IconBattery = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <rect x="2.5" y="8" width="16" height="8" rx="1.6" />
    <path d="M21 11v2" />
    <path d="M5.5 10.5v3" />
  </svg>
);

export const IconRollout = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <path d="M20 5v5h-5" />
    <path d="M4 19v-5h5" />
    <path d="M19.5 10a7.6 7.6 0 0 0-13-3.1L4 10" />
    <path d="M4.5 14a7.6 7.6 0 0 0 13 3.1L20 14" />
  </svg>
);

export const IconChip = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" {...S}>
    <rect x="7" y="7" width="10" height="10" rx="1.4" />
    <path d="M10 3.5v3M14 3.5v3M10 17.5v3M14 17.5v3M3.5 10h3M3.5 14h3M17.5 10h3M17.5 14h3" />
  </svg>
);
