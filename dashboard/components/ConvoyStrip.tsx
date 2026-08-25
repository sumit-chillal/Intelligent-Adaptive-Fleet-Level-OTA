"use client";

/**
 * CONVOY STRIP — the signature element.
 *
 * Each batch is a segment whose WIDTH IS ITS BATCH SIZE, laid left to right in
 * execution order, filled with one square per device coloured by outcome.
 *
 * When the adaptive engine shrinks the batch, the next segment is visibly
 * narrower. The intellectual core of the project — a control loop reacting to
 * live failure rates — becomes a shape you can read from the back of a lecture
 * hall, with no chart-reading required.
 *
 * The obvious alternative was a progress bar plus a line chart of batch size.
 * A progress bar shows how far along the rollout is; this shows how the system
 * is THINKING, which is the thing worth looking at.
 */

import type { Batch, Decision, Target } from "@/lib/api";

interface Props {
  batches: (Batch & { id?: number })[];
  decisions: Decision[];
  targets: Target[];
}

const OUTCOME_COLOR: Record<string, string> = {
  SUCCEEDED: "var(--verified)",
  FAILED: "var(--fault)",
  SKIPPED: "var(--dormant)",
  ROLLED_BACK: "var(--govern)",
  DOWNLOADING: "var(--transit)",
  INSTALLING: "var(--transit)",
  OFFERED: "var(--transit)",
  PENDING: "var(--rule)",
};

export function ConvoyStrip({ batches, decisions, targets }: Props) {
  if (!batches.length) {
    return (
      <div className="panel px-5 py-8">
        <div className="legend mb-2">Convoy strip</div>
        <p className="text-body text-ink-mute">
          No batches yet. The strip fills in as the rollout runs, one segment
          per batch, each as wide as its batch size.
        </p>
      </div>
    );
  }

  const totalWidth = batches.reduce((sum, b) => sum + Math.max(b.actual_size, 1), 0);
  const decisionFor = (index: number) =>
    decisions.find((d) => d.batch_index === index);

  return (
    <section className="panel px-5 py-4" aria-label="Batch timeline">
      <div className="legend mb-3">Convoy strip</div>

      <div className="flex items-stretch gap-[2px]">
        {batches.map((batch) => {
          const size = Math.max(batch.actual_size, 1);
          const share = (size / totalWidth) * 100;
          const decision = decisionFor(batch.index);
          const shrank =
            decision && decision.new_batch_size < decision.prev_batch_size;
          const grew =
            decision && decision.new_batch_size > decision.prev_batch_size;

          const members = targets.filter((t) => t.batch_id === batch.id);
          const ok = members.filter(
            (t) => t.state === "SUCCEEDED" || t.state === "ROLLED_BACK",
          ).length;
          const failed = members.filter((t) => t.state === "FAILED").length;
          const skipped = members.filter((t) => t.state === "SKIPPED").length;

          return (
            <div
              key={batch.index}
              style={{ width: `${share}%`, minWidth: 44 }}
              className="flex flex-col"
            >
              <div className="flex items-baseline justify-between px-1">
                <span className="font-mono text-legend text-ink-mute">
                  {String(batch.index).padStart(2, "0")}
                </span>
                {batch.is_canary && (
                  <span className="font-mono text-legend text-ink-mute">canary</span>
                )}
              </div>

              <div className="mt-1 flex flex-wrap content-start gap-[3px] border border-rule bg-concrete p-[5px] min-h-[46px]">
                {/* One square per DEVICE, coloured by that device's own
                    outcome. Colouring by the batch's tallies instead would
                    draw whatever the counters say -- which is how a batch of
                    five came to render six squares. */}
                {members.map((t) => (
                  <span
                    key={t.device_id}
                    className="h-[9px] w-[9px]"
                    style={{ background: OUTCOME_COLOR[t.state] ?? OUTCOME_COLOR.PENDING }}
                    title={`${t.device_id} — ${t.reason_code ?? t.state.toLowerCase()}`}
                  />
                ))}
              </div>

              <div className="px-1 pt-1 font-mono text-[10px] leading-tight text-ink-mute">
                {/* Counted from the SAME device records that draw the squares
                    above, not from the batch's stored counters.
                    
                    Two views of one batch must never be able to disagree: the
                    squares said five devices while the counters said six, and
                    a viewer has no way to know which to believe. Deriving both
                    from one source removes the question. (The stored counters
                    were double-counting rejected offers; that is fixed, but
                    rows written before the fix are still in the database, and
                    the display should not repeat their error.)
                    
                    Built as a list and joined, so a batch with no successes
                    never renders a stray leading separator. */}
                {[
                  ok > 0 ? `${ok} ok` : null,
                  failed > 0 ? `${failed} failed` : null,
                  skipped > 0 ? `${skipped} skipped` : null,
                  !batch.closed_at ? "in flight" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>

              {(shrank || grew) && decision && (
                <div className="decision-stamp mt-1 border-l-2 px-1 py-[3px]"
                     style={{ borderColor: "var(--govern)" }}>
                  <div
                    className="decision-sweep h-[2px] w-full"
                    style={{ background: "var(--govern)" }}
                  />
                  <div
                    className="pt-1 font-mono text-[10px] leading-tight"
                    style={{ color: "var(--govern)" }}
                  >
                    {/* A shrink is explained by the failure rate that caused
                        it. A growth is explained by the clean run that earned
                        it -- labelling that "0% failure" states the arithmetic
                        rather than the reason. */}
                    {shrank
                      ? `${(decision.observed_failure_rate * 100).toFixed(0)}% failure`
                      : "clean run"}
                    <br />
                    {decision.prev_batch_size} → {decision.new_batch_size}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}