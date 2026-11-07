<div align="center">

# CONVOY

### Intelligent Adaptive Fleet-Level OTA

*Adaptive fleet-level firmware delivery over an untrusted network.*  
*Eighteen devices · Two implementations · Four networks · One protocol.*

---

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-15-000000?style=flat&logo=next.js&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat&logo=postgresql&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-TLS_8883-660066?style=flat&logo=mqtt&logoColor=white)
![ESP32](https://img.shields.io/badge/ESP32-Hardware-E7352C?style=flat&logo=espressif&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Simulators-2496ED?style=flat&logo=docker&logoColor=white)
![Ed25519](https://img.shields.io/badge/Ed25519-Signed_OTA-brightgreen?style=flat)

</div>

---

## Overview

CONVOY is an end-to-end IoT OTA (Over-the-Air) update system that delivers firmware to vehicle fleets adaptively and safely. The orchestrator dynamically adjusts batch sizes in real time based on observed failure rates, catches regressions in a probation window, and rolls back automatically — verified simultaneously on **15 Docker-simulated TCUs** and **3 physical ESP32 microcontrollers** across **4 independent WiFi networks**.

All **18 software requirements** verified. Zero manual interventions required during autonomous fleet recovery.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Key Features](#key-features)
- [Dashboard](#dashboard)
- [Hardware Fleet](#hardware-fleet)
- [Adaptive Control Law](#adaptive-control-law)
- [Requirements Verification](#requirements-verification)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)

---

## System Architecture

```
                     ┌─────────────────────────────────┐
                     │       HiveMQ Cloud Broker        │
                     │  b8ec2aad…hivemq.cloud : 8883   │
                     │      TLS · Mutual Auth           │
                     └──────────────┬──────────────────┘
                                    │  MQTT pub/sub
           ┌────────────────────────┼────────────────────────┐
           │                        │                        │
  ┌────────▼────────┐    ┌──────────▼──────────┐   ┌────────▼────────────┐
  │  ESP32 Fleet    │    │  Docker TCU Fleet    │   │   FastAPI Backend   │
  │  (3 boards)     │    │   (15 processes)     │   │    Port 8000        │
  │                 │    │                      │   │                     │
  │  • OLED status  │    │  • Battery sim       │   │  ┌───────────────┐  │
  │  • NVS identity │    │  • Network quality   │   │  │  Orchestrator │  │
  │  • 90s probation│    │  • Chunk download    │   │  │  (EWMA-based) │  │
  │  • Ed25519 verify    │  • OTA client        │   │  └──────┬────────┘  │
  │  • Two-stage    │    │                      │   │         │           │
  │    eligibility  │    │                      │   │  ┌──────▼────────┐  │
  └─────────────────┘    └──────────────────────┘   │  │  Campaign     │  │
                                                    │  │  Service      │  │
                                                    │  └──────┬────────┘  │
                                                    └─────────┼───────────┘
                                                              │ REST + SSE
                                               ┌─────────────┴──────────────┐
                                               │                            │
                                     ┌─────────▼──────┐          ┌─────────▼──────┐
                                     │   PostgreSQL    │          │   Next.js 15   │
                                     │   Database      │          │   Dashboard    │
                                     │                 │          │   Port 3000    │
                                     │  • Campaigns    │          │                │
                                     │  • Devices      │          │  • 3D Landing  │
                                     │  • Batches      │          │  • Fleet Grid  │
                                     │  • Events       │          │  • Live View   │
                                     └─────────────────┘          │  • Analytics   │
                                                                   └────────────────┘
```

### Campaign State Machine

```
  CREATED ──► RUNNING ──► COMPLETE
                │               ▲
                │  (failure ≥   │ (all devices
                │   abort θ)    │  processed)
                ▼               │
             ABORTED        ROLLING_BACK ──► ROLLED_BACK_AUTOMATIC
```

### OTA Protocol Flow (per device)

```
  Server                    Broker                     Device
    │                          │                          │
    │── OFFER ────────────────►│────────────────────────►│
    │                          │                          │── eligibility check
    │                          │◄── OFFER_ACCEPTED ───────│   (battery, network,
    │                          │                          │    version, policy)
    │── MANIFEST ─────────────►│────────────────────────►│
    │                          │                          │── Ed25519 verify
    │── CHUNKS (n×32KB) ──────►│────────────────────────►│
    │                          │                          │── SHA-256 verify
    │                          │◄── OTA_RESULT ───────────│── apply + reboot
    │                          │                          │
    │                          │                          │── 90s probation
    │                          │                          │── health check
    │                          │◄── HEARTBEAT ────────────│   (pass → DONE)
    │                          │                          │   (fail → revert)
```

---

## Key Features

| Feature | Description |
|---|---|
| **Adaptive Batch Control** | EWMA failure rate → shrink/grow batch size automatically |
| **Two-Stage Eligibility** | Server-side stale check + device-side live check via MQTT |
| **Probation Window** | 90s post-reboot health check; auto-revert on failure |
| **Selective Rollback** | Roll back any subset of the fleet to any prior firmware |
| **NVS Identity Guard** | ESP32 identity written once to NVS; OTA cannot overwrite it |
| **Ed25519 Signatures** | Manifest signed server-side; verified on device before apply |
| **Encrypted OTA** | AES-128-CBC firmware encryption with per-campaign IV |
| **Canary Batches** | First N devices get firmware first; failure aborts full rollout |
| **Battery Floor** | Per-device AND per-campaign minimum battery enforcement |
| **Stranded Target Rescue** | Orphaned OFFERED targets auto-requeued after 90s grace period |
| **Deferral Backoff** | 45s between retry rounds so rebooting devices can recover |
| **Decision Log** | Every batch decision logged with reason — shrink, grow, abort, hold |

---

## Dashboard

### Landing Page — 3D Car Showroom

The landing page features an interactive 3D car showroom with six GLB models. Drag to rotate each model, navigate between cars, then enter the fleet dashboard.

> **Models:** Mustang 1965 · Lamborghini · Toyota Supra · BMW M3 E30 · McLaren 765LT · Porsche 911 Turbo

![Landing Page — Mustang 1965](docs/images/landing_mustang.png)
![Landing Page — Concept Racer](docs/images/landing_concept.png)

---

### Fleet Dashboard — Home

Real-time overview of all 18 devices. Stats bar shows fleet reachability, version spread, battery and signal warnings at a glance. Device cards are grouped by family (ESP32 hardware vs. Docker simulators).

![Fleet Dashboard](docs/images/dashboard_home.png)

**Stats bar:** Fleet size · Reachable · Devices on latest firmware · Low battery count · Weak signal count · Rollout state

**Version spread donut:** Live breakdown of firmware versions across the fleet

---

### Live Campaign View

Track an active or completed campaign in real time. The **Convoy Strip** shows every batch as a lane — coloured green (success), red (failure), or grey (skipped). The **Decision Log** streams every orchestrator decision with timestamp.

![Live View — Combined HW + Sim Campaign](docs/images/live_combined_hw.png)
![Live View — Hardware Rollback](docs/images/live_rollback_hw.png)

**Key indicators:**
- `COMPLETE` / `ABORTED` / `RUNNING` campaign state in large type
- Updated · Failed · Skipped · Batch size counters
- Per-device event stream (OFFER_SENT → OFFER_ACCEPTED → SUCCESS / FAILED_LOW_BATTERY)
- Batch decisions: `SHRINK_HIGH_FAILURE · batch size 2 → 1`

---

### Analytics

Filter and search campaigns, sorted by any column. Three embedded charts:

- **Delivery Timeline** — batch-by-batch update progression
- **Eligibility Funnel** — considered → offered → succeeded
- **Implementation Compare** — adaptive vs. baseline side-by-side

---

### Device Card

Each TCU is a car-shaped card showing device ID, firmware version, network quality bars, battery indicator, and live download progress (wave fill animation during OTA).

![Device Card](docs/images/device_card.png)

---

## Hardware Fleet

Three ESP32-WROOM-32 boards running a custom OTA client, each commissioned with a unique identity stored in NVS.

| Board | Device ID | Operator | Normal Battery | Hotspot |
|---|---|---|---|---|
| Board 1 | `esp32_001` | Shashank | 87% | Shashank's iPhone |
| Board 2 | `esp32_002` | Shravan | 84% | Shravan's iPhone |
| Board 3 | `esp32_003` | Sumit | 8% ← intentional | Sumit's iPhone |

### Hardware Campaign Results (verified)

| Campaign | Version | Outcome | Notes |
|---|---|---|---|
| `c_3fd61bd9` | 1.7 → 1.8 | ✅ SUCCESS | Initial bring-up |
| `c_888b8f06` | broken image | 🔁 ROLLED_BACK_AUTOMATIC | Probation health check failed |
| `c_f7380d6740` | → 3.1.0 | ✅ SUCCESS | All 3 boards |
| `c_a77d5dad` | → 4.0.0 | ⚠️ 2/3 UPDATED | Board 3 skipped (policy) |
| `c_8acb94f7` | → 6.0.0 | ❌ FAILED_LOW_BATTERY | esp32_003 at 8% vs 30% floor |
| `c_b1cc5e4e` | rollback | 🔁 1 of 3 selected | Selective rollback verified |

### Hardware-specific Mechanics

**NVS Identity Guard** — Identity is written to NVS exactly once using `PROVISION_IDENTITY`. Two guards prevent overwrite: a compile-time flag and an OTA-arrival detector that checks for a `pending` NVS key before applying any firmware.

**Two-Stage Eligibility** — The server checks its cached telemetry (server-side gate). Then when the MQTT OFFER arrives, the ESP32 reads live battery and network values (device-side gate). These two checks can and do disagree — demonstrated on hardware.

**Probation** — The 90-second deadline is checked inside both the WiFi reconnect loop and the MQTT reconnect loop, not only in the main loop. Without this, a board stuck reconnecting would silently miss its probation window.

---

## Adaptive Control Law

The orchestrator uses an Exponentially Weighted Moving Average (EWMA) of the per-batch failure rate to decide the next batch size:

```
r_k  = f_k / a_k                    # batch failure rate
E_k  = α · r_k + (1−α) · E_{k−1}   # EWMA update

b_{k+1} = ABORT   if r_k ≥ θ_abort   (default: 0.50)
           shrink  if r_k ≥ θ_shrink  (default: 0.20)
           grow    if n consecutive clean batches (n = 2)
           hold    otherwise
```

Where `b_min` and `b_max` cap the batch size at all times.

### Observed Behaviour (Campaign `c_e0d3bb39` — 15 simulators)

| Batch | Size | Attempted | Failed | Rate | Decision |
|---|---|---|---|---|---|
| 1 | 5 | 5 | 0 | 0.00 | HOLD_COOLDOWN |
| 2 | 5 | 5 | 2 | **0.40** | **SHRINK** → 2 |
| 3 | 2 | 2 | 0 | 0.00 | HOLD_COOLDOWN |
| 4 | 2 | 2 | 0 | 0.00 | **GROW** → 5 |
| 5 | 5 | 5 | 0 | 0.00 | HOLD_COOLDOWN |
| 6 | 5 | 1 | 0 | 0.00 | COMPLETE |

**Result:** 13/15 updated, 2 FAILED (battery/network). No abort triggered.

---

## Requirements Verification

All 18 software requirements verified across combined 18-device test.

| # | Requirement | Status | Evidence |
|---|---|---|---|
| SR-01 | Device registration via MQTT | ✅ | Heartbeat on connect |
| SR-02 | Firmware version tracking per device | ✅ | `device_events` table |
| SR-03 | Campaign creation with parameters | ✅ | `manage.py campaign:create` |
| SR-04 | Batch-based delivery | ✅ | Convoy Strip visualization |
| SR-05 | Canary batch | ✅ | First N devices isolated |
| SR-06 | Battery eligibility filter | ✅ | `min_battery` + `device_min_battery` |
| SR-07 | Network quality filter | ✅ | `min_network` parameter |
| SR-08 | Adaptive batch resize | ✅ | Shrink at batch 2 (40% failure) |
| SR-09 | Abort on high failure rate | ✅ | θ_abort = 0.50 tested |
| SR-10 | Firmware signature verification | ✅ | Ed25519 on device |
| SR-11 | Firmware encryption | ✅ | AES-128-CBC per campaign |
| SR-12 | Chunked transfer | ✅ | 32KB chunks, SHA-256 per chunk |
| SR-13 | Probation window | ✅ | 90s, auto-revert tested |
| SR-14 | Automatic rollback | ✅ | Campaign `c_888b8f06` |
| SR-15 | Selective rollback | ✅ | Campaign `c_b1cc5e4e` |
| SR-16 | Stranded target rescue | ✅ | 90s grace period in orchestrator |
| SR-17 | NVS identity persistence | ✅ | Dual guard (compile + runtime) |
| SR-18 | Real-time dashboard | ✅ | SSE + MQTT relay to Next.js |

---

## Quick Start

### Prerequisites

```bash
# Backend
Python 3.11+, PostgreSQL 16, pip

# Dashboard
Node.js 20+, npm

# Hardware (optional)
Arduino IDE 2.x, ESP32 board support package
```

### 1 — Clone & configure

```bash
git clone https://github.com/sumit-chillal/Intelligent-Adaptive-Fleet-Level-OTA
cd Intelligent-Adaptive-Fleet-Level-OTA
```

### 2 — Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt --break-system-packages

# Copy and edit environment
cp .env.example .env
# Set DATABASE_URL, MQTT_HOST, MQTT_USER, MQTT_PASS

alembic upgrade head
uvicorn app.main:app --port 8000 --reload
```

### 3 — Dashboard

```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:3000
```

### 4 — Start simulators

```bash
cd backend
python manage.py devices:start --count 15
```

### 5 — Run a campaign

```bash
# Publish firmware
python manage.py firmware:make --version 1.0.0 --size 256
python manage.py firmware:publish --file build/tcu-1.0.0.bin --version 1.0.0

# Create adaptive campaign (fleet of 15 simulators)
python manage.py campaign:create \
  --firmware fw_XXXX \
  --name "fleet-1.0.0" \
  --batch-size 5 --canary 2 \
  --min-battery 5 --min-network 1 \
  --abort-threshold 0.5 --max-attempts 1 \
  --no-encryption --fleet all

# Start it
python manage.py campaign:start --campaign c_XXXX
```

Watch the campaign progress at `http://localhost:3000/live`.

---

## ESP32 Provisioning

```bash
# Step 1: Erase flash
python -m esptool --port COM5 erase-flash

# Step 2: In config.h — set DEVICE_ID, BATTERY_PERCENT, WiFi creds
#         Uncomment: #define PROVISION_IDENTITY
# Upload firmware → confirms "PROVISIONING BUILD — writing identity to NVS"

# Step 3: Comment out PROVISION_IDENTITY, upload again
# Now identity is locked in NVS; fleet images cannot overwrite it
```

---

## Useful Commands

```bash
# List all devices and current firmware
python manage.py devices

# List recent campaigns
python manage.py campaigns --limit 10

# Campaign status
python manage.py campaign:status --campaign c_XXXX

# Rollback
python manage.py campaign:rollback --campaign c_XXXX --to-firmware fw_YYYY
python manage.py campaign:start --campaign c_ZZZZ

# Non-adaptive baseline (pinned batch size)
python manage.py campaign:create \
  --firmware fw_XXXX --name "baseline-fixed5" \
  --batch-size 5 --batch-size-min 5 --batch-size-max 5 \
  --canary 0 --abort-threshold 1.0 --max-attempts 1 \
  --no-encryption --fleet all
```

---

## Project Structure

```
convoy/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   └── orchestrator.py       # Adaptive engine (994 lines)
│   │   ├── services/
│   │   │   └── campaign_service.py   # Two-stage eligibility (290 lines)
│   │   ├── models/                   # SQLAlchemy ORM
│   │   ├── api/                      # FastAPI routers
│   │   └── main.py
│   ├── alembic/                      # DB migrations
│   ├── manage.py                     # CLI (540 lines)
│   └── requirements.txt
│
├── dashboard/
│   ├── app/
│   │   ├── page.tsx                  # Landing (3D showroom)
│   │   ├── dashboard/page.tsx        # Fleet overview
│   │   ├── live/page.tsx             # Campaign live view
│   │   └── analytics/page.tsx        # Campaign analytics
│   ├── components/
│   │   ├── CarShowcase.tsx           # 3D GLB showroom (327 lines)
│   │   ├── DeviceCard.tsx            # Car-shaped TCU card (297 lines)
│   │   ├── ConvoyLogo.tsx            # O-as-wheel wordmark
│   │   ├── FleetGrid.tsx             # Grouped by family
│   │   ├── CampaignCharts.tsx        # 3 analytical charts
│   │   └── Nav.tsx                   # Sliding indicator nav
│   └── public/models/               # GLB car models
│
├── firmware/
│   └── esp32/                        # Arduino OTA client
│       ├── config.h
│       ├── convoy_esp32.ino
│       └── ...
│
└── docs/
    ├── ARCHITECTURE.md
    ├── ADAPTIVE_CONTROL.md
    ├── HARDWARE_SETUP.md
    ├── DEMO_WORKFLOW.md
    ├── EXPERIMENT_BASELINE.md
    ├── MANUSCRIPT_1_CONFERENCE.md
    └── images/                       # Screenshots
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Broker** | HiveMQ Cloud (TLS 8883, mutual auth) |
| **Backend** | FastAPI · SQLAlchemy · aiomqtt · PostgreSQL |
| **Dashboard** | Next.js 15 · React · Three.js · @react-three/fiber · Motion |
| **Hardware** | ESP32-WROOM-32 · Arduino OTA · SH1106 OLED |
| **Security** | Ed25519 signatures · AES-128-CBC encryption |
| **Transport** | MQTT over TLS · chunked binary transfer · SHA-256 per chunk |
| **Simulation** | Docker containers · Python asyncio |

---
