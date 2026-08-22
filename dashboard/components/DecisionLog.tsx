"use client";

/**
 * Decision log. The running commentary of what the system did and why.
 *
 * aria-live="polite" so a screen reader announces adaptive decisions as they
 * happen rather than leaving them as a silent visual change.
 */

import type { Decision, FleetEvent } from "@/lib/api";

interface Props {
  decisions: Decision[];
  events: FleetEvent[];
}

const clock = (ts: string) =>
  new Date(ts).toLocaleTimeString("en-GB", { hour12: false });

export function DecisionLog({ decisions, events }: Props) {
  const merged = [
    ...decisions.map((d) => ({ kind: "decision" as const, ts: d.ts, d })),
    ...events
      // Connection churn is not rollout news. Fifteen devices reconnecting
      // after a broker blip produces fifteen last-will lines that bury the
      // decisions this panel exists to show. Online/offline is already
      // visible on every tile in the fleet grid.
      .filter(
        (e) =>
          e.event_type !== "HEALTH_SAMPLE" &&
          !(e.event_type === "DEVICE_OFFLINE" && e.reason_code === "last_will") &&
          e.event_type !== "DEVICE_ONLINE",
      )
      .map((e) => ({ kind: "event" as const, ts: e.ts, e })),
  ]
    .sort((a, b) => (a.ts < b.ts ? 1 : -1))
    .slice(0, 40);

  return (
    <section className="panel flex h-full flex-col p-4" aria-label="Decision log">
      <div className="legend mb-3">Decision log</div>
      <div className="flex-1 space-y-[6px] overflow-y-auto" aria-live="polite">
        {merged.length === 0 && (
          <p className="text-body text-ink-mute">
            Nothing yet. Device events and rollout decisions appear here as they
            happen.
          </p>
        )}

        {merged.map((row, i) =>
          row.kind === "decision" ? (
            <div
              key={`d${i}`}
              className="border-l-2 pl-2"
              style={{ borderColor: "var(--govern)" }}
            >
              <div className="font-mono text-[11px] text-ink-mute">
                {clock(row.ts)} batch {String(row.d.batch_index).padStart(2, "0")} closed{" "}
                {row.d.attempted - row.d.failures}/{row.d.attempted}
              </div>
              <div
                className="font-mono text-data font-medium"
                style={{ color: "var(--govern)" }}
              >
                {row.d.reason_code}
                {row.d.prev_batch_size !== row.d.new_batch_size && (
                  <> · batch size {row.d.prev_batch_size} → {row.d.new_batch_size}</>
                )}
              </div>
              {row.d.detail && (
                <div className="text-[11px] text-ink-mute">{row.d.detail}</div>
              )}
            </div>
          ) : (
            <div key={`e${i}`} className="flex gap-2 font-mono text-[11px]">
              <span className="text-ink-mute">{clock(row.ts)}</span>
              <span className="text-ink">{row.e.device_id}</span>
              <span
                style={{
                  color: row.e.reason_code?.startsWith("FAILED")
                    ? "var(--fault)"
                    : "var(--ink-mute)",
                }}
              >
                {row.e.reason_code ?? row.e.event_type}
              </span>
            </div>
          ),
        )}
      </div>
    </section>
  );
}