"use client";

/**
 * Landing.
 *
 * The car is the page. It sits centred with the wordmark above it and the
 * routes below, because the object is the subject — a headline beside it would
 * compete with the only thing on the page worth looking at, and a visitor who
 * has just seen a car on a lit turntable does not need to be told in forty-
 * point type what the project is about.
 *
 * Everything that explains sits below the fold, where someone who wants it
 * will scroll for it.
 */

import { motion } from "motion/react";
import dynamic from "next/dynamic";

import { ConvoyLogo } from "@/components/ConvoyLogo";

const CarShowcase = dynamic(
  () => import("@/components/CarShowcase").then((m) => m.CarShowcase),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-[clamp(380px,58vh,660px)] w-full items-center justify-center rounded-xl border border-rule bg-concrete">
        <span className="legend">preparing the showroom…</span>
      </div>
    ),
  },
);

const POINTS = [
  {
    title: "Adaptive, not scheduled",
    body: "Batch size responds to what actually happens. A failure contracts the rollout within seconds; clean batches earn it back.",
  },
  {
    title: "Signed at the source",
    body: "Every device verifies an Ed25519 manifest before a byte reaches flash. An attacker holding the network still cannot install firmware.",
  },
  {
    title: "Reversible by design",
    body: "Updates land in an inactive partition and must prove themselves. One that cannot is reverted without anyone touching the vehicle.",
  },
];

const FIGURES: [string, string][] = [
  ["18", "devices"],
  ["4", "networks"],
  ["2", "implementations"],
  ["97s", "full fleet rollout"],
];

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      <section className="mx-auto max-w-[1600px] px-6 pt-9">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
          className="mb-7 flex flex-col items-center"
        >
          <ConvoyLogo size={76} tagline />
          <p className="mt-4 max-w-[42rem] text-center text-lead text-ink-mute">
            Adaptive fleet-level firmware delivery over an untrusted network.
            Eighteen devices, two implementations, four networks, one protocol.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.985 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, ease: [0.2, 0.8, 0.2, 1] }}
        >
          <CarShowcase />
        </motion.div>

        {/* One primary action, two secondary.
            
            Three buttons of equal weight is not a choice, it is a menu — the
            reader has to evaluate all three before doing anything. Making
            "Open dashboard" solid and the others quiet means the page has an
            obvious default and two ways out of it. */}
        <div className="mt-9 flex flex-wrap items-stretch justify-center gap-2">
          <Action href="/dashboard" primary>
            Open dashboard
          </Action>
          <Action href="/live">Watch a rollout</Action>
          <Action href="/analytics">Analytics</Action>
        </div>

        {/* Hairlines between the figures rather than wide gaps: four numbers
            spaced apart read as four unrelated facts, four numbers in a ruled
            row read as one measurement of one system. */}
        <div className="mx-auto mt-12 flex max-w-[56rem] flex-wrap justify-center">
          {FIGURES.map(([n, l], i) => (
            <motion.div
              key={l}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 + i * 0.06, duration: 0.35 }}
              className="flex-1 px-7 text-center"
              style={{
                minWidth: "9rem",
                borderLeft: i === 0 ? "none" : "1px solid var(--rule)",
              }}
            >
              <div className="font-mono text-figure font-medium">{n}</div>
              <div className="legend mt-1">{l}</div>
            </motion.div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-[1600px] px-6 py-16">
        <div className="grid gap-3 md:grid-cols-3">
          {POINTS.map((p, i) => (
            <motion.article
              key={p.title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ delay: i * 0.08, duration: 0.4 }}
              whileHover={{ y: -4 }}
              className="panel rounded-xl p-5"
            >
              <div className="legend mb-2">{String(i + 1).padStart(2, "0")}</div>
              <h2 className="mb-2 font-display text-lead font-bold">{p.title}</h2>
              <p className="text-body text-ink-mute">{p.body}</p>
            </motion.article>
          ))}
        </div>
      </section>

      <footer className="mx-auto max-w-[1600px] border-t border-rule px-6 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="legend">Convoy · adaptive fleet OTA</span>
          <span className="font-mono text-[11px] text-ink-mute">
            smarter data, better world
          </span>
        </div>
      </footer>
    </main>
  );
}

/**
 * The wordmark from the reference: CONVOY in a wide display face with a tyre
 * tucked beneath the centre, its tread visible, and the road running out to
 * either side.
 *
 * Drawn rather than imported. Six shapes do not justify an asset pipeline, and
 * an inline SVG inherits currentColor, so the mark follows the theme instead of
 * needing its own colour rules.
 */

/**
 * A call to action.
 *
 * `primary` is filled, everything else is outlined. The distinction is carried
 * by fill rather than by colour so it survives a projector with poor contrast,
 * which is where this page will actually be seen.
 */
function Action({
  href,
  children,
  primary = false,
}: {
  href: string;
  children: React.ReactNode;
  primary?: boolean;
}) {
  return (
    <motion.a
      href={href}
      whileHover={{ y: -2 }}
      whileTap={{ y: 0, scale: 0.985 }}
      transition={{ type: "spring", stiffness: 420, damping: 30 }}
      className="group inline-flex items-center gap-3 rounded-xl border px-7 py-[13px] font-mono text-data transition-colors"
      style={
        primary
          ? {
              background: "var(--ink)",
              color: "var(--panel)",
              borderColor: "var(--ink)",
            }
          : { borderColor: "var(--rule)" }
      }
    >
      {children}
      {primary && (
        <svg
          width="15"
          height="15"
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
          className="transition-transform group-hover:translate-x-[3px]"
        >
          <path
            d="M2.5 8 H13 M9 4 L13 8 L9 12"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </motion.a>
  );
}
