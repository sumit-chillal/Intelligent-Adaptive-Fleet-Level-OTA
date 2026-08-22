"use client";

/**
 * CONVOY — live campaign.
 *
 * The page's single job: let an operator standing ten feet from a projector
 * know whether the rollout is safe, and if not, which devices are hurting it
 * and what the system did about it.
 *
 * That framing decides the hierarchy. The campaign state word is 72px and the
 * largest thing on screen, because from ten feet that word alone is the
 * answer. Everything else is available on approach.
 */

import { useMemo, useState } from "react";
import { ConvoyStrip } from "@/components/ConvoyStrip";
import { DecisionLog } from "@/components/DecisionLog";
import { FleetGrid } from "@/components/FleetGrid";
import { campaignAction } from "@/lib/api";
import { useConvoy } from "@/lib/useConvoy";

const STATE_WORD: Record<string, string> = {
  RUNNING: "ROLLING",
  PAUSED: "HELD",
  COMPLETED: "COMPLETE",
  ABORTED: "ABORTED",
  DRAFT: "DRAFT",
  ROLLED_BACK: "REVERTED",
};

const STATE_COLOR: Record<string, string> = {
  RUNNING: "var(--transit)",
  PAUSED: "var(--govern)",
  COMPLETED: "var(--verified)",
  ABORTED: "var(--fault)",
  DRAFT: "var(--ink-mute)",
};

export default function LiveCampaignPage() {
  const { devices, campaign, events, decisions, progress, connection, refresh } =
    useConvoy();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<null | "abort">(null);
  const [confirmText, setConfirmText] = useState("");

  const targetStates = useMemo(() => {
    const map: Record<string, string> = {};
    campaign?.targets?.forEach((t) => (map[t.device_id] = t.state));
    return map;
  }, [campaign]);

  const counts = useMemo(() => {
    const t = campaign?.targets ?? [];
    return {
      total: t.length,
      succeeded: t.filter((x) => x.state === "SUCCEEDED").length,
      failed: t.filter((x) => x.state === "FAILED").length,
      skipped: t.filter((x) => x.state === "SKIPPED").length,
    };
  }, [campaign]);

  const latest = decisions[decisions.length - 1];
  const shrank = latest && latest.new_batch_size < latest.prev_batch_size;

  const act = async (action: "start" | "pause" | "abort") => {
    if (!campaign) return;
    setBusy(true);
    try {
      await campaignAction(campaign.campaign_id, action);
      await refresh();
    } finally {
      setBusy(false);
      setConfirming(null);
      setConfirmText("");
    }
  };

  const targetVersion = campaign?.targets?.[0]?.to_version ?? null;

  return (
    <main className="mx-auto max-w-[1600px] px-6 py-5">
      {/* ---------------------------------------------------------- header */}
      <header className="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div className="flex items-baseline gap-4">
          <span className="font-display text-lead font-bold tracking-[0.18em]">
            CONVOY
          </span>
          {campaign && (
            <span className="font-mono text-data text-ink-mute">
              {campaign.name} · {campaign.campaign_id}
            </span>
          )}
        </div>

        <div className="flex items-center gap-4">
          <a href="/analytics" className="font-mono text-data text-ink-mute underline">
            Analytics
          </a>
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

          {campaign?.state === "RUNNING" ? (
            <button
              onClick={() => act("pause")}
              disabled={busy}
              className="border border-rule bg-panel px-3 py-1 font-mono text-data hover:border-ink-mute disabled:opacity-40"
            >
              Hold rollout
            </button>
          ) : (
            <button
              onClick={() => act("start")}
              disabled={busy || !campaign}
              className="border border-rule bg-panel px-3 py-1 font-mono text-data hover:border-ink-mute disabled:opacity-40"
            >
              {campaign?.state === "PAUSED" ? "Resume rollout" : "Start campaign"}
            </button>
          )}

          <button
            onClick={() => setConfirming("abort")}
            disabled={busy || !campaign}
            className="border px-3 py-1 font-mono text-data disabled:opacity-40"
            style={{ borderColor: "var(--fault)", color: "var(--fault)" }}
          >
            Abort campaign
          </button>
        </div>
      </header>

      {/* Destructive actions require typing the campaign name. On demo day
          somebody will click the wrong thing. */}
      {confirming === "abort" && campaign && (
        <div className="panel mb-4 flex flex-wrap items-center gap-3 p-4">
          <span className="text-body">
            Type <span className="font-mono font-medium">{campaign.name}</span> to
            abort. Devices not yet updated will be held.
          </span>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            className="border border-rule bg-panel px-2 py-1 font-mono text-data"
            placeholder={campaign.name}
            autoFocus
          />
          <button
            disabled={confirmText !== campaign.name}
            onClick={() => act("abort")}
            className="border px-3 py-1 font-mono text-data disabled:opacity-30"
            style={{ borderColor: "var(--fault)", color: "var(--fault)" }}
          >
            Abort
          </button>
          <button
            onClick={() => setConfirming(null)}
            className="font-mono text-data text-ink-mute underline"
          >
            Cancel
          </button>
        </div>
      )}

      {/* ------------------------------------------------------ status row */}
      <section className="panel mb-3 flex flex-wrap items-end justify-between gap-6 px-6 py-5">
        <div>
          <div className="legend mb-1">Campaign state</div>
          <div
            className="font-display text-state font-bold"
            style={{ color: STATE_COLOR[campaign?.state ?? "DRAFT"] }}
          >
            {STATE_WORD[campaign?.state ?? "DRAFT"] ?? "—"}
          </div>
        </div>

        <Figure label="Updated" value={`${counts.succeeded}/${counts.total}`} />
        <Figure
          label="Failed"
          value={String(counts.failed)}
          color={counts.failed ? "var(--fault)" : undefined}
        />
        <Figure label="Skipped" value={String(counts.skipped)} />

        <div>
          <div className="legend mb-1">Batch size</div>
          <div className="flex items-baseline gap-2">
            <span
              className="font-display text-big font-bold"
              style={{ color: shrank ? "var(--govern)" : "var(--ink)" }}
            >
              {campaign?.current_batch_size ?? "—"}
            </span>
            {latest && latest.prev_batch_size !== latest.new_batch_size && (
              <span className="font-mono text-data" style={{ color: "var(--govern)" }}>
                {shrank ? "↓" : "↑"} from {latest.prev_batch_size}
              </span>
            )}
          </div>
          {shrank && (
            <div
              className="decision-sweep mt-1 h-[2px]"
              style={{ background: "var(--govern)" }}
            />
          )}
        </div>
      </section>

      <div className="mb-3">
        <ConvoyStrip
          batches={campaign?.batches ?? []}
          decisions={decisions}
          targets={campaign?.targets ?? []}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
        <FleetGrid
          devices={devices}
          progress={progress}
          targetVersion={targetVersion}
          targetStates={targetStates}
        />
        <DecisionLog decisions={decisions} events={events} />
      </div>
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