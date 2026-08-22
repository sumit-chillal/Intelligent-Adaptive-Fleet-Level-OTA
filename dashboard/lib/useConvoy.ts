"use client";

/**
 * CONVOY — live fleet state.
 *
 * Snapshot, then stream. The hook fetches full state over REST, opens the
 * WebSocket, and applies deltas as they arrive. If the socket drops it
 * reconnects with backoff and RE-FETCHES, because a socket that has been away
 * has missed messages and replaying is not possible — the only honest recovery
 * is to ask the server what is true now.
 *
 * Deltas are coalesced into a single React state update per animation frame.
 * Fifteen devices heartbeating every five seconds is trivial, but ten thousand
 * would be two thousand renders a second, and the fix belongs here rather than
 * in a later panic.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Campaign, type Decision, type Device, type FleetEvent } from "./api";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws";

export type Connection = "connecting" | "live" | "reconnecting";

export interface Progress {
  chunkIndex: number;
  chunkCount: number;
  percent: number;
}

export interface ConvoyState {
  devices: Device[];
  campaign: Campaign | null;
  campaigns: Campaign[];
  events: FleetEvent[];
  decisions: Decision[];
  progress: Record<string, Progress>;
  connection: Connection;
  lastSync: string | null;
  refresh: () => void;
}

export function useConvoy(campaignId?: string): ConvoyState {
  const [devices, setDevices] = useState<Device[]>([]);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [events, setEvents] = useState<FleetEvent[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [connection, setConnection] = useState<Connection>("connecting");
  const [lastSync, setLastSync] = useState<string | null>(null);

  const pending = useRef<any[]>([]);
  const frame = useRef<number | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(500);

  const snapshot = useCallback(async () => {
    try {
      const [d, cs] = await Promise.all([api.devices(), api.campaigns()]);
      setDevices(d);
      setCampaigns(cs);
      const active =
        cs.find((c) => c.campaign_id === campaignId) ??
        cs.find((c) => c.state === "RUNNING") ??
        cs[0] ??
        null;
      if (active) {
        const full = await api.campaign(active.campaign_id);
        setCampaign(full);
        setDecisions(full.decisions ?? []);
      }
      setEvents(await api.events(60));
      setLastSync(new Date().toISOString());
    } catch (err) {
      console.error("snapshot failed", err);
    }
  }, [campaignId]);

  /** Apply a frame's worth of deltas in one render pass. */
  const flush = useCallback(() => {
    frame.current = null;
    const batch = pending.current;
    pending.current = [];
    if (!batch.length) return;

    let touchedCampaign = false;

    for (const msg of batch) {
      const { channel, data } = msg;

      if (channel === "device" || channel === "health") {
        setDevices((prev) => {
          const i = prev.findIndex((d) => d.device_id === data.device_id);
          if (i === -1) return prev;
          const next = [...prev];
          next[i] = {
            ...next[i],
            online: data.online ?? next[i].online,
            battery: data.battery ?? next[i].battery,
            network_quality: data.network_quality ?? next[i].network_quality,
            current_version: data.current_version ?? next[i].current_version,
          };
          return next;
        });
      } else if (channel === "progress") {
        setProgress((prev) => ({
          ...prev,
          [data.device_id]: {
            chunkIndex: data.chunk_index,
            chunkCount: data.chunk_count ?? 0,
            percent: data.percent ?? 0,
          },
        }));
      } else if (channel === "event") {
        setEvents((prev) =>
          [
            {
              id: Date.now() + Math.random(),
              device_id: data.device_id,
              campaign_id: data.campaign_id,
              event_type: data.event_type,
              reason_code: data.reason_code,
              battery: data.battery,
              network_quality: data.network_quality,
              ts: msg.ts,
            } as FleetEvent,
            ...prev,
          ].slice(0, 80),
        );
      } else if (channel === "decision") {
        setDecisions((prev) => [
          ...prev,
          {
            batch_index: data.batch_index,
            prev_batch_size: data.prev_batch_size,
            new_batch_size: data.new_batch_size,
            observed_failure_rate: data.observed_failure_rate,
            ewma: data.ewma,
            attempted: data.attempted,
            failures: data.failures,
            skipped: data.skipped,
            action: data.action,
            reason_code: data.reason_code,
            detail: data.detail,
            ts: msg.ts,
          },
        ]);
        touchedCampaign = true;
      } else if (channel === "batch" || channel === "campaign") {
        touchedCampaign = true;
      }
    }

    // Batch and campaign transitions change enough structure (target states,
    // batch membership) that patching by hand would drift. Re-fetch instead:
    // these fire a handful of times per campaign, not per message.
    if (touchedCampaign) void snapshot();
  }, [snapshot]);

  const enqueue = useCallback(
    (msg: any) => {
      pending.current.push(msg);
      if (frame.current === null) {
        frame.current = requestAnimationFrame(flush);
      }
    },
    [flush],
  );

  useEffect(() => {
    let closed = false;
    void snapshot();

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(WS_URL);
      socket.current = ws;

      ws.onopen = () => {
        setConnection("live");
        backoff.current = 500;
        // A socket that has been away missed messages. Re-sync rather than
        // trusting whatever the screen currently shows.
        void snapshot();
      };
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.channel !== "hello") enqueue(msg);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setConnection("reconnecting");
        setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 2, 10_000);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      socket.current?.close();
    };
  }, [snapshot, enqueue]);

  return {
    devices,
    campaign,
    campaigns,
    events,
    decisions,
    progress,
    connection,
    lastSync,
    refresh: snapshot,
  };
}