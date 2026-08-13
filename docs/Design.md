# Design — CONVOY dashboard & device UX

---

## 1. What this interface is for

One job: **let an operator standing ten feet from a projector know, in one glance, whether the rollout is safe — and if not, which devices are hurting it and what the system did about it.**

That framing rejects two tempting defaults. It is not an admin CRUD panel (tables of rows with a status column), and it is not a generic "telemetry dashboard" (a wall of gauges). It is closer to a **flight-following board or a rail signalling panel**: a small number of very large, very legible states, with detail available on demand but never competing for attention.

Audience: release engineers and, on demo day, an evaluation panel who have never seen the system before and will form an opinion in about eight seconds.

---

## 2. Design direction: *Instrument, not app*

The vernacular is borrowed from the subject's own world — vehicle instrumentation and dispatch boards. Silkscreened labels, hairline rules, condensed uppercase legends, signal colours that mean exactly one thing each, and numbers that are allowed to be enormous.

Deliberately avoided: the cream-and-serif editorial look, the near-black-plus-one-acid-accent "hacker console", and the newspaper-column layout. This project already has a real visual language available to it; borrowing a trendy one would be a wasted opportunity.

### Colour — "Hangar Daylight"

A cool concrete canvas rather than black, so it survives a bright lecture hall and a projector with poor black levels. Signals are a *system*, not one accent: each hue is reserved for exactly one meaning and never used decoratively.

```css
--concrete:  #E7EAEE;  /* canvas — cool grey, reads as poured floor       */
--panel:     #F9FAFB;  /* raised surfaces                                  */
--ink:       #0F1319;  /* primary text                                     */
--ink-mute:  #5B6472;  /* secondary text, labels                           */
--rule:      #C3CAD4;  /* 1px hairlines, the main structural device        */

--transit:   #1F5FD8;  /* IN FLIGHT — downloading / installing             */
--verified:  #0B7A5C;  /* VERIFIED — installed and self-confirmed          */
--fault:     #C22B1B;  /* FAILED                                           */
--govern:    #B45309;  /* the adaptive engine acted / campaign held        */
--dormant:   #8B94A1;  /* idle, offline, skipped                           */
```

`--govern` amber is reserved **exclusively** for the adaptive engine and operator holds. Nothing else on the screen is ever amber. That single reservation is what makes the system's intelligence visible: when amber appears, the machine made a decision.

Dark variant ("Hangar Night") swaps `--concrete` to `#141922` and `--panel` to `#1B222C` with the same signal hues lifted 8% in lightness. Toggle only; light is the default because it projects better.

### Type

| Role | Face | Use |
|---|---|---|
| Display | **Archivo Expanded** 600/700 | Campaign state word, the one huge number, section legends |
| Body / UI | **IBM Plex Sans** 400/500/600 | Everything readable |
| Data | **IBM Plex Mono** 400/500 | Device IDs, versions, SHA-256, chunk indices, log lines, timestamps |

Rules: device IDs and version strings are **always** mono, everywhere, without exception — they are identifiers, and monospacing is how the eye scans a column of them. Legends are Archivo Expanded, uppercase, 11px, `letter-spacing: 0.14em`. Body never goes below 13px. The campaign state word (`ROLLING`, `HELD`, `COMPLETE`, `ABORTED`) is 72px and is the largest thing on the page — from ten feet, that word alone tells you the answer.

Scale: 11 / 13 / 15 / 18 / 24 / 40 / 72.

### Structure

Hairline rules and silkscreen legends, not cards with shadows. Panels are separated by 1px `--rule` lines on a flat surface; elevation is used only for the device drawer and modals. Border radius 2px — instrument panels are not rounded. No gradients anywhere except the one place noted below.

Numbering (01 / 02 / 03) is used in exactly one place: batch indices, because batches genuinely are an ordered sequence and the order carries meaning. Nowhere else.

---

## 3. Signature element: the Convoy Strip

The one thing this dashboard will be remembered for.

A horizontal strip across the full width of the live campaign view. Each batch is a segment whose **width is proportional to its batch size**, filled with one small square per device, coloured by outcome. Batches are laid left to right in execution order.

```
BATCH   01 canary      02                        03            04
      ┌────────┬───────────────────────────┬──────────┬──────────────┐
      │ ▪ ▪    │ ▪ ▪ ▪ ✕ ✕                 │ ▪ ▪      │ ▪ ▪ ▪        │
      └────────┴───────────────────────────┴──────────┴──────────────┘
        2 ok        3 ok · 2 failed            2 ok        in flight
                    ▲
                    └─ ⬤ 40% FAILURE · BATCH SIZE 5 → 2 · SHRINK_HIGH_FAILURE
```

When the engine shrinks the batch, the next segment is **visibly narrower**. The adaptive behaviour — the intellectual core of the whole project — becomes a shape you can see from the back of the room, with no chart-reading required. An amber decision chip is stamped under the boundary where the change happened.

This replaces the obvious choice (a progress bar plus a line chart of batch size). A progress bar shows *how far*; the Convoy Strip shows *how the system is thinking*.

---

## 4. Screens

### 4.1 Live campaign (the projector screen)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ CONVOY   ·  CAMPAIGN  fleet-1.4.0-rollout            ◉ live   [Hold] [Abort]│
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│   ROLLING            11/18 UPDATED    2 FAILED    BATCH SIZE  2  ↓ from 5   │
│   (72px display)     (40px mono)      (fault)     (amber, animates)         │
│                                                                            │
├────────────────────────────────────────────────────────────────────────────┤
│ CONVOY STRIP                                                               │
│ [ the signature element ]                                                  │
├───────────────────────────────────────┬────────────────────────────────────┤
│ FLEET                          filter │ DECISION LOG                       │
│ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐  │ 14:22:07 batch 02 closed 3/5       │
│ │B001││B002││B003││B004││B005││C001│  │ 14:22:07 ⬤ SHRINK_HIGH_FAILURE     │
│ │ ▰▰ ││ ▰▰ ││ ▰▰ ││ ▰▰ ││ ▰▰ ││ 62%│  │          batch size 5 → 2          │
│ └────┘└────┘└────┘└────┘└────┘└────┘  │ 14:21:58 tcu_D_005 FAILED          │
│  ... virtualised, 18 or 18,000        │          POOR_NETWORK q=1          │
└───────────────────────────────────────┴────────────────────────────────────┘
```

Device tile: mono ID, a 4-bar battery glyph, a network-quality glyph, current version, and a fill state. In-flight tiles show a thin `--transit` progress fill rising from the bottom of the tile — so a wall of tiles literally *fills up* as the fleet updates.

### 4.2 Device drawer (right slide-over)

Identity block (mono) → live battery/network sparkline → version: current → target → chunk progress with `17/32` in mono → **event timeline**, newest first, each row `HH:MM:SS · EVENT · REASON_CODE` with the battery and network reading captured at that instant. This drawer is the visual proof of Requirement 14: everything needed to answer "what happened to this device and why" is on one surface.

### 4.3 Campaign wizard

Four steps, each a full-width panel, no modal-in-modal: firmware → targeting (live count of matched devices updates as you type the selector) → rollout policy (batch sizes, thresholds, canary) → **dry run preview**, which lists exactly which devices would be skipped as ineligible and why, *before* you commit. The preview is the difference between a tool an engineer trusts and one they don't.

### 4.4 Analytics

- Batch size over time as a **step chart** (never a smooth line — batch size changes discretely, and a curve would be a lie), with amber annotation markers at each decision.
- Outcome donut: verified / failed / skipped / rolled back.
- Failure taxonomy horizontal bars, sorted, reason codes in mono.
- Per-device outcome table, virtualised, CSV export.
- Duration histogram: how long devices took to update.

### 4.5 Firmware library

Version rows in mono with the SHA-256 truncated to 12 chars, a signature-verified checkmark, size, chunk count, and which campaigns used it.

---

## 5. Motion

One orchestrated moment, everything else still.

**The decision moment.** When an adaptive decision arrives: the batch-size number in the header cross-fades to the new value, an amber underline sweeps left-to-right beneath it over 400 ms, the decision chip stamps into the Convoy Strip with a 120 ms scale-in, and the strip's next segment animates to its new narrower width over 500 ms with a cubic ease-out. Nothing else on the screen moves during that beat.

Everything else: 120 ms colour transitions on state change, no entrance animations on tiles (a fleet of 10,000 tiles cascading in would be nauseating and slow), no skeleton shimmer — empty states are text.

`prefers-reduced-motion: reduce` removes the sweep and the width tween; the values still change, instantly.

---

## 6. States, copy, and voice

Errors and empty states are directions, not apologies.

| Situation | Copy |
|---|---|
| No devices online | `No devices connected. Start a TCU container or power on a board — they appear here within a second of connecting.` |
| No campaigns yet | `No campaigns yet. Publish a firmware version, then create a campaign to roll it out.` |
| WebSocket dropped | Persistent bar: `Live updates disconnected. Reconnecting…` then `Reconnected — resynced at 14:22:31.` |
| Device failed | Tile turns `--fault`; drawer shows `Failed at chunk 17/32 — network quality 1, below the campaign minimum of 2.` |
| Campaign aborted | `Campaign aborted. Failure rate reached 40% in batch 02, above the 40% abort threshold. 7 devices were not attempted.` |

Buttons state what happens: `Start campaign`, `Hold rollout`, `Resume rollout`, `Abort campaign`, `Roll back fleet`. The action keeps its name through the flow — `Hold rollout` produces the state word `HELD`. Destructive actions (`Abort`, `Roll back fleet`) require typing the campaign name to confirm, because on demo day someone will click the wrong thing.

Never say "webhook", "MQTT topic", or "job row" in the UI. Say device, update, batch, campaign.

---

## 7. Quality floor

Responsive to 768px (the fleet grid reflows to two columns, the Convoy Strip scrolls horizontally). Visible keyboard focus rings using `--transit`. All colour-coded states also carry a shape or label — failed tiles show `✕`, verified show `▪` — so the demo does not fall apart for a colour-blind evaluator or a badly calibrated projector. Contrast ≥ 4.5:1 for all text. `aria-live="polite"` on the decision log so a screen reader announces adaptive decisions.

---

## 8. Device-side UX (ESP32)

The board is a display too, and it should follow the same signal semantics.

**LEDs** — green `--verified` (idle / verified), blue `--transit` (offered / downloading / installing), red `--fault` (failed). Never two at once. Slow blue pulse while downloading, solid blue while flashing.

**OLED, 128×64:**

```
┌──────────────────────────┐
│ esp32_001        [batt]  │   line 1: device id + battery glyph
│ v1.3.0 → v1.4.0          │   line 2: version transition
│ ████████████░░░░░  62%   │   line 3: chunk progress bar
│ CHUNK 20/32              │   line 4: mono detail
└──────────────────────────┘
```

Failure screen inverts to a solid block with the reason spelled out plainly, because a judge will walk over and read it from 30 cm:

```
┌──────────────────────────┐
│      UPDATE ABORTED      │
│   BATTERY 8% < MIN 30%   │
│    esp32_003  v1.3.0     │
└──────────────────────────┘
```

Same reason-code vocabulary as the dashboard. A person reading the board and a person reading the projector see the same words for the same event — which is, in the end, the whole point of the design system.
