"use client";

/**
 * The fleet, grouped by what kind of device it is.
 *
 * A simulated container and a physical board are interchangeable to the
 * SERVER — same protocol, same manifests, same reason codes — and that
 * indifference is the point of the project. But it is a claim about the server,
 * not a claim about the operator: mixed into one grid, eighteen tiles read as
 * an undifferentiated wall, and "13 of 18 updated" hides whether the boards or
 * the containers were the ones that lagged.
 *
 * Grouping states the equivalence more clearly than mixing does, because it
 * puts the two families side by side under the same headings, each with its
 * own count, and lets the reader see they behave identically.
 */

import type { Device } from "@/lib/api";
import type { Progress } from "@/lib/useConvoy";
import { DeviceCard } from "./DeviceCard";

interface Props {
  devices: Device[];
  progress: Record<string, Progress>;
  targetVersion?: string | null;
  targetStates?: Record<string, string>;
  onSelect?: (deviceId: string) => void;
  /** Restrict to one family. Omit to show every group. */
  onlyModel?: string;
}

/** Display names, so the interface never shows a raw model string. */
export const FAMILY: Record<string, { label: string; note: string }> = {
  "tcu-sim-v1": {
    label: "Simulated TCUs",
    note: "Docker containers across three laptops",
  },
  "esp32-tcu-v1": {
    label: "ESP32 boards",
    note: "physical hardware on separate networks",
  },
};

/** Simulators first, then hardware, then anything unrecognised. */
export const FAMILY_ORDER = ["tcu-sim-v1", "esp32-tcu-v1"];

export function groupByFamily(devices: Device[]): [string, Device[]][] {
  const groups = new Map<string, Device[]>();
  for (const d of devices) {
    const key = d.model ?? "unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(d);
  }
  const keys = [
    ...FAMILY_ORDER.filter((k) => groups.has(k)),
    // Unrecognised models are listed rather than dropped: a device the UI does
    // not know about is exactly the one worth noticing.
    ...[...groups.keys()].filter((k) => !FAMILY_ORDER.includes(k)),
  ];
  return keys.map((k) => [
    k,
    groups.get(k)!.sort((a, b) => a.device_id.localeCompare(b.device_id)),
  ]);
}

export function FleetGrid({
  devices,
  progress,
  targetVersion,
  targetStates = {},
  onSelect,
  onlyModel,
}: Props) {
  const pool = onlyModel ? devices.filter((d) => d.model === onlyModel) : devices;

  if (!pool.length) {
    return (
      <section className="panel px-5 py-8">
        <div className="legend mb-2">Fleet</div>
        <p className="text-body text-ink-mute">
          No devices connected. Start a container or power on a board — they
          appear here within a second of connecting.
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-3">
      {groupByFamily(pool).map(([model, list]) => {
        const online = list.filter((d) => d.online).length;
        const family = FAMILY[model] ?? { label: model, note: "" };

        return (
          <section key={model} className="panel p-4" aria-label={family.label}>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <span className="legend">{family.label}</span>
                {family.note && (
                  <span className="ml-3 font-mono text-[11px] text-ink-mute">
                    {family.note}
                  </span>
                )}
              </div>
              <span className="font-mono text-legend text-ink-mute">
                {online}/{list.length} online
              </span>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
              {list.map((device) => (
                <DeviceCard
                  key={device.device_id}
                  device={device}
                  progress={progress[device.device_id]}
                  targetVersion={targetVersion}
                  targetState={targetStates[device.device_id]}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
