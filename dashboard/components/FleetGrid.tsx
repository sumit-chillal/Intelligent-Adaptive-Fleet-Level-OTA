"use client";

/**
 * Fleet grid. One tile per device.
 *
 * Every state carries a SHAPE or a label as well as a colour — failed tiles
 * show a cross, verified a filled square. A projector with poor colour
 * calibration, or a colour-blind examiner, must still be able to read the
 * screen (Design.md §7).
 */

import type { Device } from "@/lib/api";
import type { Progress } from "@/lib/useConvoy";

interface Props {
  devices: Device[];
  progress: Record<string, Progress>;
  targetVersion?: string | null;
  targetStates?: Record<string, string>;
  onSelect?: (deviceId: string) => void;
}

function batteryBars(level: number | null) {
  const filled = level === null ? 0 : Math.ceil((level / 100) * 4);
  return (
    <span className="inline-flex items-end gap-[2px]" aria-label={`battery ${level ?? "unknown"}%`}>
      {[1, 2, 3, 4].map((b) => (
        <span
          key={b}
          className="w-[3px]"
          style={{
            height: 3 + b * 2,
            background: b <= filled
              ? level !== null && level < 30 ? "var(--fault)" : "var(--ink-mute)"
              : "var(--rule)",
          }}
        />
      ))}
    </span>
  );
}

export function FleetGrid({ devices, progress, targetVersion, targetStates = {}, onSelect }: Props) {
  if (!devices.length) {
    return (
      <div className="panel px-5 py-8">
        <div className="legend mb-2">Fleet</div>
        <p className="text-body text-ink-mute">
          No devices connected. Start a TCU container or power on a board — they
          appear here within a second of connecting.
        </p>
      </div>
    );
  }

  return (
    <section className="panel p-4" aria-label="Fleet">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="legend">Fleet</span>
        <span className="font-mono text-legend text-ink-mute">
          {devices.filter((d) => d.online).length}/{devices.length} online
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
        {devices.map((device) => {
          const state = targetStates[device.device_id];
          const live = progress[device.device_id];
          const updated = targetVersion && device.current_version === targetVersion;

          const accent =
            state === "FAILED" ? "var(--fault)"
            : state === "SKIPPED" ? "var(--dormant)"
            : state === "ROLLED_BACK" ? "var(--govern)"
            : updated ? "var(--verified)"
            : live ? "var(--transit)"
            : device.online ? "var(--rule)"
            : "var(--dormant)";

          const mark =
            state === "FAILED" ? "✕"
            // Amber and a down-arrow: a reverted device is neither a success
            // nor a failure, and the strip should not imply either.
            : state === "ROLLED_BACK" ? "↓"
            : state === "SKIPPED" ? "–"
            : updated ? "▪"
            : live ? "▸"
            : "";

          return (
            <button
              key={device.device_id}
              onClick={() => onSelect?.(device.device_id)}
              className="relative overflow-hidden border border-rule bg-panel p-2 text-left transition-colors hover:border-ink-mute"
              style={{ borderLeftWidth: 3, borderLeftColor: accent }}
            >
              {live && !updated && (
                <span
                  className="absolute inset-x-0 bottom-0 transition-[height] duration-150"
                  style={{
                    height: `${live.percent}%`,
                    background: "var(--transit)",
                    opacity: 0.12,
                  }}
                />
              )}

              <div className="relative flex items-center justify-between">
                <span className="font-mono text-data font-medium">{device.device_id}</span>
                <span style={{ color: accent }} className="font-mono text-data">{mark}</span>
              </div>

              <div className="relative mt-1 flex items-center justify-between">
                <span className="font-mono text-[11px] text-ink-mute">
                  {device.current_version ?? "—"}
                </span>
                <span className="flex items-center gap-2">
                  {batteryBars(device.battery)}
                  <span className="font-mono text-[11px] text-ink-mute">
                    n{device.network_quality ?? "–"}
                  </span>
                </span>
              </div>

              <div className="relative mt-1 font-mono text-[10px] text-ink-mute">
                {live && !updated
                  ? `${live.chunkIndex + 1}/${live.chunkCount} chunks`
                  : device.online
                    ? device.fleet_tag ?? "—"
                    : "offline"}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}