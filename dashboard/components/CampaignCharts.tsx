"use client";

/**
 * Three charts that answer questions the summary numbers cannot.
 *
 * A note on where the data comes from, because it constrains what these can
 * honestly show. Campaign detail carries `batches` (opened_at / closed_at),
 * `targets` (outcome, batch, chunk reached) and `decisions`. It does NOT carry
 * a per-device offer or per-chunk timestamp, so the timeline below resolves to
 * BATCH granularity, not millisecond granularity.
 *
 * That is a real limitation and it is stated on the chart rather than hidden by
 * drawing bars that imply a precision the data does not have. Inventing plausible
 * sub-batch timings would make a prettier picture and a dishonest one.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Batch, Campaign, Target } from "@/lib/api";

const MONO = "var(--font-mono)";
const axis = { fontSize: 11, fontFamily: MONO, fill: "var(--ink-mute)" };

const OUTCOME_COLOUR: Record<string, string> = {
  SUCCEEDED: "var(--verified)",
  ROLLED_BACK: "var(--govern)",
  FAILED: "var(--fault)",
  SKIPPED: "var(--dormant)",
  PENDING: "var(--rule)",
};

function outcomeOf(t: Target): keyof typeof OUTCOME_COLOUR {
  if (t.state in OUTCOME_COLOUR) return t.state as keyof typeof OUTCOME_COLOUR;
  return "PENDING";
}

const secondsBetween = (a: string, b: string) =>
  Math.max(0, (new Date(b).getTime() - new Date(a).getTime()) / 1000);

/* ======================================================== 2.1 timeline === */

/**
 * When each device was worked on, and for how long.
 *
 * Every other chart in the project is a summary taken after the fact, yet the
 * entire argument is about PACING — a canary alone, then widening, then
 * contracting on failure. Without a time axis the reader has to take the
 * sequencing on trust.
 *
 * One lane per device, bars spanning the batch that carried it. The vertical
 * gaps between batches are the cooldowns; the first lane sitting alone is the
 * canary; a lane ending in red is a device the campaign stopped for.
 */
export function DeliveryTimeline({ campaign }: { campaign: Campaign }) {
  const batches = campaign.batches ?? [];
  const targets = campaign.targets ?? [];
  if (!batches.length || !targets.length) return null;

  const t0 = batches[0].opened_at;
  const byId = new Map<number, Batch>(batches.map((b) => [b.id, b]));

  // Order lanes by when work started, then by id. Sorting by device name would
  // scatter each batch across the chart and destroy the staircase that makes
  // the pacing visible.
  const rows = targets
    .filter((t) => t.batch_id !== null && byId.has(t.batch_id))
    .map((t) => {
      const b = byId.get(t.batch_id!)!;
      const start = secondsBetween(t0, b.opened_at);
      const end = b.closed_at ? secondsBetween(t0, b.closed_at) : start + 2;
      return {
        device: t.device_id,
        batch: b.index,
        canary: b.is_canary,
        offset: start,
        span: Math.max(end - start, 1.5),
        outcome: outcomeOf(t),
        reason: t.reason_code,
        chunks: t.last_chunk_index + 1,
      };
    })
    .sort((a, b) => a.offset - b.offset || a.device.localeCompare(b.device));

  if (!rows.length) return null;

  const total = Math.max(...rows.map((r) => r.offset + r.span));
  const boundaries = batches
    .map((b) => secondsBetween(t0, b.opened_at))
    .filter((s) => s > 0);

  return (
    <section className="panel rounded-xl p-4">
      <div className="mb-1 legend">Delivery timeline</div>
      <p className="mb-3 max-w-[46rem] text-[11px] text-ink-mute">
        One lane per device, spanning the batch that carried it. Vertical rules
        are batch boundaries. Resolution is per batch, not per chunk — the API
        does not record per-device transfer timestamps.
      </p>

      <ResponsiveContainer width="100%" height={Math.max(220, rows.length * 22)}>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 24, bottom: 18, left: 4 }}
          barCategoryGap={3}
        >
          <CartesianGrid horizontal={false} stroke="var(--rule)" strokeOpacity={0.35} />
          <XAxis
            type="number"
            domain={[0, Math.ceil(total * 1.02)]}
            tick={axis}
            stroke="var(--rule)"
            label={{
              value: "seconds since campaign start",
              position: "insideBottom",
              offset: -10,
              style: { ...axis, fontSize: 10 },
            }}
          />
          <YAxis
            type="category"
            dataKey="device"
            width={104}
            tick={axis}
            stroke="var(--rule)"
            interval={0}
          />
          <Tooltip
            cursor={{ fill: "var(--concrete)", opacity: 0.5 }}
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--rule)",
              borderRadius: 10,
              fontFamily: MONO,
              fontSize: 11,
            }}
            formatter={(_v, _n, item: { payload?: Record<string, unknown> }) => {
              const p = item?.payload as Record<string, unknown> | undefined;
              if (!p) return ["", ""];
              return [
                `batch ${p.batch}${p.canary ? " (canary)" : ""} · ${p.span}s · ` +
                  `${p.chunks} chunks · ${p.reason ?? p.outcome}`,
                String(p.device),
              ];
            }}
          />
          {/* Transparent spacer positions each bar at its start time. */}
          <Bar dataKey="offset" stackId="t" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="span" stackId="t" isAnimationActive={false} radius={[3, 3, 3, 3]}>
            {rows.map((r, i) => (
              <Cell key={i} fill={OUTCOME_COLOUR[r.outcome]} />
            ))}
          </Bar>
          {boundaries.map((s, i) => (
            <ReferenceLine
              key={i}
              x={s}
              stroke="var(--ink-mute)"
              strokeDasharray="2 3"
              strokeOpacity={0.55}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>

      <Key
        items={[
          ["Succeeded", "var(--verified)"],
          ["Rolled back", "var(--govern)"],
          ["Failed", "var(--fault)"],
          ["Skipped", "var(--dormant)"],
        ]}
      />
    </section>
  );
}

/* ========================================================== 2.3 funnel === */

/**
 * Where devices dropped out, stage by stage.
 *
 * This exists to make the TWO-STAGE health check visible, which is the hardest
 * design decision in the project to explain in prose. The server filters on a
 * reading at least a heartbeat old; the device then checks again against its
 * own live reading. A device can pass the first and refuse the second, and on
 * this chart that is a visible step rather than a paragraph.
 */
export function EligibilityFunnel({ campaign }: { campaign: Campaign }) {
  const targets = campaign.targets ?? [];
  if (!targets.length) return null;

  const isSkippedBefore = (t: Target) =>
    t.state === "SKIPPED" && (t.reason_code ?? "").startsWith("SKIPPED_INELIGIBLE");
  const refusedByDevice = (t: Target) =>
    (t.reason_code ?? "").startsWith("FAILED_LOW_BATTERY") ||
    (t.reason_code ?? "").startsWith("FAILED_POOR_NETWORK");

  const targeted = targets.length;
  const passedServer = targets.filter((t) => !isSkippedBefore(t)).length;
  const accepted = targets.filter(
    (t) => !isSkippedBefore(t) && !refusedByDevice(t),
  ).length;
  const transferred = targets.filter((t) => t.last_chunk_index >= 0).length;
  const confirmed = targets.filter(
    (t) => t.state === "SUCCEEDED" || t.state === "ROLLED_BACK",
  ).length;

  const stages = [
    { label: "Targeted", n: targeted, note: "selected by the campaign" },
    { label: "Passed server gate", n: passedServer, note: "battery and signal, as last reported" },
    { label: "Accepted by device", n: accepted, note: "device checked its own live reading" },
    { label: "Transferred", n: transferred, note: "received at least one chunk" },
    { label: "Confirmed", n: confirmed, note: "installed and reported back" },
  ];

  return (
    <section className="panel rounded-xl p-4">
      <div className="mb-1 legend">Eligibility funnel</div>
      <p className="mb-4 max-w-[46rem] text-[11px] text-ink-mute">
        Two health checks, not one. The server filters on a reading at least a
        heartbeat old; the device checks again against its own. A drop at
        “Accepted by device” is a device refusing an update it was offered.
      </p>

      <div className="space-y-[6px]">
        {stages.map((s, i) => {
          const pct = targeted ? (s.n / targeted) * 100 : 0;
          const lost = i > 0 ? stages[i - 1].n - s.n : 0;
          return (
            <div key={s.label} className="flex items-center gap-3">
              <span className="w-[11rem] shrink-0 font-mono text-[11px]">
                {s.label}
              </span>
              <span className="relative h-[26px] flex-1 overflow-hidden rounded-md"
                    style={{ background: "var(--concrete)" }}>
                <span
                  className="absolute inset-y-0 left-0 rounded-md transition-[width] duration-500"
                  style={{
                    width: `${pct}%`,
                    background: i === 0 ? "var(--ink-mute)" : "var(--transit)",
                    opacity: 1 - i * 0.1,
                  }}
                />
                <span className="absolute inset-y-0 left-2 flex items-center font-mono text-[11px]"
                      style={{ color: "var(--panel)" }}>
                  {s.n}
                </span>
              </span>
              <span className="w-[13rem] shrink-0 font-mono text-[10px] text-ink-mute">
                {lost > 0 ? (
                  <span style={{ color: "var(--fault)" }}>−{lost} </span>
                ) : null}
                {s.note}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ============================================== 2.2 small multiples ====== */

/**
 * The same control law, two implementations.
 *
 * The report claims one protocol serves both a Python simulator and C++ on an
 * ESP32. Shown as two panels on IDENTICAL axes, the claim becomes checkable:
 * the batch-size curves have the same shape, arrived at independently by two
 * codebases on different hardware.
 *
 * Shared axes are the whole point. Letting each panel scale to its own data
 * would destroy the only comparison the chart exists to make.
 */
export function ImplementationCompare({
  left,
  right,
}: {
  left: { title: string; campaign: Campaign } | null;
  right: { title: string; campaign: Campaign } | null;
}) {
  if (!left || !right) return null;

  const series = (c: Campaign) =>
    (c.decisions ?? []).map((d) => ({
      batch: d.batch_index,
      size: d.new_batch_size,
      rate: d.observed_failure_rate,
    }));

  const a = series(left.campaign);
  const b = series(right.campaign);
  if (!a.length || !b.length) return null;

  // One domain for both panels.
  const maxBatch = Math.max(...a.concat(b).map((d) => d.batch));
  const maxSize = Math.max(...a.concat(b).map((d) => d.size));

  return (
    <section className="panel rounded-xl p-4">
      <div className="mb-1 legend">Same control law, two implementations</div>
      <p className="mb-3 max-w-[46rem] text-[11px] text-ink-mute">
        Identical axes on both panels. The shapes match because the adaptive
        engine is the same code path; the devices underneath are not.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {[
          { ...left, data: a },
          { ...right, data: b },
        ].map((panel) => (
          <div key={panel.title}>
            <div className="mb-1 font-mono text-[11px] text-ink-mute">
              {panel.title}
            </div>
            <ResponsiveContainer width="100%" height={210}>
              <LineChart
                data={panel.data}
                margin={{ top: 6, right: 10, bottom: 20, left: -14 }}
              >
                <CartesianGrid vertical={false} stroke="var(--rule)" strokeOpacity={0.4} />
                <XAxis
                  dataKey="batch"
                  type="number"
                  domain={[1, maxBatch]}
                  allowDecimals={false}
                  tick={axis}
                  stroke="var(--rule)"
                  label={{
                    value: "batch",
                    position: "insideBottom",
                    offset: -12,
                    style: { ...axis, fontSize: 10 },
                  }}
                />
                <YAxis
                  domain={[0, maxSize]}
                  allowDecimals={false}
                  tick={axis}
                  stroke="var(--rule)"
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)",
                    border: "1px solid var(--rule)",
                    borderRadius: 10,
                    fontFamily: MONO,
                    fontSize: 11,
                  }}
                />
                <Line
                  type="stepAfter"
                  dataKey="size"
                  stroke="var(--ink)"
                  strokeWidth={2.4}
                  dot={{ r: 2.5, fill: "var(--ink)" }}
                  isAnimationActive={false}
                  name="batch size"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ key -- */
/**
 * A shared key.
 *
 * Colour never carries meaning alone here — every outcome also appears as a
 * word. These charts get projected and printed in greyscale, and a legend that
 * only works in colour works in neither.
 */
function Key({ items }: { items: [string, string][] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
      {items.map(([label, colour]) => (
        <span key={label} className="flex items-center gap-[6px] font-mono text-[10px] text-ink-mute">
          <span className="h-[9px] w-[9px] rounded-[2px]" style={{ background: colour }} />
          {label}
        </span>
      ))}
    </div>
  );
}
