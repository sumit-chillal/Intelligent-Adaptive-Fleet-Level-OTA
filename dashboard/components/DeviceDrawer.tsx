"use client";

/**
 * Device detail — a slide-over panel.
 *
 * This is where the audit trail stops being a claim and becomes something you
 * can look at. Everything the system knows about one device: who it is, how it
 * is doing, what version it runs, and the ordered record of everything that
 * ever happened to it, with the battery and signal readings captured at each
 * moment rather than as they are now.
 *
 * A panel rather than a page. The operator is looking at a device IN CONTEXT
 * of the fleet, and navigating away to a detail route loses that context and
 * the live campaign behind it.
 */

import { useEffect, useState } from "react";
import { api, pingDevice, type DeviceDetail } from "@/lib/api";

interface Props {
  deviceId: string | null;
  onClose: () => void;
}

const clock = (ts: string) =>
  new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
const day = (ts: string) =>
  new Date(ts).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });

export function DeviceDrawer({ deviceId, onClose }: Props) {
  const [device, setDevice] = useState<DeviceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [pinging, setPinging] = useState(false);
  const [pingResult, setPingResult] = useState<string | null>(null);

  useEffect(() => {
    if (!deviceId) {
      setDevice(null);
      return;
    }
    setLoading(true);
    setPingResult(null);
    void api
      .device(deviceId)
      .then(setDevice)
      .catch(() => setDevice(null))
      .finally(() => setLoading(false));
  }, [deviceId]);

  // Escape closes. A panel that traps you is worse than no panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ping = async () => {
    if (!deviceId) return;
    setPinging(true);
    setPingResult(null);
    try {
      const result = await pingDevice(deviceId);
      setPingResult(
        result.responded
          ? `replied · battery ${result.battery}% · network ${result.network_quality}`
          : "no reply within 6s",
      );
      setDevice(await api.device(deviceId));
    } catch {
      setPingResult("ping failed");
    } finally {
      setPinging(false);
    }
  };

  if (!deviceId) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-ink/20"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[520px] flex-col border-l border-rule bg-panel shadow-2xl"
        role="dialog"
        aria-label={`Device ${deviceId}`}
      >
        <header className="flex items-start justify-between border-b border-rule px-5 py-4">
          <div>
            <div className="legend mb-1">Device</div>
            <div className="font-mono text-figure font-medium">{deviceId}</div>
          </div>
          <button
            onClick={onClose}
            className="font-mono text-data text-ink-mute hover:text-ink"
            aria-label="Close"
          >
            ✕ esc
          </button>
        </header>

        {loading && (
          <div className="px-5 py-8 text-body text-ink-mute">Loading…</div>
        )}

        {device && (
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {/* ------------------------------------------------ identity */}
            <section className="mb-5 grid grid-cols-2 gap-y-3">
              <Field label="Status">
                <span
                  style={{
                    color: device.online ? "var(--verified)" : "var(--dormant)",
                  }}
                >
                  {device.online ? "online" : "offline"}
                </span>
              </Field>
              <Field label="Fleet">{device.fleet_tag ?? "—"}</Field>
              <Field label="Version">{device.current_version ?? "—"}</Field>
              <Field label="Model">{device.model}</Field>
              <Field label="Battery">
                <span
                  style={{
                    color:
                      device.battery !== null && device.battery < 30
                        ? "var(--fault)"
                        : undefined,
                  }}
                >
                  {device.battery ?? "—"}%
                </span>
              </Field>
              <Field label="Network">
                <span
                  style={{
                    color:
                      device.network_quality !== null &&
                      device.network_quality < 2
                        ? "var(--fault)"
                        : undefined,
                  }}
                >
                  {device.network_quality ?? "—"}/5
                </span>
              </Field>
              <Field label="Last seen">
                {device.last_seen_at ? clock(device.last_seen_at) : "—"}
              </Field>
              <Field label="Profile">
                {device.failure_profile?.mode ?? "none"}
              </Field>
            </section>

            {/* --------------------------------------------- live ping */}
            <section className="mb-5 flex items-center gap-3">
              <button
                onClick={ping}
                disabled={pinging}
                className="border border-rule px-3 py-1 font-mono text-data hover:border-ink-mute disabled:opacity-40"
              >
                {pinging ? "asking…" : "Ping device"}
              </button>
              {pingResult && (
                <span className="font-mono text-[11px] text-ink-mute">
                  {pingResult}
                </span>
              )}
            </section>

            {/* ------------------------------------------- health trend */}
            <section className="mb-5">
              <div className="legend mb-2">Battery · last {device.health.length} samples</div>
              <Sparkline
                values={device.health.map((h) => h.battery)}
                min={0}
                max={100}
                warnBelow={30}
              />
              <div className="legend mb-2 mt-4">Network quality</div>
              <Sparkline
                values={device.health.map((h) => h.network_quality)}
                min={0}
                max={5}
                warnBelow={2}
              />
            </section>

            {/* ----------------------------------------------- timeline */}
            <section>
              <div className="legend mb-2">
                History · {device.events.length} events
              </div>
              <p className="mb-3 text-[11px] text-ink-mute">
                Readings are those captured at the moment of each event, not the
                device&apos;s current state — which is what makes it possible to
                say why something happened rather than only that it did.
              </p>
              <div className="space-y-[6px]">
                {device.events.length === 0 && (
                  <p className="text-body text-ink-mute">
                    Nothing recorded for this device yet.
                  </p>
                )}
                {device.events.map((e) => (
                  <div
                    key={e.id}
                    className="border-l-2 pl-2"
                    style={{
                      borderColor: e.reason_code?.startsWith("FAILED")
                        ? "var(--fault)"
                        : e.reason_code?.startsWith("ROLLED_BACK")
                          ? "var(--govern)"
                          : "var(--rule)",
                    }}
                  >
                    <div className="font-mono text-[11px] text-ink-mute">
                      {day(e.ts)} {clock(e.ts)}
                      {e.battery !== null && ` · ${e.battery}%`}
                      {e.network_quality !== null && ` · n${e.network_quality}`}
                    </div>
                    <div className="font-mono text-data">
                      {e.event_type}
                      {e.reason_code && (
                        <span
                          style={{
                            color: e.reason_code.startsWith("FAILED")
                              ? "var(--fault)"
                              : "var(--ink-mute)",
                          }}
                        >
                          {" "}
                          · {e.reason_code}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </aside>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="legend mb-[2px]">{label}</div>
      <div className="font-mono text-data">{children}</div>
    </div>
  );
}

/** Inline trend. No axes, no grid — the shape is the information. */
function Sparkline({
  values,
  min,
  max,
  warnBelow,
}: {
  values: number[];
  min: number;
  max: number;
  warnBelow: number;
}) {
  if (values.length < 2) {
    return (
      <div className="text-[11px] text-ink-mute">Not enough samples yet.</div>
    );
  }
  const W = 460;
  const H = 40;
  const step = W / (values.length - 1);
  const y = (v: number) => H - ((v - min) / (max - min)) * H;
  const path = values.map((v, i) => `${i === 0 ? "M" : "L"}${i * step},${y(v)}`).join(" ");
  const latest = values[values.length - 1];

  return (
    <div className="flex items-center gap-3">
      <svg width={W} height={H} className="max-w-full" role="img"
           aria-label={`trend, latest ${latest}`}>
        <line x1="0" y1={y(warnBelow)} x2={W} y2={y(warnBelow)}
              stroke="var(--rule)" strokeDasharray="2 3" />
        <path d={path} fill="none" strokeWidth="1.5"
              stroke={latest < warnBelow ? "var(--fault)" : "var(--transit)"} />
      </svg>
      <span className="font-mono text-data">{latest}</span>
    </div>
  );
}