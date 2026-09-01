"use client";

/**
 * Home — the fleet at rest.
 *
 * The live page answers "is this rollout safe". This one answers the question
 * asked far more often, when nothing is rolling out at all: what state is my
 * fleet actually in? How many devices, how many reachable, which versions are
 * out there, and what has been happening.
 *
 * Deliberately not the live page with fewer widgets. Version fragmentation is
 * the headline here because it is the number that decides whether a rollout is
 * needed in the first place.
 */

import { useMemo, useState } from "react";
import { DeviceDrawer } from "@/components/DeviceDrawer";
import { FleetGrid } from "@/components/FleetGrid";
import { Nav } from "@/components/Nav";
import { VersionDonut } from "@/components/VersionDonut";
import { useConvoy } from "@/lib/useConvoy";

const clock = (ts: string) =>
  new Date(ts).toLocaleTimeString("en-GB", { hour12: false });

/** Numeric version compare. A string sort puts 1.9.0 above 1.10.0. */
const versionCode = (v: string | null) =>
  (v ?? "0.0.0").split(".").reduce((n, p) => n * 1000 + (parseInt(p) || 0), 0);

export default function HomePage() {
  const { devices, campaigns, events, progress, connection } = useConvoy();
  const [selected, setSelected] = useState<string | null>(null);

  const stats = useMemo(() => {
    const online = devices.filter((d) => d.online).length;
    const lowBattery = devices.filter(
      (d) => d.battery !== null && d.battery < 30,
    ).length;
    const weakSignal = devices.filter(
      (d) => d.network_quality !== null && d.network_quality < 2,
    ).length;
    const newest = devices.reduce(
      (best, d) =>
        versionCode(d.current_version) > versionCode(best)
          ? d.current_version ?? best
          : best,
      "0.0.0",
    );
    const onCurrent = devices.filter((d) => d.current_version === newest).length;
    return { online, lowBattery, weakSignal, newest, onCurrent };
  }, [devices]);

  const active = campaigns.find((c) => c.state === "RUNNING");
  const recent = campaigns.slice(0, 5);

  return (
    <main className="mx-auto max-w-[1600px] px-6 py-5">
      <Nav
        right={
          <span className="flex items-center gap-2 font-mono text-legend text-ink-mute">
            <span
              className="h-[7px] w-[7px] rounded-full"
              style={{
                background:
                  connection === "live" ? "var(--verified)" : "var(--govern)",
              }}
            />
            {connection === "live" ? "live" : "reconnecting…"}
          </span>
        }
      />

      <section className="panel mb-3 flex flex-wrap items-end justify-between gap-8 px-6 py-5">
        <div>
          <div className="legend mb-1">Fleet</div>
          <div className="font-display text-state font-bold">{devices.length}</div>
        </div>
        <Figure
          label="Reachable"
          value={`${stats.online}/${devices.length}`}
          color={
            devices.length && stats.online === devices.length
              ? "var(--verified)"
              : "var(--govern)"
          }
        />
        <Figure
          label={`On ${stats.newest}`}
          value={`${stats.onCurrent}/${devices.length}`}
        />
        <Figure
          label="Low battery"
          value={String(stats.lowBattery)}
          color={stats.lowBattery ? "var(--fault)" : undefined}
        />
        <Figure
          label="Weak signal"
          value={String(stats.weakSignal)}
          color={stats.weakSignal ? "var(--fault)" : undefined}
        />
        <div>
          <div className="legend mb-1">Rollout</div>
          <div
            className="font-mono text-figure font-medium"
            style={{ color: active ? "var(--transit)" : "var(--ink-mute)" }}
          >
            {active ? "in progress" : "idle"}
          </div>
          {active && (
            <a href="/live" className="font-mono text-[11px] text-ink-mute underline">
              {active.name} →
            </a>
          )}
        </div>
      </section>

      <div className="mb-3 grid gap-3 lg:grid-cols-2">
        <VersionDonut devices={devices} />

        <section className="panel p-4">
          <div className="legend mb-3">Recent campaigns</div>
          {recent.length === 0 ? (
            <p className="text-body text-ink-mute">
              No campaigns yet. Publish a firmware version to get started.
            </p>
          ) : (
            <table className="w-full font-mono text-data">
              <thead>
                <tr className="border-b border-rule text-left">
                  <th className="legend py-1 pr-3 font-normal">Name</th>
                  <th className="legend py-1 pr-3 font-normal">State</th>
                  <th className="legend py-1 pr-3 font-normal">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((c) => {
                  const ok =
                    (c.counts?.SUCCEEDED ?? 0) + (c.counts?.ROLLED_BACK ?? 0);
                  const failed = c.counts?.FAILED ?? 0;
                  return (
                    <tr key={c.campaign_id} className="border-b border-rule/50">
                      <td className="py-[6px] pr-3">
                        {c.name}
                        {(c as { is_rollback?: boolean }).is_rollback && (
                          <span style={{ color: "var(--govern)" }}> ↓</span>
                        )}
                      </td>
                      <td
                        className="py-[6px] pr-3"
                        style={{
                          color:
                            c.state === "ABORTED"
                              ? "var(--fault)"
                              : c.state === "RUNNING"
                                ? "var(--transit)"
                                : "var(--ink-mute)",
                        }}
                      >
                        {c.state}
                      </td>
                      <td className="py-[6px] pr-3 text-ink-mute">
                        {ok} ok
                        {failed > 0 && (
                          <span style={{ color: "var(--fault)" }}> · {failed} failed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <div className="mb-3">
        <FleetGrid devices={devices} progress={progress} onSelect={setSelected} />
      </div>

      <section className="panel p-4">
        <div className="legend mb-3">Recent activity</div>
        <div className="space-y-[4px]">
          {events.length === 0 && (
            <p className="text-body text-ink-mute">Nothing recorded yet.</p>
          )}
          {events.slice(0, 15).map((e) => (
            <div key={e.id} className="flex gap-3 font-mono text-[11px]">
              <span className="text-ink-mute">{clock(e.ts)}</span>
              <button
                onClick={() => setSelected(e.device_id)}
                className="underline decoration-dotted"
              >
                {e.device_id}
              </button>
              <span
                style={{
                  color: e.reason_code?.startsWith("FAILED")
                    ? "var(--fault)"
                    : "var(--ink-mute)",
                }}
              >
                {e.reason_code ?? e.event_type}
              </span>
            </div>
          ))}
        </div>
      </section>

      <DeviceDrawer deviceId={selected} onClose={() => setSelected(null)} />
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
      <div className="font-mono text-big font-medium" style={{ color }}>
        {value}
      </div>
    </div>
  );
}