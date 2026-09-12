"use client";

/**
 * A device, drawn as a car — laid out to the reference card.
 *
 * Reading order runs top to bottom: identity, then the two readings that decide
 * eligibility, then the car. The car is the status display rather than
 * decoration: its body fills with the thing currently worth watching, which is
 * battery at rest and transfer progress during a rollout.
 *
 * While chunks are arriving the fill rises with a rippling surface. A flat bar
 * that steps forward every few seconds is ambiguous — a stalled transfer and a
 * slow one look identical until you watch for half a minute. A moving surface
 * says "running" continuously and stops dead when the transfer does, which is
 * the distinction an operator actually needs.
 */

import { motion } from "motion/react";
import { useEffect, useState } from "react";

import type { Device } from "@/lib/api";
import type { Progress } from "@/lib/useConvoy";

interface Props {
  device: Device;
  progress?: Progress;
  targetVersion?: string | null;
  targetState?: string;
  onSelect?: (deviceId: string) => void;
}

/* ------------------------------------------------------------------ car -- */
const BODY =
  "M20 62 L26 44 Q30 34 44 32 L96 26 Q104 14 130 13 Q166 12 188 28 " +
  "L224 34 Q246 38 250 52 L252 62 Z";
const GREENHOUSE = "M104 26 Q112 17 130 16 Q158 15 176 27 Z";
const CAR_W = 272;
const CAR_H = 92;

/**
 * A region filled to `pct`, its top edge a travelling wave.
 *
 * Amplitude eases to nothing at both ends: a full tank should read as full and
 * an empty one should not slosh. Two frequencies are summed so the surface does
 * not repeat on an obvious beat, which a single sine does — that reads as
 * mechanical oscillation rather than liquid.
 */
function wavePath(pct: number, phase: number): string {
  const clamped = Math.min(Math.max(pct, 0), 100);
  const level = CAR_H - (CAR_H + 6) * (clamped / 100);
  const amp = 3.6 * Math.sin((clamped / 100) * Math.PI);
  const pts: string[] = [];
  for (let x = 0; x <= CAR_W; x += 8) {
    const y =
      level +
      amp * Math.sin(x / 26 + phase) +
      amp * 0.45 * Math.sin(x / 11 - phase * 1.6);
    pts.push(`${x === 0 ? "M" : "L"}${x} ${y.toFixed(2)}`);
  }
  return `${pts.join(" ")} L${CAR_W} ${CAR_H} L0 ${CAR_H} Z`;
}

/* ---------------------------------------------------------------- marks -- */
function SignalBars({ level }: { level: number | null }) {
  const n = level ?? 0;
  const lit = Math.ceil((n / 5) * 4);
  return (
    <span className="inline-flex items-end gap-[3px]" aria-label={`network ${n} of 5`}>
      {[1, 2, 3, 4].map((b) => (
        <span
          key={b}
          className="w-[4px] rounded-[1px]"
          style={{
            height: 5 + b * 3,
            background:
              b <= lit ? (n <= 1 ? "var(--fault)" : "var(--ink)") : "var(--rule)",
          }}
        />
      ))}
    </span>
  );
}

function BatteryIcon({ pct }: { pct: number | null }) {
  const v = pct ?? 0;
  const cells = Math.round((v / 100) * 3);
  const low = v < 30;
  return (
    <span className="inline-flex items-center" aria-label={`battery ${v} percent`}>
      <span
        className="flex h-[15px] w-[26px] items-center gap-[2px] rounded-[3px] border-[2px] px-[2px]"
        style={{ borderColor: low ? "var(--fault)" : "var(--ink)" }}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-[7px] flex-1 rounded-[1px]"
            style={{
              background:
                i < cells ? (low ? "var(--fault)" : "var(--ink)") : "transparent",
            }}
          />
        ))}
      </span>
      <span
        className="ml-[2px] h-[6px] w-[2px] rounded-r-[1px]"
        style={{ background: low ? "var(--fault)" : "var(--ink)" }}
      />
    </span>
  );
}

/** The two-lamp status pill from the reference: red and green side by side. */
function StatusPill({ online }: { online: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-[7px] rounded-full border px-[9px] py-[4px]"
      style={{ borderColor: "var(--rule)", background: "var(--panel)" }}
      aria-label={online ? "online" : "offline"}
    >
      <span
        className="h-[8px] w-[8px] rounded-full"
        style={{
          background: online ? "var(--rule)" : "var(--fault)",
          boxShadow: online ? "none" : "0 0 6px var(--fault)",
        }}
      />
      <span
        className="h-[8px] w-[8px] rounded-full"
        style={{
          background: online ? "var(--verified)" : "var(--rule)",
          boxShadow: online ? "0 0 6px var(--verified)" : "none",
        }}
      />
    </span>
  );
}

/* ----------------------------------------------------------------- card -- */
export function DeviceCard({
  device,
  progress,
  targetVersion,
  targetState,
  onSelect,
}: Props) {
  const updated = Boolean(targetVersion && device.current_version === targetVersion);
  const downloading = Boolean(progress && !updated);

  const [phase, setPhase] = useState(0);
  useEffect(() => {
    if (!downloading) return;
    let raf = 0;
    const tick = () => {
      setPhase((t) => (t + 0.055) % (Math.PI * 2));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [downloading]);

  const accent =
    targetState === "FAILED"
      ? "var(--fault)"
      : targetState === "ROLLED_BACK"
        ? "var(--govern)"
        : targetState === "SKIPPED"
          ? "var(--dormant)"
          : updated
            ? "var(--verified)"
            : downloading
              ? "var(--transit)"
              : device.online
                ? "var(--rule)"
                : "var(--dormant)";

  const mark =
    targetState === "FAILED"
      ? "✕"
      : targetState === "ROLLED_BACK"
        ? "↓"
        : targetState === "SKIPPED"
          ? "–"
          : updated
            ? "▪"
            : downloading
              ? "▸"
              : "";

  const fillPct = downloading
    ? progress!.percent
    : device.battery !== null
      ? device.battery
      : 0;
  const fillColor = downloading
    ? "var(--transit)"
    : device.battery !== null && device.battery < 30
      ? "var(--fault)"
      : "var(--transit)";

  const uid = device.device_id.replace(/[^a-zA-Z0-9]/g, "");

  return (
    <motion.button
      layout
      onClick={() => onSelect?.(device.device_id)}
      whileHover={{ y: -3 }}
      whileTap={{ scale: 0.985 }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
      className="group relative flex flex-col overflow-hidden rounded-xl border border-rule bg-panel p-4 text-left"
      style={{ borderLeftWidth: 3, borderLeftColor: accent }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-lead font-medium tracking-tight">
          {device.device_id}
        </span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-data" style={{ color: accent }}>
            {mark}
          </span>
          <StatusPill online={device.online} />
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <span className="legend">Network</span>
        <SignalBars level={device.network_quality} />
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="legend">Battery</span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-ink-mute">
            {device.battery ?? "–"}%
          </span>
          <BatteryIcon pct={device.battery} />
        </span>
      </div>

      <svg
        viewBox={`0 0 ${CAR_W} ${CAR_H}`}
        className="mt-3 w-full"
        role="img"
        aria-label={`${device.device_id}, ${fillPct.toFixed(0)} percent`}
      >
        <defs>
          <clipPath id={`clip-${uid}`}>
            {downloading ? (
              <path d={wavePath(fillPct, phase)} />
            ) : (
              <motion.rect
                x="0"
                y="0"
                height={CAR_H}
                initial={false}
                animate={{ width: (CAR_W * fillPct) / 100 }}
                transition={{ type: "spring", stiffness: 120, damping: 24 }}
              />
            )}
          </clipPath>
        </defs>

        <path d={BODY} fill="var(--concrete)" />
        <g clipPath={`url(#clip-${uid})`}>
          <path d={BODY} fill={fillColor} opacity="0.95" />
        </g>

        <g
          fill="none"
          stroke="var(--ink)"
          strokeWidth="6"
          strokeLinejoin="round"
          strokeLinecap="round"
        >
          <path d={BODY} />
          <path d={GREENHOUSE} />
        </g>

        {[74, 206].map((cx) => (
          <g key={cx}>
            <circle cx={cx} cy="64" r="19" fill="var(--panel)"
                    stroke="var(--ink)" strokeWidth="7" />
            <circle cx={cx} cy="64" r="6" fill="var(--ink)" />
          </g>
        ))}
      </svg>

      <div className="mt-2 flex items-center justify-between font-mono text-[11px]">
        <span className="text-ink-mute">{device.current_version ?? "—"}</span>
        <span style={{ color: downloading ? "var(--transit)" : "var(--ink-mute)" }}>
          {downloading
            ? `${progress!.chunkIndex + 1}/${progress!.chunkCount} chunks`
            : (device.fleet_tag ?? (device.online ? "online" : "offline"))}
        </span>
      </div>
    </motion.button>
  );
}
