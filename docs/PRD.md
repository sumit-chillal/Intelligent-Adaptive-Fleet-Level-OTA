# PRD — Adaptive Fleet-Level OTA Firmware Deployment Framework

**Codename:** CONVOY
**Version:** 1.0 (pre-implementation baseline)
**Owner:** Project team
**Status:** Approved for build

---

## 1. Problem statement

Connected vehicles and industrial IoT fleets must receive firmware updates remotely and continuously. Production OTA systems in most student and mid-tier commercial projects push updates in **fixed batches** with **no awareness of live device health**. Consequences:

- A bad firmware image reaches thousands of devices before anyone notices.
- Devices with low battery or poor signal start a flash cycle they cannot finish and brick.
- A dropped connection at 80% download restarts the transfer from byte zero.
- There is no signed chain of trust, so anyone who can publish to the transport can push firmware.
- After the campaign, nobody can answer "what happened to device X and why".

CONVOY fixes all five by making the rollout **closed-loop**: the server observes real-time outcomes and changes its own batch size mid-campaign.

## 2. Goals

| # | Goal | Measure of success |
|---|---|---|
| G1 | Push a signed firmware image to a fleet across arbitrary networks | Devices on 4 different NATs/ISPs complete the same campaign |
| G2 | Adapt rollout aggressiveness to live failure rate | Batch size provably shrinks within one batch of a failure spike, decision logged with reason |
| G3 | Sub-100 ms dashboard reflection of device state | p95 device-event → browser-paint latency < 100 ms on LAN-attached admin |
| G4 | Never install unauthenticated firmware | Tampered or unsigned image is rejected on-device, event recorded |
| G5 | Survive interruption | A device killed mid-download resumes at the last verified chunk, not chunk 0 |
| G6 | Full auditability | "What happened to device X in campaign Y and why" answerable from Postgres alone |
| G7 | Scale beyond the demo | Architecture supports 10,000+ devices with no design change, only capacity change |

## 3. Non-goals (v1)

- Delta/differential firmware (bsdiff) — full-image only; the chunk protocol is designed so delta drops in later.
- Multi-tenant SaaS billing, SSO, org hierarchies.
- Real CAN-bus / UDS flashing of ECUs behind the TCU. The TCU itself is the update target.
- Mobile app. Dashboard is desktop-web first (projector-optimised).

## 4. Personas

| Persona | Needs |
|---|---|
| **Release engineer (Admin)** | Upload firmware, define a campaign, watch it, hit the kill switch, explain the outcome afterwards |
| **Field/support engineer** | Look up one VIN/device, see its version history and why its last update failed |
| **Device (TCU)** | Know whether it is safe to update right now; get a verified image; never brick |
| **Auditor / evaluator** | Reconstruct any campaign from stored records without trusting logs |

## 5. Feature requirements

### F1 — Firmware management
- Upload a binary + semantic version + target hardware model + release notes.
- Server computes SHA-256 of whole image, splits into fixed-size chunks (default 8 KiB), computes per-chunk SHA-256.
- Server builds a **manifest** and signs it with an Ed25519 private key held only by the server.
- Firmware states: `draft → published → deprecated → revoked`. Only `published` can be campaigned.
- Every image is immutable once published. New build = new version.

### F2 — Campaign management
- Create campaign: firmware, target selector (all / by model / by tag / explicit list), **initial batch size**, min/max batch size, eligibility thresholds (min battery %, min network quality, must-be-online), failure thresholds, canary size.
- Lifecycle: `draft → running → paused → completed | aborted | rolled_back`.
- Manual controls at all times: **Pause**, **Resume**, **Abort**, **Roll back fleet**.
- Adjustable batch size: editable before start and live during a run (operator override wins over the engine, and the override is itself an audited event).

### F3 — Adaptive rollout engine
- Operates per batch, not per device. AIMD (additive-increase / multiplicative-decrease) with hysteresis.
- Inputs: batch outcome counts, EWMA of failure rate, consecutive clean batches, time since last decision.
- Rules (all thresholds configurable per campaign):
  - `f_batch > 40%` → **abort** campaign, hold remaining devices.
  - `f_batch > 20%` → `B ← max(B_min, floor(B × 0.5))`, reason `SHRINK_HIGH_FAILURE`.
  - `f_batch > 0%` → `B ← max(B_min, B − 1)`, reason `SHRINK_MINOR_FAILURE`.
  - `f_batch == 0` for ≥2 consecutive batches → `B ← min(B_max, B + ceil(B × 0.5))`, reason `GROW_STABLE`.
  - Otherwise hold, reason `HOLD_COOLDOWN`.
- Every decision is persisted as a row: previous size, new size, observed rate, EWMA, reason, timestamp. This table drives the analytics chart and the terminal print-out.
- Canary: first batch is forced to `canary_size` (default 1–2) regardless of `B0`.

### F4 — Transport (MQTT over the public internet)
- Broker: **HiveMQ Cloud**, MQTT 5 over TLS 1.2+ on 8883, per-device username/password.
- All parties are **clients**. Nobody opens an inbound port. NAT/CGNAT/hotspot friendly.
- Server never holds a socket to a device; devices never call the REST API. Requirement 17 satisfied structurally, not by convention.
- Last Will and Testament per device gives instant offline detection.
- QoS 1 everywhere, idempotent handlers, `retain` only on device status topics.

### F5 — Real-time dashboard
- WebSocket channel from browser to FastAPI; FastAPI fans out from Redis pub/sub so any worker can serve any socket.
- Events: device online/offline, health sample, job state change, batch opened/closed, adaptive decision, campaign state change.
- Client reconnects with backoff and re-syncs via REST snapshot, so a dropped socket never leaves a stale screen.

### F6 — Fleet dashboard UI
- Live campaign header (state, progress, current batch size, failure rate).
- **Convoy Strip**: batch-by-batch timeline where segment width = batch size (see Design.md).
- Virtualised device grid: 15 devices or 10,000 devices, same component.
- Device drawer: identity, health sparkline, current/target version, chunk progress, full event timeline.

### F7 — Analytics page
- Campaign summary (attempted, succeeded, failed, skipped-ineligible, rolled back, mean duration).
- Batch size over time with decision annotations.
- Failure taxonomy breakdown (`LOW_BATTERY`, `POOR_NETWORK`, `CHECKSUM_MISMATCH`, `SIGNATURE_INVALID`, `TIMEOUT`, `FLASH_ERROR`, `DEVICE_OFFLINE`).
- Per-device outcome table, exportable to CSV.

### F8 — Failure simulation
- Simulated TCU reads `BATTERY_LEVEL`, `NETWORK_QUALITY`, `FAILURE_MODE`, `FAILURE_PROBABILITY` from environment variables only — no source edits (Requirement 16).
- `FAILURE_MODE ∈ {none, low_battery, poor_network, checksum, flash_error, drop_midway, timeout}`.
- `drop_midway` kills the transfer at a random chunk to exercise resume.

### F9 — Channel security & firmware integrity
- **Transport:** TLS to broker + per-device credentials + ACL so a device can only publish on its own topic branch.
- **Payload / provenance:** Ed25519 signature over a canonical manifest. The device holds only the **public** key, burned into its image. An attacker with full broker access still cannot produce a manifest the device accepts, because the signing secret never leaves the server. This is the same trust model as Uptane's image repository and is explained inline in `crypto.py` and `verify.c`.
- Anti-rollback counter: device refuses a manifest whose `version_code` is lower than its stored `min_allowed_version` unless the manifest carries a signed `rollback: true` flag.

### F10 — Rollback
- Devices keep two slots (A/B). The previous known-good image is never erased until the new one boots and self-confirms.
- Device-initiated: boot watchdog not confirmed in N seconds → revert to previous slot, report `ROLLED_BACK_AUTOMATIC`.
- Server-initiated: operator hits "Roll back fleet"; server issues a signed rollback manifest pinned to the last stable version.

### F11 — Automatic retry with resume
- Device persists `{campaign_id, firmware_id, next_chunk, bytes_ok, running_hash}` to durable storage after every N chunks.
- On reconnect it publishes `ota/resume` with its last verified chunk index; the server streams from there.
- Retry policy: exponential backoff, jitter, `max_attempts` per campaign; each attempt is its own audited record.

### F12 — Docker multi-device simulation
- One image, many containers. Identity and health entirely from env vars / a mounted `fleet.yaml`.
- `docker compose --profile laptopB up` brings up `tcu_B_001..005` with no file editing.
- Container is stateless except for a small named volume holding resume state, proving resume across restarts.

### F13 — ESP32 hardware integration
- Same MQTT topics, same manifest, same signature verification, same resume protocol as the simulated TCU. The server cannot tell them apart except by `device_type`.
- SSD1306 OLED shows device ID, battery, state, progress bar, and failure reason.
- LEDs: green = idle/verified, blue = updating, red = failed.
- Real `esp_ota_ops` dual-partition flash with rollback.

### F14 — Persistence & auditability
- Append-only `device_events` table. Nothing is ever updated in place; current state is a materialised projection.
- Query `SELECT * FROM device_events WHERE device_id=? AND campaign_id=? ORDER BY seq` answers G6 completely, including the reason code and the health snapshot captured at decision time.

## 6. Success criteria (demo acceptance)

1. Admin launches a campaign from Laptop A.
2. 15 containers across Laptops B/C/D on three different networks plus 3 ESP32 boards on a fourth network all react.
3. `tcu_D_004` (battery 8%), `tcu_D_005` (network quality 1), and `esp32_003` (battery 8%) fail with the correct reason codes.
4. Backend terminal prints the adaptive decision, e.g.
   `[ADAPTIVE] batch#2 failures=2/5 (40.0%) ewma=0.24 → SHRINK_HIGH_FAILURE 5 → 2`
5. Remaining devices update in the reduced batch size.
6. Every transition appears on the dashboard within 100 ms.
7. Analytics page shows the batch-size timeline with the decision annotated, and full per-device outcomes.
8. Killing a container mid-download and restarting it resumes from the last chunk, not from zero.
9. Publishing a tampered image with a forged signature is rejected by every device with `SIGNATURE_INVALID`.

## 7. Scale requirements

| Dimension | Demo | Design target |
|---|---|---|
| Devices | 18 | 10,000 concurrent, 100,000 registered |
| Events/sec | ~50 | 5,000 sustained |
| Backend instances | 1 | N stateless workers behind shared MQTT subscriptions |
| Orchestrators | 1 | 1 leader elected via Redis lock, hot standby |
| Firmware size | 256 KiB–2 MiB | ≤ 8 MiB over MQTT chunks; larger images switch to signed HTTPS URL in the manifest (protocol already carries the field) |

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| HiveMQ Cloud free tier connection cap (100) | Demo uses 20; scale test uses self-hosted EMQX in Docker with identical config |
| Campus WiFi blocks 8883 | Broker also configured for MQTT over WebSocket on 443; client falls back automatically |
| ESP32 flash memory too small for dual OTA slots | Use a 4 MB module with a custom partition table (`ota_0`, `ota_1`, `otadata`, `nvs`) |
| Chunked MQTT transfer is slow | 8 KiB chunks at QoS 1 with a sliding window of 8 in flight; measured, tunable per campaign |
| Clock skew breaks signature freshness | Manifests carry a nonce + campaign ID, not a timestamp-only freshness check |
