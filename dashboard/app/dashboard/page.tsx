"use client";

/**
 * Dashboard — the fleet at rest.
 *
 * The live page answers "is this rollout safe". This one answers the question
 * asked far more often, when nothing is rolling out: what state is my fleet in?
 *
 * Counts are reported PER FAMILY as well as in total. A combined "13 of 18 on
 * the current version" is true and unhelpful: it cannot distinguish a fleet
 * where every board is current and two containers lag from one where the
 * reverse holds, and those call for different action. The families run
 * different firmware on different hardware over different networks, so they
 * are different populations and averaging them hides exactly what an operator
 * needs to see.
 */

import { useMemo, useState } from "react";
import { DeviceDrawer } from "@/components/DeviceDrawer";
import { FAMILY, FleetGrid, groupByFamily } from "@/components/FleetGrid";
import { Nav } from "@/components/Nav";
import {
  IconBattery,
  IconChip,
  IconCloud,
  IconFleet,
  IconRollout,
  IconSignal,
  StatCard,
} from "@/components/StatCard";
import { VersionDonut } from "@/components/VersionDonut";
import { useConvoy } from "@/lib/useConvoy";
import type { Device } from "@/lib/api";

const clock = (ts: string) =>
  new Date(ts).toLocaleTimeString("en-GB", { hour12: false });

/** Numeric version compare. A string sort puts 1.9.0 above 1.10.0. */
const versionCode = (v: string | null) =>
  (v ?? "0.0.0").split(".").reduce((n, p) => n * 1000 + (parseInt(p) || 0), 0);

function familyStats(list: Device[]) {
  const online = list.filter((d) => d.online).length;
  const newest = list.reduce(
    (best, d) =>
      versionCode(d.current_version) > versionCode(best)
        ? d.current_version ?? best
        : best,
    "0.0.0",
  );
  return {
    total: list.length,
    online,
    newest,
    onNewest: list.filter((d) => d.current_version === newest).length,
    lowBattery: list.filter((d) => d.battery !== null && d.battery < 30).length,
    weakSignal: list.filter(
      (d) => d.network_quality !== null && d.network_quality < 2,
    ).length,
  };
}

export default function HomePage() {
  const { devices, campaigns, events, progress, connection } = useConvoy();
  const [selected, setSelected] = useState<string | null>(null);

  const families = useMemo(() => groupByFamily(devices), [devices]);
  const active = campaigns.find((c) => c.state === "RUNNING");

  const { newest, onNewest, lowBattery, weakSignal } = useMemo(() => {
    const newest = devices.reduce(
      (best, d) =>
        versionCode(d.current_version) > versionCode(best)
          ? d.current_version ?? best
          : best,
      "0.0.0",
    );
    return {
      newest,
      onNewest: devices.filter((d) => d.current_version === newest).length,
      lowBattery: devices.filter((d) => d.battery !== null && d.battery < 30).length,
      weakSignal: devices.filter(
        (d) => d.network_quality !== null && d.network_quality < 2,
      ).length,
    };
  }, [devices]);

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

      {/* ----------------------------------------------------- fleet total */}
      <div className="mb-3 flex flex-wrap gap-2">
        <StatCard icon={<IconFleet />} label="Fleet" value={String(devices.length)}
                  note="total devices" delay={0} />
        <StatCard icon={<IconSignal />} label="Reachable"
                  value={`${devices.filter((d) => d.online).length}/${devices.length}`}
                  note="online now"
                  tone={devices.length && devices.every((d) => d.online) ? "good" : "warn"}
                  delay={0.04} />
        <StatCard icon={<IconCloud />} label={`On ${newest}`}
                  value={`${onNewest}/${devices.length}`} note="current version"
                  delay={0.08} />
        <StatCard icon={<IconBattery />} label="Low battery"
                  value={String(lowBattery)} note="below 30%"
                  tone={lowBattery ? "bad" : "normal"} delay={0.12} />
        <StatCard icon={<IconChip />} label="Weak signal"
                  value={String(weakSignal)} note="band 1 or below"
                  tone={weakSignal ? "bad" : "normal"} delay={0.16} />
        <StatCard icon={<IconRollout />} label="Rollout"
                  value={active ? "running" : "idle"}
                  note={active ? active.name : "no active campaign"}
                  tone={active ? "warn" : "normal"} delay={0.2} />
      </div>

      {/* --------------------------------------------------- per family */}
      <div className="mb-3 grid gap-3 lg:grid-cols-2">
        {families.map(([model, list]) => {
          const s = familyStats(list);
          const family = FAMILY[model] ?? { label: model, note: "" };
          return (
            <section key={model} className="panel px-5 py-4">
              <div className="mb-3">
                <span className="legend">{family.label}</span>
                {family.note && (
                  <div className="font-mono text-[11px] text-ink-mute">
                    {family.note}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Cell label="Reachable" value={`${s.online}/${s.total}`}
                      tone={s.online === s.total ? "good" : "warn"} />
                <Cell label={`On ${s.newest}`} value={`${s.onNewest}/${s.total}`} />
                <Cell label="Low battery" value={String(s.lowBattery)}
                      tone={s.lowBattery ? "bad" : "normal"} />
                <Cell label="Weak signal" value={String(s.weakSignal)}
                      tone={s.weakSignal ? "bad" : "normal"} />
              </div>
            </section>
          );
        })}
      </div>

      <div className="mb-3 grid gap-3 lg:grid-cols-2">
        <VersionDonut devices={devices} />

        <section className="panel p-4">
          <div className="legend mb-3">Recent campaigns</div>
          {campaigns.length === 0 ? (
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
                {campaigns.slice(0, 6).map((c) => {
                  const ok =
                    (c.counts?.SUCCEEDED ?? 0) + (c.counts?.ROLLED_BACK ?? 0);
                  const failed = c.counts?.FAILED ?? 0;
                  return (
                    <tr key={c.campaign_id} className="border-b border-rule/50">
                      <td className="py-[6px] pr-3">
                        {c.name}
                        {c.is_rollback && (
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
        <div className="divide-y divide-rule/40">
          {events.length === 0 && (
            <p className="text-body text-ink-mute">Nothing recorded yet.</p>
          )}
          {events.slice(0, 15).map((e) => (
            <div key={e.id}
                 className="flex items-center gap-3 py-[5px] font-mono text-[11px]">
              <span className="w-[4.5rem] shrink-0 text-ink-mute">{clock(e.ts)}</span>
              <button
                onClick={() => setSelected(e.device_id)}
                className="w-[7.5rem] shrink-0 text-left underline decoration-dotted decoration-rule underline-offset-2 hover:decoration-ink"
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

/** A measurement inside a family panel. Bordered, so four of them read as
 *  four things rather than one run-on line. */
function Cell({
  label,
  value,
  tone = "normal",
}: {
  label: string;
  value: string;
  tone?: "normal" | "good" | "warn" | "bad";
}) {
  const color =
    tone === "good" ? "var(--verified)"
    : tone === "warn" ? "var(--govern)"
    : tone === "bad" ? "var(--fault)"
    : undefined;
  return (
    <div className="rounded-xl border border-rule bg-concrete/40 px-3 py-2">
      <div className="legend mb-[2px]">{label}</div>
      <div className="font-mono text-lead font-medium" style={{ color }}>
        {value}
      </div>
    </div>
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
