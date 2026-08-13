# Deployment Guide — CONVOY (free tier only)

For when you want the backend and dashboard reachable from anywhere, not just Laptop A. Useful for a remote demo, for a portfolio link, and for the report's "production readiness" section.

**The devices need no change at all.** They already talk only to the broker. Moving the server to the cloud changes nothing on Laptops B/C/D or the ESP32s — which is itself the strongest evidence that the decoupling requirement was met properly.

---

## 1. Free-tier service map

| Component | Service | Free tier | Watch out for |
|---|---|---|---|
| MQTT broker | **HiveMQ Cloud Serverless** | 100 connections, 10 GB/mo | Connection cap, not data, is the limit |
| Postgres | **Neon** | 0.5 GB, autosuspend | Suspends after inactivity → first query is slow |
| Redis | **Upstash** | 10k commands/day, 256 MB | Command budget; coalesce WebSocket fan-out |
| Backend | **Render** free web service | 512 MB RAM, sleeps after 15 min idle | Sleeping kills the MQTT connection — see §4 |
| Backend (alt) | **Fly.io** | 3 shared-cpu-1x VMs | Does *not* sleep — better for a long-lived MQTT client |
| Dashboard | **Vercel** Hobby | Generous | Non-commercial only |
| Firmware blobs | **Cloudflare R2** free (10 GB) or Supabase Storage (1 GB) | | Only needed if images exceed the MQTT path |
| CI | **GitHub Actions** | 2000 min/mo | |
| Errors/logs | **Sentry** free, **Better Stack** free | | Optional |

**Recommendation: Fly.io for the backend, not Render.** The backend holds a persistent MQTT subscription. Render's free tier sleeps on HTTP inactivity, which silently drops that subscription and makes devices look offline. Fly's free allocation stays up.

---

## 2. Backend → Fly.io

```bash
brew install flyctl && fly auth login
cd software/backend
fly launch --no-deploy          # generates fly.toml; pick a region near you (bom = Mumbai)
```

`fly.toml` essentials:
```toml
app = "convoy-api"
primary_region = "bom"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = false     # CRITICAL: keeps the MQTT client alive
  min_machines_running = 1

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

Secrets — never in `fly.toml`, never in git:
```bash
fly secrets set \
  DATABASE_URL="postgresql+asyncpg://...neon.tech/convoy?sslmode=require" \
  REDIS_URL="rediss://...upstash.io:6379" \
  MQTT_HOST="xxxx.s1.eu.hivemq.cloud" \
  MQTT_USERNAME="convoy_server" \
  MQTT_PASSWORD="..." \
  ADMIN_API_TOKEN="..." \
  JWT_SECRET="..." \
  FIRMWARE_SIGNING_PRIVATE_KEY="$(cat admin/secrets/convoy_ed25519_private.pem)"

fly deploy
fly logs
```

Note the signing key is passed as a **secret value**, not a file path, in cloud mode. The app reads `FIRMWARE_SIGNING_PRIVATE_KEY` first and falls back to `..._PATH` locally. Fly secrets are encrypted at rest and only exposed to the running VM.

Migrations:
```bash
fly ssh console -C "alembic upgrade head"
```

---

## 3. Dashboard → Vercel

```bash
cd software/dashboard
npx vercel
```
In the Vercel project settings, add:
```
NEXT_PUBLIC_API_BASE_URL = https://convoy-api.fly.dev
NEXT_PUBLIC_WS_URL       = wss://convoy-api.fly.dev/ws
CONVOY_ADMIN_API_TOKEN   = <server-side only, no NEXT_PUBLIC_ prefix>
```
Then set `CORS_ORIGINS=https://convoy.vercel.app` on the backend and redeploy.

WebSockets work on Vercel because the socket terminates at Fly, not at Vercel — the browser dials the backend directly. Vercel serves static assets only.

---

## 4. Free-tier hazards and how to survive them

| Hazard | Effect | Mitigation |
|---|---|---|
| Render sleeps after 15 min idle | MQTT subscription silently dies; fleet appears offline | Use Fly with `auto_stop_machines = false` |
| Neon autosuspends | First request after idle takes 2–5 s | Keep a 4-minute health ping, or accept a cold start before the demo |
| Upstash 10k commands/day | WebSocket fan-out burns commands fast | Coalesce deltas at 20 Hz instead of one publish per event; batch health samples |
| HiveMQ 100-connection cap | Scale test can't run | Self-host EMQX in Docker for load tests; keep HiveMQ for the demo |
| Fly 512 MB RAM | OOM if you buffer whole firmware images | Stream chunks from disk/R2; never `read()` a full image into memory |
| Vercel Hobby non-commercial | Terms violation if monetised | Fine for a college project |

---

## 5. CI (GitHub Actions, free)

`.github/workflows/ci.yml` should do four things and nothing else:

1. `ruff` + `mypy` on the backend, `tsc --noEmit` + `eslint` on the dashboard.
2. `pytest` with a Postgres service container — including the **adaptive engine table test** and the **four negative signature tests**. If either fails, the build fails.
3. `docker build` the TCU image and push to GHCR so the device laptops can `docker pull` instead of building on demo day.
4. On a tag, deploy: `flyctl deploy` (backend) and Vercel's Git integration (dashboard).

Store `FLY_API_TOKEN` in GitHub repository secrets. No credential ever appears in a workflow file.

---

## 6. Production hardening (report material, not demo work)

These are the honest gaps between this project and a system you would put in a real vehicle. Naming them shows you understand the difference.

- **Per-device MQTT credentials with broker ACLs.** The demo shares one credential per laptop. Production issues one per device, scoped so a device can publish only under its own topic branch — otherwise a single compromised device can impersonate the whole fleet.
- **Key rotation and a key hierarchy.** One root signing key is a single point of failure. Uptane's answer is separate roles (root, targets, timestamp, snapshot) with independent keys and offline root storage.
- **HSM or KMS for the signing key.** A key in an env var is a key that can be read by anything that can read the process environment.
- **mTLS client certificates** instead of username/password.
- **Staged geographic rollout** — canary by region, not just by count.
- **Delta updates** (bsdiff/courgette) to cut cellular data cost, which is the dominant operational expense in a real fleet.
- **Backpressure** — a per-campaign concurrency cap so 10,000 devices don't all pull chunks simultaneously.
- **Event retention and partitioning** — `device_events` grows without bound; monthly partitions plus a cold-storage export.
- **Observability** — Prometheus metrics on batch outcomes and chunk throughput, alerting when the abort guard fires.
- **RBAC and approval gates** — a real fleet requires two-person sign-off before a campaign targeting more than N devices.
- **Compliance artefacts** — UNECE R156 requires a documented Software Update Management System with traceability from firmware version to affected vehicle. The append-only event log is the raw material for that; the missing piece is the report generator.
