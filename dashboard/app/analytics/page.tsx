"use client";

/**
 * CONVOY — campaign analytics.
 *
 * The post-mortem view: what the rollout did, and why. Where the live page
 * answers "is this safe right now", this one answers "what happened, and can
 * I defend it to someone who wasn't watching".
 */

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Nav } from "@/components/Nav";
import { api, type Campaign } from "@/lib/api";

export default function AnalyticsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<Campaign | null>(null);

  useEffect(() => {
    void (async () => {
      const list = await api.campaigns();
      setCampaigns(list);
      if (list[0]) setSelected(await api.campaign(list[0].campaign_id));
    })();
  }, []);

  const pick = async (id: string) => setSelected(await api.campaign(id));

  const decisions = selected?.decisions ?? [];
  const targets = selected?.targets ?? [];

  // The step chart's data. Each decision contributes the size BEFORE and AFTER
  // it, so the line steps at the decision point instead of sloping through it.
  const series = decisions.flatMap((d) => [
    { x: d.batch_index - 0.5, size: d.prev_batch_size, batch: d.batch_index },
    { x: d.batch_index + 0.5, size: d.new_batch_size, batch: d.batch_index },
  ]);

  const taxonomy = targets.reduce<Record<string, number>>((acc, t) => {
    if (t.reason_code) acc[t.reason_code] = (acc[t.reason_code] ?? 0) + 1;
    return acc;
  }, {});
  const taxonomyMax = Math.max(1, ...Object.values(taxonomy));

  const duration =
    selected?.started_at && selected?.ended_at
      ? Math.round(
          (new Date(selected.ended_at).getTime() -
            new Date(selected.started_at).getTime()) /
            1000,
        )
      : null;

  return (
    <main className="mx-auto max-w-[1400px] px-6 py-5">
      <Nav />

      <section className="panel mb-3 p-4">
        <div className="legend mb-3">Campaign</div>
        <div className="flex flex-wrap gap-2">
          {campaigns.map((c) => (
            <button
              key={c.campaign_id}
              onClick={() => pick(c.campaign_id)}
              className="border px-3 py-1 text-left font-mono text-data hover:border-ink-mute"
              style={{
                borderColor:
                  selected?.campaign_id === c.campaign_id
                    ? "var(--ink)"
                    : "var(--rule)",
                background:
                  selected?.campaign_id === c.campaign_id
                    ? "var(--concrete)"
                    : "var(--panel)",
              }}
            >
              {c.name}
              <span className="ml-2 text-[11px] text-ink-mute">{c.state}</span>
            </button>
          ))}
        </div>
      </section>

      {!selected ? (
        <div className="panel p-8 text-body text-ink-mute">
          No campaigns yet. Publish firmware and create a campaign to see results
          here.
        </div>
      ) : (
        <>
          <section className="panel mb-3 flex flex-wrap gap-10 px-6 py-5">
            <Figure label="Devices" value={String(targets.length)} />
            <Figure
              label="Updated"
              value={String(
                targets.filter(
                  (t) => t.state === "SUCCEEDED" || t.state === "ROLLED_BACK",
                ).length,
              )}
              color="var(--verified)"
            />
            <Figure
              label="Failed"
              value={String(targets.filter((t) => t.state === "FAILED").length)}
              color="var(--fault)"
            />
            <Figure
              label="Skipped"
              value={String(targets.filter((t) => t.state === "SKIPPED").length)}
            />
            <Figure label="Batches" value={String(selected.batches_completed)} />
            <Figure
              label="Duration"
              value={duration === null ? "—" : `${duration}s`}
            />
          </section>

          {/* --------------------------------------------- batch size chart */}
          <section className="panel mb-3 p-4">
            <div className="legend mb-1">Batch size over time</div>
            <p className="mb-3 text-[11px] text-ink-mute">
              A step, not a curve. Batch size changes discretely at a decision
              point — drawing it as a smooth line would imply sizes the rollout
              never used.
            </p>
            <div style={{ height: 240 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                  <CartesianGrid stroke="var(--rule)" strokeDasharray="2 4" />
                  <XAxis
                    dataKey="x"
                    type="number"
                    domain={[0.5, decisions.length + 0.5]}
                    ticks={decisions.map((d) => d.batch_index)}
                    tickFormatter={(v) => `batch ${v}`}
                    stroke="var(--ink-mute)"
                    tick={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
                  />
                  <YAxis
                    allowDecimals={false}
                    stroke="var(--ink-mute)"
                    tick={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
                    width={32}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--panel)",
                      border: "1px solid var(--rule)",
                      borderRadius: 2,
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                    }}
                    labelFormatter={(v) => `batch ${Math.round(Number(v))}`}
                    formatter={(v) => [v, "batch size"]}
                  />
                  <Line
                    type="stepAfter"
                    dataKey="size"
                    stroke="var(--transit)"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                  {/* Amber marks every point where the engine intervened. */}
                  {decisions
                    .filter((d) => d.prev_batch_size !== d.new_batch_size)
                    .map((d) => (
                      <ReferenceDot
                        key={d.batch_index}
                        x={d.batch_index + 0.5}
                        y={d.new_batch_size}
                        r={5}
                        fill="var(--govern)"
                        stroke="none"
                      />
                    ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <div className="mb-3 grid gap-3 lg:grid-cols-2">
            {/* ----------------------------------------- failure taxonomy */}
            <section className="panel p-4">
              <div className="legend mb-3">Outcome taxonomy</div>
              {Object.keys(taxonomy).length === 0 ? (
                <p className="text-body text-ink-mute">No outcomes recorded yet.</p>
              ) : (
                <div className="space-y-2">
                  {Object.entries(taxonomy)
                    .sort((a, b) => b[1] - a[1])
                    .map(([reason, count]) => {
                      const color = reason.startsWith("FAILED")
                        ? "var(--fault)"
                        : reason.startsWith("SKIPPED")
                          ? "var(--dormant)"
                          : reason.startsWith("ROLLED_BACK")
                            ? "var(--govern)"
                            : "var(--verified)";
                      return (
                        <div key={reason} className="flex items-center gap-3">
                          <span className="w-[15rem] shrink-0 font-mono text-[11px]">
                            {reason}
                          </span>
                          <span className="h-[10px] flex-1 bg-concrete">
                            <span
                              className="block h-full"
                              style={{
                                width: `${(count / taxonomyMax) * 100}%`,
                                background: color,
                              }}
                            />
                          </span>
                          <span className="w-6 text-right font-mono text-data">
                            {count}
                          </span>
                        </div>
                      );
                    })}
                </div>
              )}
            </section>

            {/* ------------------------------------------ decision record */}
            <section className="panel p-4">
              <div className="legend mb-3">Rollout decisions</div>
              <div className="space-y-2">
                {decisions.map((d) => (
                  <div
                    key={d.batch_index}
                    className="border-l-2 pl-2"
                    style={{
                      borderColor:
                        d.prev_batch_size === d.new_batch_size
                          ? "var(--rule)"
                          : "var(--govern)",
                    }}
                  >
                    <div className="font-mono text-[11px] text-ink-mute">
                      batch {String(d.batch_index).padStart(2, "0")} ·{" "}
                      {d.attempted - d.failures}/{d.attempted} ok ·{" "}
                      {(d.observed_failure_rate * 100).toFixed(0)}% failure · ewma{" "}
                      {d.ewma.toFixed(2)}
                    </div>
                    <div className="font-mono text-data">
                      {d.reason_code}
                      {d.prev_batch_size !== d.new_batch_size && (
                        <span style={{ color: "var(--govern)" }}>
                          {" "}
                          · {d.prev_batch_size} → {d.new_batch_size}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>

          {/* ------------------------------------------- per-device outcomes */}
          <section className="panel p-4">
            <div className="mb-3 flex items-baseline justify-between">
              <span className="legend">Per-device outcome</span>
              <button
                onClick={() => downloadCsv(selected)}
                className="border border-rule px-3 py-1 font-mono text-data hover:border-ink-mute"
              >
                Export CSV
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-data">
                <thead>
                  <tr className="border-b border-rule text-left">
                    {["Device", "State", "Reason", "From", "To", "Attempts", "Deferrals"].map(
                      (h) => (
                        <th key={h} className="legend py-1 pr-4 font-normal">
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {targets.map((t) => (
                    <tr key={t.device_id} className="border-b border-rule/50">
                      <td className="py-[6px] pr-4">{t.device_id}</td>
                      <td
                        className="py-[6px] pr-4"
                        style={{
                          color:
                            t.state === "FAILED"
                              ? "var(--fault)"
                              : t.state === "SUCCEEDED"
                                ? "var(--verified)"
                                : "var(--ink-mute)",
                        }}
                      >
                        {t.state}
                      </td>
                      <td className="py-[6px] pr-4 text-ink-mute">
                        {t.reason_code ?? "—"}
                      </td>
                      <td className="py-[6px] pr-4">{t.from_version ?? "—"}</td>
                      <td className="py-[6px] pr-4">{t.to_version ?? "—"}</td>
                      <td className="py-[6px] pr-4">{t.attempts}</td>
                      <td className="py-[6px] pr-4">{t.deferrals}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

function Figure({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div>
      <div className="legend mb-1">{label}</div>
      <div className="font-mono text-figure font-medium" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function downloadCsv(campaign: Campaign) {
  const rows = [
    ["device_id", "state", "reason_code", "from_version", "to_version", "attempts", "deferrals"],
    ...(campaign.targets ?? []).map((t) => [
      t.device_id,
      t.state,
      t.reason_code ?? "",
      t.from_version ?? "",
      t.to_version ?? "",
      String(t.attempts),
      String(t.deferrals),
    ]),
  ];
  const blob = new Blob([rows.map((r) => r.join(",")).join("\n")], {
    type: "text/csv",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${campaign.campaign_id}-outcomes.csv`;
  a.click();
  URL.revokeObjectURL(url);
}