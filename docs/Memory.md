# Memory — CONVOY project state

Living context file. Read this first at the start of any work session. Update it at the end of every session. Anything not written here does not exist.

---

## Current state

**Phase:** 2B-2 — MQTT bridge landing. Transport, schema, and engine core all proven.
**Last updated:** 2026-08-16
**Next action:** finish bridge verification (events written to Postgres for all 15 devices), then build the orchestrator: firmware upload and signing, campaign creation, batch scheduling, and the adaptive engine wired to real devices.

### Progress ledger

| Area | State |
|---|---|
| Docs (PRD, Architecture, Rules, Design, Memory, Setup, Deployment, Environment, Runbook) | Done |
| HiveMQ Cloud cluster + 5 credentials | Done |
| Ed25519 keypair + keygen tool | Done |
| Transport proven across 3 networks (Mac + B + C + D) | Done |
| Simulated TCU + Dockerfile + compose profiles B/C/D | Done |
| Adaptive engine + 38 passing tests | Done |
| Firmware packaging, manifest signing, 5 attack tests | Done |
| Postgres schema + Alembic migrations (9 tables) | Done |
| Async session management + preflight health check | Done |
| MQTT bridge: topics, schemas, ingestion, reaper | Done, verifying |
| Firmware upload + campaign creation API | Not started |
| Orchestrator (batch scheduling, eligibility gate) | Not started |
| OTA handlers on the device (offer/chunk/resume/result) | Not started |
| REST API + WebSocket hub | Not started |
| Next.js dashboard | Not started |
| Analytics page | Not started |
| ESP32 firmware | Not started |
| Load test to 10k | Not started |

---

## Locked decisions

| # | Decision | Reason | Date |
|---|---|---|---|
| D1 | MQTT over TLS via HiveMQ Cloud as the only transport | Requirement 18 — devices on arbitrary networks, no inbound ports | 2026-08-12 |
| D2 | **Mac is the admin (Laptop A)**; Windows machines run TCU containers | Admin carries Postgres/bridge/dashboard; POSIX toolchain matters most there | 2026-08-12 |
| D3 | Ed25519 signatures over a canonical manifest | ~2 ms verify on ESP32; RSA too slow and too large | 2026-08-12 |
| D4 | Firmware delivered as 8 KiB chunks over MQTT, not HTTP | No inbound ports, no public file server; chunk-level resume falls out free | 2026-08-12 |
| D5 | `device_events` append-only; `devices`/`campaign_targets` are projections | Requirement 14 auditability is otherwise unprovable | 2026-08-12 |
| D6 | Adaptive engine is a pure function (AIMD + hysteresis + EWMA) | Testable, reproducible on demo day, explainable in the report | 2026-08-12 |
| D7 | `SKIPPED_*` outcomes excluded from the failure rate | Refusing to update a low-battery device is correct behaviour, not a failure | 2026-08-12 |
| D8 | Python for both backend and simulated TCU | One protocol implementation, one test suite | 2026-08-12 |
| D9 | ESP-IDF over Arduino core for ESP32 | Need real `esp_ota_ops` A/B slots and bootloader rollback | 2026-08-12 |
| D10 | Design language "instrument panel", light-first, signature = Convoy Strip | Projector legibility; adaptive decision visible as geometry | 2026-08-12 |
| D11 | **Python 3.12 pinned**, not 3.13/3.14 | pydantic-core (Rust/PyO3), asyncpg and cryptography publish wheels only for supported interpreters; newer means source builds that fail | 2026-08-15 |
| D12 | Stay on HiveMQ **Frankfurt**; no APAC region exists on free or Starter tier | Verified in current docs: region choice is Starter-only, and only Oregon or Frankfurt. Frankfurt is the better of the two for India | 2026-08-16 |
| D13 | Shared MQTT subscriptions **default OFF** (`MQTT_USE_SHARED_SUBSCRIPTION`) | Plain wildcard works on every broker tier; shared subs are the documented scale path, opt-in after verification | 2026-08-16 |
| D14 | Health samples go to `device_health_samples`, **never** to `device_events` | 15 devices = 3 writes/s; 10,000 = 2,000/s. The audit log must stay signal, not telemetry | 2026-08-16 |
| D15 | Demo campaign runs `abort_threshold = 0.50`, not the default 0.40 | The scripted 2-of-5 failure is exactly 40%, which would ABORT rather than shrink. Both behaviours asserted in tests | 2026-08-15 |
| D16 | `device_events.msg_id` uses a **plain** unique index, not partial | Postgres only matches a partial index for ON CONFLICT if the predicate is repeated; plain index keeps the insert simple, NULLs are distinct anyway | 2026-08-16 |

## Open questions

| # | Question | Blocking? |
|---|---|---|
| Q1 | HiveMQ free tier connection cap is 100 — fine for 18 devices. 10k load test needs self-hosted EMQX in Docker | No |
| Q2 | Real Li-ion cell + INA219 on at least one ESP32, or battery as an NVS value? | No |
| Q3 | Confirm all three ESP32 modules have **4 MB flash** (`esptool.py flash_id`) — 2 MB cannot hold two OTA slots | **Yes**, blocks hardware work |
| Q4 | ~~`tcu_D_004` fail at offer time or mid-transfer?~~ **Resolved:** mid-transfer via `FAILURE_MODE=low_battery`, so it counts toward the failure rate | Closed |
| Q5 | Venue network policy on port 8883 — WSS on 8884 is the fallback | Test before demo |
| Q6 | Per-topic ACLs ARE available on HiveMQ Serverless free (topic filter permissions). Configure `convoy/v1/d/tcu_B_+/#` style filters per credential before the demo | No, but strong viva material |

---

## Environment notes

```
Laptop A (Mac, sumit)      admin: postgres, redis, bridge, dashboard, projector. ZERO devices.
                           repo: ~/Documents/Major-Project/convoy
                           venv: convoy/.venv   (Python 3.12.14)
Laptop B (Win, Shravan)    tcu_B_001..005   battery 78-90, network 4-5, healthy
Laptop C (Win, Shashank)   tcu_C_001..005   healthy
Laptop D (Win, sumit)      tcu_D_001..003   healthy
                           tcu_D_004        battery 8,  FAILURE_MODE=low_battery
                           tcu_D_005        network 1,  FAILURE_MODE=poor_network
                           repo: ~/Projects/convoy   (moved OUT of OneDrive)
Broker                     b8ec2aadc24f4720a99bfd631ca7872c.s1.eu.hivemq.cloud:8883
                           credentials: convoy_server, convoy_device_b/c/d, convoy_esp32
Table                      esp32_001, esp32_002 healthy; esp32_003 battery 8
```

**Measured:** round-trip latency to the broker is 370–790 ms. That is four WAN legs (server → broker → device → broker → server) at roughly 180 ms each, India ↔ Frankfurt. It is geography, not a defect. The "under 100 ms dashboard" success criterion means **server event → browser paint**, both local; it does not and cannot mean device → browser. Say this explicitly in the report.

---

## Demo script (rehearse this exact sequence)

1. Show the dashboard with 18 devices across five networks. Point out that no two machines share a LAN.
2. Publish firmware `1.4.0`. Show the signature and hash in the firmware library.
3. Create the campaign: batch size 5, min battery 30%, min network quality 2, canary 2, **abort threshold 0.50**.
4. Start. Canary batch of 2 passes.
5. Batch 02 of 5 includes `tcu_D_004` and `tcu_D_005`. Both fail. `esp32_003` fails visibly on the table.
6. **Point at the terminal** as the banner prints `SHRINK_HIGH_FAILURE 5 -> 2`. Point at the Convoy Strip narrowing.
7. Remaining devices complete in batches of 2.
8. Kill one container mid-download, restart it, show progress resuming from 55%, not 0%.
9. Publish a tampered image with a forged signature; every device rejects it with `FAILED_SIGNATURE_INVALID`.
10. Analytics page: batch-size step chart with the annotated decision, failure taxonomy, per-device table.
11. Open the device drawer for `tcu_D_005` and read its event timeline aloud — this answers "what happened to device X during campaign Y and why".
12. Hit **Roll back fleet**. Watch versions revert to 1.3.0.

---

## Known traps (already bitten, or will)

- **`docker run --env-file` is a dumb parser.** It does not strip inline comments, quotes, or trailing spaces — everything after `=` becomes the value verbatim. Cost an hour of "Not authorized". Comments live on their own lines. The agent now validates and exits with a clear message.
- **Docker discards changes made to a path after `VOLUME` declares it.** A `chown /data` placed after `VOLUME ["/data"]` was silently thrown away, leaving the volume root-owned. Create and chown before, or omit `VOLUME` and let compose declare it.
- **Alembic autogenerate does not compare index predicates.** It saw a partial and a plain unique index on the same column as identical and emitted an empty migration. A suspiciously empty migration after a real change is this blind spot — write the SQL by hand.
- **`ON CONFLICT` cannot match a partial index** unless the statement repeats the predicate. See D16.
- **Catching `DBAPIError` for "database unreachable" is wrong.** `ProgrammingError` is a subclass, so a schema fault was reported as "is Postgres running?" while Postgres was fine. Connection failures are `OperationalError` / `InterfaceError` only.
- **Never put the repo inside OneDrive / Dropbox / Google Drive.** Files On-Demand leaves placeholder stubs; Docker read a 31-byte `Dockerfile` and failed with `invalid file request`. Also avoid spaces in the path.
- **`pip install <package>` directly drifts your machine from everyone else's.** Add to the right `requirements.txt` first, then install from the file. Installing alembic bare pulled SQLAlchemy 2.0.52 over the pinned 2.0.32 and skipped psycopg2 entirely.
- Counting `SKIPPED` as failure silently breaks the adaptive maths. See D7.
- Retained `ota/offer` messages would re-trigger an old campaign on reconnect. Never retain offers.
- Base64 inflates an 8 KiB chunk to ~11 KiB. Budget against the real broker payload cap.
- Two backend instances both running the orchestrator would double-offer. Redis leader lease is mandatory even for a single-instance demo.
- Clock skew between laptops makes event ordering look wrong. Order by server-assigned `BIGSERIAL`, never a device timestamp.
- Keepalive timeouts drop the broker connection every 2–5 minutes on an idle link. Lowering keepalive from 30 s to 20 s helped but did not eliminate it. Reconnection is automatic, costs about 3 s, and state is re-announced. Revisit only if it coincides with a live batch.
- ESP32 heap exhaustion if a chunk is buffered as a string before decoding. Decode base64 straight into the OTA write buffer.

---

## Session log

**2026-08-12 — Session 1.** Requirements analysed. Stack fixed (D1–D10). Five planning documents written. Admin machine chosen. Repository layout agreed with a hard split between software and hardware.

**2026-08-14 — Session 2.** Phase 1 transport layer. HiveMQ cluster and five credentials created. `link_check.py` and `tcu_agent.py` written. First cross-network proof: Laptop B on a different WiFi appeared on the Mac's roster. Two bugs found and fixed — env-file parsing and Docker volume ownership. Added broadcast `announce` so a late-starting server can learn the fleet's versions; added `--forget` to clear retained ghosts.

**2026-08-15 — Session 3.** Phase 2A. Adaptive engine, crypto, firmware packaging; 38 tests passing including the exact demo scenario and five attack tests. Discovered D15: the scripted 2-of-5 failure sits exactly at the abort threshold and would halt rather than shrink. Python 3.14 → 3.12 detour (D11).

**2026-08-16 — Session 4.** Phase 2B. Postgres schema with the append-only event log, nine tables, migrations applied. Async session layer with startup preflight. MQTT bridge: topic router, Pydantic message schemas, idempotent ingestion, offline reaper. Confirmed D12 (no APAC region) and Q6 (per-topic ACLs available on the free tier — corrects an earlier wrong claim). Fixed the partial-index / ON CONFLICT fault (D16) and the misleading error classification. Laptop D moved out of OneDrive to `~/Projects/convoy`; all five containers connected and announced their configured failure profiles correctly.