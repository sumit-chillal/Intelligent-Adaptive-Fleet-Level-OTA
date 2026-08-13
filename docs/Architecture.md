# Architecture — CONVOY Adaptive OTA Framework

---

## 1. Chosen tech stack

### Backend (Laptop A only)
| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| Language | Python 3.11 | Same language as the simulated TCU → one protocol implementation, one test suite |
| API | FastAPI + Uvicorn | Native async, WebSocket and REST in one process, OpenAPI for free |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | Audit tables need real migrations, not `create_all()` |
| Database | PostgreSQL 16 (Docker) | Append-only event log with `JSONB` payloads, partial indexes, window functions for analytics |
| Cache / bus | Redis 7 (Docker) | Pub/sub fan-out to WebSocket clients + leader-election lock for the orchestrator |
| MQTT client | `aiomqtt` (async wrapper over paho) | Runs in the same event loop as FastAPI, no thread bridging |
| Crypto | `cryptography` (Ed25519, SHA-256) | Ed25519 verifies in ~2 ms on ESP32; RSA-2048 does not |
| Validation | Pydantic v2 | Message schemas shared between server and simulated TCU |
| Logging | `structlog` → JSON | Machine-parseable audit trail alongside the DB |
| Tests | pytest + pytest-asyncio + testcontainers | Real Postgres and a real broker in CI |
| Load test | Locust + a headless swarm image | Proves the 10,000-device claim |

### Frontend (Laptop A only)
| Layer | Choice | Why |
|---|---|---|
| Framework | Next.js 14 (App Router) + TypeScript | Server components for the static shell, client components for the live grid |
| Styling | Tailwind CSS + CSS custom properties | Design tokens in one place (see Design.md) |
| Components | shadcn/ui (Radix primitives) | Accessible dialogs/drawers without writing focus traps |
| Charts | Recharts | Batch-size step chart, sparklines |
| Data | TanStack Query (REST snapshot) + native WebSocket (deltas) | Snapshot-then-stream is what keeps the UI correct after a reconnect |
| Virtualisation | TanStack Virtual | 10,000 device tiles at 60 fps |
| State | Zustand | One store the WebSocket reducer writes into |

### Transport
| Layer | Choice |
|---|---|
| Broker | HiveMQ Cloud (managed), MQTT 5, TLS 1.2+ on 8883, WSS on 443 as fallback |
| Local/scale alternative | EMQX 5 in Docker with the same TLS + ACL config |
| Protocol | Custom JSON control plane + base64 chunk data plane, all versioned with a `schema` field |

### Device — simulated (Laptops B, C, D)
| Layer | Choice |
|---|---|
| Runtime | Python 3.11-slim in Docker, one container = one TCU |
| Orchestration | Docker Compose profiles, config purely from env vars |
| MQTT | `paho-mqtt` (sync, one thread, tiny footprint) |
| Storage | JSON state file on a named volume (resume state, current slot, version) |

### Device — physical (ESP32)
| Layer | Choice | Why |
|---|---|---|
| SDK | ESP-IDF v5.x (PlatformIO) | `esp_ota_ops` + `esp_https_ota` rollback support; Arduino core hides the partition API |
| MQTT | `esp-mqtt` with TLS and the HiveMQ root CA embedded | |
| Crypto | mbedTLS Ed25519 + SHA-256 | Public key compiled into the image |
| Display | SSD1306 128×64 over I²C | |
| Storage | NVS for resume state + anti-rollback counter | Survives power loss |
| Flash | Custom partition table: `nvs`, `otadata`, `ota_0` (1.5 MB), `ota_1` (1.5 MB) | True A/B slots |

---

## 2. Which laptop should be the admin: **the Mac**

**Recommendation: Mac = Laptop A (admin/backend/dashboard). Windows machines = Laptops B, C, D (TCU fleet).**

Reasoning:

1. **The admin runs the most infrastructure.** Postgres + Redis + FastAPI + Next.js + the MQTT bridge. On macOS these are native POSIX processes; Docker Desktop's volume and network stack behaves predictably. On Windows the same stack runs through WSL2, which adds a filesystem boundary where bind-mounted Postgres volumes get slow and hot-reload file watching gets flaky — exactly the two things you do not want on the projector machine.
2. **Line endings and shell.** All operational scripts (`make demo`, key generation, `openssl`, `psql`) are POSIX. On Windows they need Git Bash or WSL and CRLF discipline. The device laptops run one command (`docker compose up`) so they don't care.
3. **Projector behaviour.** macOS display mirroring at a fixed resolution is more predictable during a live demo, and Safari/Chrome scaling won't reflow the dashboard.
4. **The device laptops are better off as Windows.** They run containers only, which Docker Desktop + WSL2 handles fine, and Windows has the least friction for CP2102/CH340 USB-serial drivers if you need to reflash an ESP32 on the demo table.

**If you only have Windows machines:** make the admin the one with the most RAM (≥16 GB), run the whole backend inside WSL2 Ubuntu (not Windows-native Python), keep the repo inside the WSL filesystem (`~/convoy`, never `/mnt/c/...`), and set `git config core.autocrlf input`.

**Hard rule either way:** the admin laptop runs zero TCU containers. That is what proves the devices are genuinely remote.

---

## 3. System topology

```
                    ┌──────────────────────────────────────────┐
                    │        HiveMQ Cloud  (TLS 8883)          │
                    │   the ONLY shared connection point       │
                    └───▲──────────▲──────────▲──────────▲─────┘
       publish/subscribe│          │          │          │
                        │          │          │          │
   ┌────────────────────┴───┐  ┌───┴────┐ ┌───┴────┐ ┌───┴──────────┐
   │ LAPTOP A  (Mac, admin) │  │LAPTOP B│ │LAPTOP C│ │ LAPTOP D     │
   │  ┌──────────────────┐  │  │ 5 TCU  │ │ 5 TCU  │ │ 5 TCU        │
   │  │ MQTT bridge      │  │  │ docker │ │ docker │ │ 3 healthy    │
   │  │ Orchestrator     │  │  └────────┘ └────────┘ │ D_004 bat 8% │
   │  │ FastAPI REST/WS  │  │   Network 2   Network 3│ D_005 net 1  │
   │  └───┬────────┬─────┘  │                        └──────────────┘
   │      │        │        │                              Network 4
   │  ┌───▼──┐ ┌───▼───┐    │        ┌───────────────────────────┐
   │  │Postgres│ │Redis │   │        │ ESP32 ×3 (esp32_001..003) │
   │  └──────┘ └───────┘    │        │ esp32_003 battery 8%      │
   │  Next.js dashboard     │        └───────────────────────────┘
   │  ─── projector ───     │              Network 5 (phone hotspot)
   └────────────────────────┘
```

No laptop connects to any other laptop. There is no LAN assumption anywhere. Every participant is an **outbound TLS client** to the broker, which is why hotspots, CGNAT, and hostile campus firewalls all work.

---

## 4. Backend internal structure

Four cooperating components inside one FastAPI process (splittable into separate services without code change, because they only talk through Postgres and Redis):

1. **API layer** — REST for CRUD + snapshots, WebSocket for live deltas.
2. **MQTT bridge** — the only thing that touches the broker. Subscribes with a shared subscription group so N replicas load-balance. Validates, deduplicates (`msg_id`), writes events, publishes to Redis.
3. **Orchestrator** — the campaign state machine. Runs a tick loop, holds a Redis lease so exactly one instance is leader. Selects the next batch, checks eligibility, emits offers, closes batches, calls the adaptive engine.
4. **Adaptive engine** — a pure function. `decide(batch_result, campaign_config, history) → Decision`. Pure means it is unit-testable with no broker, no DB, and its behaviour is reproducible for the report.

```
mqtt bridge ──► device_events (append-only) ──► projections (devices, jobs)
      │                                              │
      └──► redis pubsub ──► websocket hub ──► browser│
                                                     │
orchestrator ──reads──────────────────────────────────┘
      └──► adaptive engine ──► rollout_decisions ──► analytics
```

---

## 5. Data model (PostgreSQL)

```
devices(id, device_id UNIQUE, device_type, model, hw_rev, mqtt_client_id,
        current_version, target_version, status, last_seen_at,
        battery, network_quality, active_slot, registered_at, tags JSONB)

device_health_samples(id, device_id, battery, network_quality, uptime_s, ts)   -- time-series

firmware(id, version, version_code, model, size_bytes, sha256, chunk_size,
         chunk_count, chunk_hashes JSONB, manifest JSONB, signature BYTEA,
         state, storage_path, notes, created_at)

campaigns(id, name, firmware_id, selector JSONB, state,
          batch_size_initial, batch_size_min, batch_size_max, canary_size,
          min_battery, min_network_quality, max_attempts,
          fail_shrink_threshold, fail_abort_threshold,
          created_by, created_at, started_at, ended_at)

campaign_targets(id, campaign_id, device_id, state, attempts,
                 last_reason_code, from_version, to_version,
                 batch_id, started_at, ended_at)          -- one row per device per campaign

batches(id, campaign_id, index, planned_size, actual_size,
        opened_at, closed_at, success_count, failure_count, skipped_count)

rollout_decisions(id, campaign_id, batch_id, prev_size, new_size,
                  observed_failure_rate, ewma, reason_code, detail, ts)

device_events(id BIGSERIAL, device_id, campaign_id NULL, batch_id NULL,
              event_type, reason_code, payload JSONB,
              battery_at_event, network_at_event, ts, source)   -- APPEND ONLY

audit_log(id, actor, action, entity_type, entity_id, before JSONB, after JSONB, ts)
```

`device_events` is the source of truth. `devices` and `campaign_targets` are projections that can be rebuilt from it. That is what makes Requirement 14 ("answer what happened to device X during campaign Y and why from stored data alone") literally true rather than aspirational — the reason code, the battery at the moment of the decision, and the chunk index are all in the row.

Indexes: `device_events(device_id, campaign_id, id)`, `campaign_targets(campaign_id, state)`, `device_health_samples(device_id, ts DESC)`, plus monthly partitioning on `device_events` for the scale target.

---

## 6. MQTT topic contract

```
Device → Server
  convoy/v1/d/{device_id}/hello           registration + capabilities + current version
  convoy/v1/d/{device_id}/health          battery, network quality, uptime   (every 5 s)
  convoy/v1/d/{device_id}/status          retained; LWT sets {"online":false}
  convoy/v1/d/{device_id}/ota/ack         accepted / rejected + reason
  convoy/v1/d/{device_id}/ota/progress    chunk index, bytes, percent
  convoy/v1/d/{device_id}/ota/resume      resume request with last verified chunk
  convoy/v1/d/{device_id}/ota/result      SUCCESS | FAILED + reason_code
  convoy/v1/d/{device_id}/ota/rollback    rollback outcome

Server → Device
  convoy/v1/s/{device_id}/ota/offer       signed manifest
  convoy/v1/s/{device_id}/ota/chunk       {campaign_id, index, sha256, b64}
  convoy/v1/s/{device_id}/cmd             pause | abort | rollback | ping | set-config

Server subscribes as:  $share/convoy-bridge/convoy/v1/d/+/#
```

Shared subscriptions are the scaling mechanism: add a backend replica, the broker load-balances device traffic across replicas automatically. Broker ACLs restrict each device to publishing only under `convoy/v1/d/{its own id}/#` and subscribing only under `convoy/v1/s/{its own id}/#`, so a compromised device cannot impersonate or eavesdrop on another.

---

## 7. Firmware integrity — the mechanism and why it holds

**Threat model.** The attacker has full network position: they can read and write any MQTT topic, replay old messages, and stand up a fake server. They do **not** have the server's signing key.

**Mechanism.**

1. At provisioning, an Ed25519 keypair is generated. The private key stays on the admin machine (env var / file with `0600`, never in git). The public key is compiled into every device image — into the Docker image for simulated TCUs, into flash for ESP32s.
2. On publish, the server builds a canonical manifest and signs the **canonical JSON bytes**:
   ```json
   {"schema":"convoy.manifest.v1","firmware_id":"...","version":"1.4.0","version_code":10400,
    "model":"tcu-sim-v1","size":262144,"chunk_size":8192,"chunk_count":32,
    "sha256":"<whole image>","chunk_hashes":["...", "..."],
    "campaign_id":"...","device_id":"tcu_B_001","nonce":"<random>","rollback":false}
   ```
3. The device verifies the signature **before allocating a single byte for the download**, then verifies each chunk against `chunk_hashes[i]` on arrival, then verifies the whole-image SHA-256 before marking the slot bootable.

**Why an attacker cannot push accepted firmware.** Forging an accepted image requires producing a valid Ed25519 signature over a manifest containing the attacker's image hash. That is a signature forgery against Ed25519 (~2^128 work). Modifying only the payload fails at the per-chunk hash check. Replaying an old genuine manifest fails because the manifest binds `device_id`, `campaign_id`, and a server-issued `nonce`, and because the device enforces `version_code ≥ min_allowed_version` (anti-rollback) unless the manifest is signed with `rollback:true`. TLS to the broker prevents passive interception, but the design deliberately does **not depend** on the broker being trustworthy — HiveMQ itself could be malicious and devices still would not install unauthorised firmware.

Optional confidentiality layer (implemented, toggleable): the chunk payload is AES-256-GCM encrypted with a per-campaign key that is wrapped per device using X25519 ECDH against the device's registered public key. Signature = authenticity; AES-GCM = confidentiality + integrity in transit.

---

## 8. Adaptive rollout engine

```python
def decide(batch, cfg, history) -> Decision:
    total = batch.success + batch.failure
    f     = batch.failure / total if total else 0.0
    ewma  = cfg.alpha * f + (1 - cfg.alpha) * history.ewma      # alpha = 0.5

    if f >= cfg.abort_threshold:          # 0.40
        return Decision(new=history.size, action=ABORT,  reason="ABORT_FAILURE_STORM")
    if f >= cfg.shrink_threshold:         # 0.20
        return Decision(new=max(cfg.min, history.size // 2),   reason="SHRINK_HIGH_FAILURE")
    if f > 0:
        return Decision(new=max(cfg.min, history.size - 1),    reason="SHRINK_MINOR_FAILURE")
    if history.clean_streak + 1 >= cfg.grow_after:             # 2
        return Decision(new=min(cfg.max, ceil(history.size*1.5)), reason="GROW_STABLE")
    return Decision(new=history.size, reason="HOLD_COOLDOWN")
```

Properties that matter for the report:
- **Multiplicative decrease, additive-ish increase** — reacts to danger fast, recovers slowly. Same principle as TCP congestion control, which is the right analogy to cite.
- **Hysteresis** via `grow_after` prevents oscillation between 5 → 2 → 5 → 2.
- **EWMA** is recorded but only used for the analytics view and the abort guard, so a single unlucky batch cannot alone abort a healthy campaign.
- **Pure function** → the demo's exact decision sequence is reproducible in a unit test.

Terminal output (Success Criterion 4):
```
[ADAPTIVE] campaign=c_7f21 batch#2 size=5 ok=3 fail=2 rate=40.0% ewma=0.24
           -> SHRINK_HIGH_FAILURE  batch_size 5 -> 2
           failures: tcu_D_004(LOW_BATTERY 8%)  tcu_D_005(POOR_NETWORK q=1)
```

---

## 9. End-to-end walkthrough — SOFTWARE

**Phase 0 — Boot.** Laptop A: `docker compose up postgres redis` → `alembic upgrade head` → `uvicorn app.main:app` → `npm run dev`. The MQTT bridge connects to HiveMQ and subscribes to the shared device topic. The orchestrator acquires the Redis leader lease.

**Phase 1 — Device enrolment.** Each container starts, reads `DEVICE_ID`, `BATTERY_LEVEL`, `NETWORK_QUALITY`, connects to HiveMQ over TLS with its own credentials, sets its LWT, publishes `hello` with its current version and hardware model. The bridge upserts the device, writes a `DEVICE_ONLINE` event, and pushes it to Redis. The dashboard tile appears in under 100 ms. Health samples start flowing every 5 s.

**Phase 2 — Firmware publish.** Admin uploads `tcu-1.4.0.bin`. Server hashes it, chunks it, builds the manifest, signs it with Ed25519, stores everything, marks it `published`.

**Phase 3 — Campaign creation.** Admin picks the firmware, a selector (`model = tcu-sim-v1 OR esp32-v1`), initial batch size 5, min 1, max 10, min battery 30%, min network quality 2, canary 2. Server materialises one `campaign_targets` row per matched device, all `PENDING`.

**Phase 4 — Canary batch.** Orchestrator opens batch #1 with the canary size. For each device it evaluates eligibility against the **latest health sample** — a device below threshold is marked `SKIPPED_INELIGIBLE` with a reason code and never even receives an offer (this is the "don't brick a low-battery car" rule; it is a different outcome from `FAILED` and is counted separately in the failure-rate maths). Eligible devices get a signed `ota/offer`.

**Phase 5 — Transfer.** Device verifies the signature → publishes `ack:ACCEPTED` → server streams chunks with a sliding window of 8 in flight, waiting on `progress` acks. Device hashes each chunk, writes it to the inactive slot, persists `next_chunk` every 8 chunks. Dashboard shows a live percentage per device.

**Phase 6 — Install and confirm.** Whole-image SHA-256 verified → slot marked bootable → device "reboots" (simulated: process restarts from the state file; ESP32: real `esp_restart()`) → new image boots and publishes `result:SUCCESS` with the new version, which self-confirms the slot. If no confirmation arrives inside the watchdog window, the bootloader reverts to the previous slot and the device reports `ROLLED_BACK_AUTOMATIC`.

**Phase 7 — Failures.** `tcu_D_004` reports battery 8% at offer time → `SKIPPED_INELIGIBLE / LOW_BATTERY` if caught pre-offer, or `FAILED / LOW_BATTERY` if the battery drops mid-transfer (the demo config makes it fail mid-transfer so it counts toward the failure rate — that is what drives the adaptive decision). `tcu_D_005` at network quality 1 drops chunks, exceeds the timeout, and fails with `POOR_NETWORK`. Both write full event rows including battery/network at the moment of failure.

**Phase 8 — Adaptive decision.** Batch closes when every target reaches a terminal state or the batch timeout fires. Engine computes 2/5 = 40% → `SHRINK_HIGH_FAILURE`, batch size 5 → 2. Row written to `rollout_decisions`, printed to the terminal, pushed to the dashboard where the Convoy Strip visibly narrows.

**Phase 9 — Retry.** Failed devices go back into the pool with `attempts += 1` and a backoff. When `tcu_D_005` retries, its `ota/resume` carries `last_chunk=17`, and the server streams from chunk 18 — visible on the dashboard as a progress bar that starts at 55%, not 0%.

**Phase 10 — Completion.** All targets terminal → campaign `completed`. Analytics page renders the batch-size step chart with the decision annotations, the failure taxonomy, and the per-device table.

**Phase 11 — Rollback drill.** Admin clicks "Roll back fleet". Server signs a rollback manifest pinned to 1.3.0 with `rollback:true`, devices verify it, flash the previous slot, and report `ROLLED_BACK_MANUAL`.

---

## 10. End-to-end walkthrough — HARDWARE (ESP32)

**Bill of materials per board:** ESP32-WROOM-32 (4 MB flash), SSD1306 128×64 I²C OLED, 3 LEDs (green/blue/red) with 220 Ω resistors, optional push button (manual confirm), breadboard, USB cable. Optional: INA219 or a resistor divider on a Li-ion cell for a *real* battery reading; otherwise battery is a compile-time/NVS-configured value.

**Wiring**
| Signal | GPIO |
|---|---|
| OLED SDA / SCL | 21 / 22 |
| LED green (idle/verified) | 25 |
| LED blue (updating) | 26 |
| LED red (failed) | 27 |
| Button (confirm/abort) | 0 |
| Battery ADC (optional) | 34 |

**Partition table** — `nvs`, `phy_init`, `otadata`, `factory` or `ota_0` (1.5 MB), `ota_1` (1.5 MB). This is what makes rollback real rather than simulated.

**Flow**

1. **Provision.** Flash the base firmware once over USB. `menuconfig`/`sdkconfig` carries WiFi SSID+password, HiveMQ host, per-device MQTT credentials, `DEVICE_ID`, and the **server's Ed25519 public key**. `esp32_003` is provisioned with a hardcoded battery of 8%.
2. **Connect.** WiFi (any network — the demo uses a phone hotspot) → TLS to HiveMQ with the embedded root CA → LWT set → `hello` published. OLED: `esp32_001 | v1.3.0 | ONLINE`, green LED on.
3. **Telemetry.** Every 5 s it publishes battery and RSSI-derived network quality (1–5). The server treats it identically to a container.
4. **Offer.** OLED shows `UPDATE OFFERED v1.4.0`. Device checks battery against the manifest's `min_battery`. `esp32_003` at 8% immediately publishes `FAILED / LOW_BATTERY`, red LED on, OLED prints `UPDATE ABORTED — BATTERY 8% < 30%`. This is the visible table-top failure in the demo.
5. **Verify then download.** Healthy boards verify the Ed25519 signature with mbedTLS *before* opening the OTA partition. Blue LED on, OLED renders a progress bar driven by chunk index. Each chunk is SHA-256 checked, then written with `esp_ota_write()`.
6. **Install.** `esp_ota_end()` → `esp_ota_set_boot_partition(next)` → NVS records `pending_confirm=true` → `esp_restart()`.
7. **Self-confirm.** The new image boots, reconnects, publishes `result:SUCCESS v1.4.0`, and calls `esp_ota_mark_app_valid_cancel_rollback()`. Green LED, OLED shows `v1.4.0 VERIFIED`.
8. **Automatic rollback.** If the new image cannot connect or crashes before confirming, the ESP-IDF bootloader's rollback flag reverts to the previous slot on the next boot. The board comes back on 1.3.0 and reports `ROLLED_BACK_AUTOMATIC`. You can demo this deliberately by flashing a deliberately broken "bad build".
9. **Resume.** Chunk index and running hash live in NVS. Pull the USB power mid-download, plug it back in, and it resumes at the last stored chunk. This is the most convincing hardware moment in the demo.

**What makes this identical to the software path:** same topics, same manifest, same signature, same reason codes. The backend has no ESP32-specific branch. `device_type` exists only for display.

---

## 11. Why this is real OTA and not a file-sharing app

| Property | File sharing (what to avoid) | CONVOY (what real vehicles do) |
|---|---|---|
| Trust | Whoever has the link gets the file | Ed25519-signed manifest; device trusts the key, not the channel |
| Targeting | User picks a recipient | Server selects a **cohort** from a fleet by model/tag/version |
| Preconditions | None | Battery, network quality, online, version-eligibility gate every device |
| Delivery unit | Whole file, restart on failure | Hash-verified chunks, resumable at chunk granularity |
| Install | Overwrite in place | A/B dual-slot, previous image preserved |
| Failure | Retry the download | Automatic revert to last known-good, reason-coded |
| Rollout | Everyone at once | Canary → staged batches → adaptive resizing → abort guard |
| Versioning | Filename | `version_code` with anti-rollback enforcement |
| Evidence | None | Append-only per-device event log, campaign audit |

The reference frame here is the Uptane security framework (director + image repository roles, which is what the signed-manifest-per-device design mirrors), AUTOSAR's UCM update states, and UNECE R156, which legally requires a Software Update Management System with exactly this kind of traceability for type-approved vehicles. Citing these in the report is what separates this from "a Dropbox with extra steps".

---

## 12. Scaling from 18 to 10,000 devices

| Bottleneck | Handling |
|---|---|
| Broker fan-in | MQTT shared subscriptions; add backend replicas, broker balances automatically |
| Backend state | Bridge and API are stateless; all state in Postgres/Redis |
| Single orchestrator | One leader per campaign via Redis lease; campaigns shard across workers by `campaign_id` hash |
| Event write volume | Batched inserts (`COPY`/multi-row), monthly partitions on `device_events`, retention policy |
| Health sample volume | Downsample to 1-minute aggregates after 24 h; keep raw only for devices in an active campaign |
| Dashboard | Server aggregates counts; the browser never receives 10,000 individual rows — it gets a fleet histogram plus the visible page of tiles |
| WebSocket fan-out | Redis pub/sub, one channel per campaign; coalesce deltas at 20 Hz instead of per-event |
| Chunk bandwidth | Per-campaign concurrency cap; large images move to signed HTTPS URLs carried in the same manifest field |

---

## 13. Repository layout (software and hardware kept fully separate)

```
convoy/
├── software/
│   ├── backend/          FastAPI, orchestrator, adaptive engine, MQTT bridge, Alembic
│   ├── dashboard/        Next.js app
│   ├── tcu-sim/          Dockerised simulated TCU + Dockerfile + fleet profiles
│   ├── deploy/           docker-compose.admin.yml, .laptopB/C/D.yml, .env.example
│   └── tools/            keygen, firmware builder, load generator, seed scripts
├── hardware/
│   ├── esp32-tcu/        PlatformIO project: src/, include/, partitions.csv, sdkconfig
│   ├── wiring/           schematic, pin map, BOM
│   └── flashing/         provisioning script, per-board config templates
└── docs/                 PRD.md, Architecture.md, Rules.md, Design.md, Memory.md
```

Two independent build systems, one shared protocol spec (`docs/protocol.md` + `software/backend/app/schemas/`). Neither tree imports from the other.
