/**
 * CONVOY — API client.
 *
 * The dashboard follows snapshot-then-stream: fetch full state over REST,
 * then apply deltas from the WebSocket. That is what makes a dropped socket
 * recoverable — on reconnect it re-fetches rather than trying to replay what
 * it missed, so the screen is never quietly stale.
 */

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface Device {
  device_id: string;
  device_type: string;
  model: string;
  fleet_tag: string | null;
  current_version: string | null;
  online: boolean;
  battery: number | null;
  network_quality: number | null;
  last_seen_at: string | null;
  failure_profile: { mode?: string; p?: number } | null;
}

export interface Decision {
  batch_index: number;
  prev_batch_size: number;
  new_batch_size: number;
  observed_failure_rate: number;
  ewma: number;
  attempted: number;
  failures: number;
  skipped: number;
  action: string;
  reason_code: string;
  detail: string | null;
  ts: string;
}

export interface Batch {
  id: number;
  index: number;
  planned_size: number;
  actual_size: number;
  is_canary: boolean;
  success: number;
  failure: number;
  skipped: number;
  opened_at: string;
  closed_at: string | null;
}

export interface Target {
  device_id: string;
  state: string;
  reason_code: string | null;
  attempts: number;
  deferrals: number;
  batch_id: number | null;
  from_version: string | null;
  to_version: string | null;
  last_chunk_index: number;
}

export interface Campaign {
  campaign_id: string;
  name: string;
  state: string;
  firmware_id: string;
  batch_size_initial: number;
  current_batch_size: number;
  canary_size: number;
  batches_completed: number;
  ewma_failure_rate: number;
  shrink_threshold: number;
  abort_threshold: number;
  min_battery: number;
  min_network_quality: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  counts?: Record<string, number>;
  targets?: Target[];
  batches?: Batch[];
  decisions?: Decision[];
}

export interface FleetEvent {
  id: number;
  device_id: string;
  campaign_id: string | null;
  event_type: string;
  reason_code: string | null;
  battery: number | null;
  network_quality: number | null;
  ts: string;
}

export interface Health {
  status: string;
  broker_connected: boolean;
  messages_handled: number;
  ws_subscribers: number;
  events_published: number;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  devices: () => get<Device[]>("/api/devices"),
  campaigns: () => get<Campaign[]>("/api/campaigns"),
  campaign: (id: string) => get<Campaign>(`/api/campaigns/${id}`),
  events: (limit = 60) => get<FleetEvent[]>(`/api/events?limit=${limit}`),
  timeline: (deviceId: string, campaignId?: string) =>
    get<FleetEvent[]>(
      `/api/devices/${deviceId}/timeline${campaignId ? `?campaign_id=${campaignId}` : ""}`,
    ),
};

/** Mutating calls go through a Next route handler so the admin token stays
 *  on the server and never reaches the browser bundle. */
export async function campaignAction(
  campaignId: string,
  action: "start" | "pause" | "abort",
): Promise<Response> {
  return fetch(`/api/campaign-action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ campaignId, action }),
  });
}