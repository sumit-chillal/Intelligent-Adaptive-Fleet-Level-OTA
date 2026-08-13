# Rules — CONVOY

Binding engineering rules for this project. If a rule and a convenience conflict, the rule wins. If a rule must be broken, record it in Memory.md with a reason and a date.

---

## 1. Non-negotiable architecture rules

**R1 — No LAN assumptions.** No code may contain a private IP, `localhost` device address, mDNS discovery, or subnet scan for device communication. Every device–server interaction goes through the broker. Violating this fails Requirement 18 outright.

**R2 — No direct coupling.** The server never opens a socket to a device. Devices never call the REST API. If you need a new interaction, add a topic, not an endpoint.

**R3 — The signing key never leaves the admin machine.** Not in git, not in a Docker image, not in an env file that is committed, not in the frontend. `.env` is gitignored; `.env.example` holds placeholder values only. Devices hold the **public** key only.

**R4 — No unsigned install path.** There is exactly one code path that writes firmware to a slot, and it is downstream of signature verification. No debug flag may bypass it. A `--skip-verify` option must not exist.

**R5 — `device_events` is append-only.** No `UPDATE`, no `DELETE`. Corrections are new events. Any state you can see in the UI must be derivable from this table.

**R6 — Device config comes from the environment.** Simulated TCUs read identity and health profile from env vars or a mounted config file. Adding a device or changing a health profile must never require editing a `.py` file or rebuilding an image.

**R7 — One protocol, two implementations, zero divergence.** Message schemas live in one place and are mirrored in the ESP32 header. Any topic or field change updates `docs/protocol.md`, the Pydantic schema, the simulated TCU, and the ESP32 firmware in the same commit.

**R8 — The adaptive engine is a pure function.** No DB calls, no clock reads, no I/O inside `decide()`. Time and state are passed in. This is what makes it testable and what makes the demo reproducible.

---

## 2. Code rules

- Python: 3.11, `ruff` + `black` (line length 100), full type hints on public functions, `mypy --strict` on `app/core` and `app/schemas`.
- TypeScript: `strict: true`, no `any` in committed code, ESLint + Prettier.
- C (ESP32): ESP-IDF style, no dynamic allocation in the OTA path, every `esp_err_t` checked, `-Wall -Wextra` clean.
- No secrets, tokens, or device credentials in any source file.
- No `print()` in backend code — `structlog` only. The one exception is the adaptive-decision banner, which is a deliberate demo artefact and lives in a single named function.
- Every function that can fail returns a reason code from the shared `ReasonCode` enum. No free-text error strings in persisted data.
- Docstring the *why*, not the *what*. The crypto module must explain its threat model inline (this is a graded requirement).

## 3. Reason code taxonomy (closed set — do not invent new strings)

```
SUCCESS
SKIPPED_INELIGIBLE_LOW_BATTERY
SKIPPED_INELIGIBLE_POOR_NETWORK
SKIPPED_ALREADY_ON_TARGET
SKIPPED_OFFLINE
FAILED_LOW_BATTERY
FAILED_POOR_NETWORK
FAILED_TIMEOUT
FAILED_CHUNK_HASH_MISMATCH
FAILED_IMAGE_HASH_MISMATCH
FAILED_SIGNATURE_INVALID
FAILED_ANTI_ROLLBACK
FAILED_FLASH_WRITE
FAILED_INSUFFICIENT_SPACE
FAILED_MAX_ATTEMPTS
ROLLED_BACK_AUTOMATIC
ROLLED_BACK_MANUAL
ABORTED_BY_OPERATOR
ABORTED_FAILURE_STORM
```

`SKIPPED_*` never counts toward the failure rate. `FAILED_*` always does. Mixing these up silently breaks the adaptive engine's maths — this is the single most likely bug in the project.

## 4. MQTT rules

- QoS 1 on everything. QoS 0 loses updates; QoS 2 is not worth the round trips.
- Every message carries `schema`, `msg_id` (UUIDv4), `device_id`, and `ts`.
- All handlers are **idempotent**. Assume every message can arrive twice; dedupe on `msg_id` with a bounded LRU.
- `retain` only on `.../status`. Never retain chunks or offers — a retained offer would re-trigger an old campaign on reconnect.
- Payload ≤ 12 KiB. Chunks are 8 KiB raw ≈ 11 KiB base64.
- Every client sets an LWT before connecting. No exceptions.
- Topic strings are built by a single helper module, never string-concatenated inline.

## 5. Database rules

- Schema changes go through Alembic. `create_all()` is banned outside tests.
- Every timestamp is `TIMESTAMPTZ` in UTC. The UI converts for display.
- Money-free project, but the same discipline: no floats for anything that gets compared for equality. Failure rates are computed from integer counts at read time.
- Foreign keys everywhere, `ON DELETE RESTRICT` for firmware and campaigns. History is never orphaned.

## 6. Security rules

- TLS on every broker connection. Certificate verification is on. Never `tls_insecure_set(True)`, not even "just for testing".
- One MQTT credential per device. No shared fleet password.
- Broker ACLs: a device may publish only to `convoy/v1/d/{own_id}/#` and subscribe only to `convoy/v1/s/{own_id}/#`.
- Rate limit: a device that publishes more than 20 messages/second is throttled and flagged.
- Firmware uploads are validated for size and magic bytes before hashing.
- The dashboard's mutating endpoints require an admin token even in the demo build.

## 7. Testing rules

- The adaptive engine has a table-driven test covering every branch plus the exact demo scenario (5 devices, 2 failures → 5 → 2). If that test fails, the demo fails.
- Signature verification has a negative test: tampered payload, wrong key, replayed nonce, downgraded `version_code`. All four must be rejected.
- Resume has an integration test that kills the device process at chunk 17 and asserts the next transfer starts at 18.
- One end-to-end test against a real broker with 3 containers before every demo rehearsal.
- No PR merges with a failing test or a skipped test lacking a linked note in Memory.md.

## 8. Demo-day operational rules

1. Rehearse the full run end-to-end at least twice, once on a hotspot, before the real thing.
2. Every laptop must have the broker credentials and the Docker image **pre-pulled**. Do not build images on demo day.
3. Have a phone hotspot ready as the fallback network for every machine.
4. Record a screen capture of a successful run the night before. If the venue's network dies, you still have a demo.
5. Seed the database with one completed historical campaign so the analytics page is never empty on first load.
6. Keep the backend terminal visible on the projector next to the dashboard — the adaptive decision print-out is a graded success criterion and it is the single most persuasive thing on screen.
7. `esp32_003` and `tcu_D_004/005` failing is the *feature*. Say so out loud before it happens, so the room reads it as design rather than an accident.

## 9. Git rules

- `main` is always demo-able. Feature branches, squash merge.
- Conventional commits: `feat(orchestrator):`, `fix(esp32):`, `docs(prd):`.
- Tag a release before every rehearsal: `demo-rehearsal-1`, `demo-final`.
- Firmware binaries used in the demo are committed under `software/tools/firmware_samples/` so the demo cannot be broken by a missing artefact.

## 10. Definition of done (per feature)

A feature is done when: it works over the internet on two different networks; it writes a reason-coded event; it appears correctly in the dashboard within 100 ms; it has a test; it is documented in `protocol.md` if it touches the wire; and Memory.md records the decision if it changed one.
