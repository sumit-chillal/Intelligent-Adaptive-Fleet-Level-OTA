# Memory — CONVOY project state

Living context file. Read this first at the start of any work session. Update it at the end of every session. Anything not written here does not exist.

---

## Current state

**Phase:** 0 — planning complete, no code written.
**Last updated:** 2026-08-12
**Next action:** scaffold `software/backend` and `software/tcu-sim`, generate the Ed25519 keypair, get one container talking to HiveMQ Cloud end-to-end before writing anything else.

### Progress ledger

| Area | State |
|---|---|
| Docs (PRD, Architecture, Rules, Design, Memory) | Done |
| HiveMQ Cloud account + credentials | Not started |
| Ed25519 keypair + keygen tool | Not started |
| Postgres schema + Alembic migrations | Not started |
| MQTT bridge | Not started |
| Orchestrator + adaptive engine | Not started |
| REST API + WebSocket hub | Not started |
| Simulated TCU + Dockerfile | Not started |
| Compose profiles for laptops B/C/D | Not started |
| Next.js dashboard | Not started |
| Analytics page | Not started |
| ESP32 firmware | Not started |
| Load test to 10k | Not started |

---

## Locked decisions

| # | Decision | Reason | Date |
|---|---|---|---|
| D1 | MQTT over TLS via HiveMQ Cloud as the only transport | Requirement 18 — devices on arbitrary networks, no inbound ports, NAT-friendly | 2026-08-12 |
| D2 | **Mac is the admin (Laptop A)**; Windows machines run TCU containers | Admin carries Postgres/Redis/FastAPI/Next; POSIX toolchain and predictable Docker volumes matter most there | 2026-08-12 |
| D3 | Ed25519 signatures over a canonical manifest | ~2 ms verify on ESP32; RSA is too slow and too large | 2026-08-12 |
| D4 | Firmware delivered as 8 KiB chunks over MQTT, not HTTP | No inbound ports, no public file server, and chunk-level resume falls out for free | 2026-08-12 |
| D5 | `device_events` is append-only; `devices`/`campaign_targets` are projections | Requirement 14 auditability is otherwise unprovable | 2026-08-12 |
| D6 | Adaptive engine is a pure function (AIMD + hysteresis + EWMA) | Testable, reproducible on demo day, explainable in the report | 2026-08-12 |
| D7 | `SKIPPED_*` outcomes excluded from the failure rate | Refusing to update a low-battery device is correct behaviour, not a failure; counting it would make the engine shrink for the wrong reason | 2026-08-12 |
| D8 | Python for both backend and simulated TCU | One protocol implementation, one test suite | 2026-08-12 |
| D9 | ESP-IDF over Arduino core for ESP32 | Need real `esp_ota_ops` A/B slots and bootloader rollback | 2026-08-12 |
| D10 | Design language is "instrument panel", light-first, signature = Convoy Strip | Projector legibility; makes the adaptive decision visible as geometry | 2026-08-12 |

## Open questions

| # | Question | Owner | Blocking? |
|---|---|---|---|
| Q1 | Which HiveMQ tier — free (100 connections) or pay-as-you-go? Free is enough for 18 devices; the 10k load test needs self-hosted EMQX | — | No, but decide before the scale test |
| Q2 | Do we wire a real Li-ion cell + INA219 to at least one ESP32, or keep battery as an NVS value? A real reading is far more convincing | — | No |
| Q3 | ESP32 module flash size — confirm 4 MB on all three boards before designing the partition table | — | **Yes**, blocks hardware work |
| Q4 | Should the demo make `tcu_D_004` fail at offer time (`SKIPPED`) or mid-transfer (`FAILED`)? Mid-transfer is required for it to drive the adaptive decision | — | **Yes**, blocks the demo script |
| Q5 | Campus network policy on port 8883 — test early; WSS on 443 is the fallback | — | **Yes**, test in week 1 |

---

## Environment notes

```
Laptop A (Mac)   admin: postgres, redis, fastapi, next.js, projector. ZERO devices.
Laptop B (Win)   tcu_B_001..005   battery 78–90, network 4–5, all healthy
Laptop C (Win)   tcu_C_001..005   healthy
Laptop D (Win)   tcu_D_001..003   healthy
                 tcu_D_004        battery 8   → FAILED_LOW_BATTERY
                 tcu_D_005        network 1   → FAILED_POOR_NETWORK
Table            esp32_001, esp32_002 healthy
                 esp32_003        battery 8   → FAILED_LOW_BATTERY (red LED + OLED)
```

Broker is the only shared point. All five networks may differ.

---

## Demo script (rehearse this exact sequence)

1. Show the fleet dashboard with 18 tiles connected across five networks. Point out that no two machines share a LAN.
2. Publish firmware `1.4.0`. Show the signature and hash in the firmware library.
3. Create the campaign: batch size 5, min battery 30%, min network quality 2, canary 2.
4. Start. Canary batch of 2 passes.
5. Batch 02 of 5 includes `tcu_D_004` and `tcu_D_005`. Both fail. `esp32_003` fails visibly on the table.
6. **Point at the terminal** as the adaptive banner prints `SHRINK_HIGH_FAILURE 5 → 2`. Point at the Convoy Strip narrowing.
7. Remaining devices complete in batches of 2.
8. Kill one container mid-transfer, restart it, show the progress bar resuming from 55%, not 0%.
9. Publish a tampered image with a forged signature; every device rejects it with `FAILED_SIGNATURE_INVALID`.
10. Open the analytics page: batch-size step chart with the annotated decision, failure taxonomy, per-device table.
11. Open the device drawer for `tcu_D_005` and read the full event timeline aloud — this answers "what happened to device X during campaign Y and why".
12. Hit **Roll back fleet**. Watch versions revert to 1.3.0.

---

## Known traps (things that will bite)

- Counting `SKIPPED` as failure. Breaks the adaptive maths silently. See Rule R-3.3 in Rules.md.
- Retained `ota/offer` messages re-triggering an old campaign when a device reconnects. Never retain offers.
- Base64 inflating an 8 KiB chunk past the broker's payload limit. Budget ~11 KiB and test against the real broker's cap.
- Docker Desktop on Windows sleeping containers when the laptop lid closes. Disable sleep on B/C/D on demo day.
- Two backend instances both running the orchestrator and double-offering. Redis leader lease is mandatory even for a single-instance demo.
- Clock skew between laptops making event ordering look wrong. Order by server-assigned `BIGSERIAL`, never by device timestamp.
- ESP32 heap exhaustion if a chunk is buffered as a string before decoding. Decode base64 straight into the OTA write buffer.

---

## Session log

**2026-08-12 — Session 1.** Requirements analysed. Tech stack fixed (D1–D10). Five planning documents written: PRD, Architecture, Rules, Design, Memory. Admin machine chosen (Mac). Repository layout agreed with a hard split between `software/` and `hardware/`. No code yet. Blocking items surfaced as Q3, Q4, Q5.
