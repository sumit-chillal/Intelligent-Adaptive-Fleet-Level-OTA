# Phase 1 Runbook — from "files copied" to "fleet visible across networks"

Follow in order. Do not skip ahead: each step verifies the one before it. Total time about 90 minutes, most of it waiting on Docker.

Legend: `A` = Mac (admin) · `W` = Windows device laptop · 🔴 = manual, done by a human

---

## Step 0 — Clean up the copy (A, 1 min)

Two stray files came across in the copy. Remove them:

```bash
cd ~/major-project/convoy
rm admin/.env.example.env      # duplicate
rm device/env.example          # missing the leading dot; .env.example is the real one
touch admin/secrets/.gitkeep   # empty is fine, it only keeps the folder in git
```

Verify you have exactly this:

```bash
find . -type f | sort
```

Expected — 15 files:
```
./.gitignore
./README.md
./admin/.env.dashboard.example
./admin/.env.example
./admin/docker-compose.admin.yml
./admin/link_check.py
./admin/requirements.txt
./admin/secrets/.gitkeep
./device/.dockerignore
./device/.env.example
./device/Dockerfile
./device/docker-compose.laptopB.yml
./device/docker-compose.laptopC.yml
./device/docker-compose.laptopD.yml
./device/requirements.txt
./device/tcu_agent.py
./docs/...
./tools/keygen.py
```

---

## Step 1 — Python environment (A, 5 min)

```bash
cd ~/major-project/convoy
python3 --version                 # need 3.11 or newer
python3 -m venv .venv
source .venv/bin/activate         # your prompt now starts with (.venv)
pip install --upgrade pip
pip install -r admin/requirements.txt
```

Verify:
```bash
python -c "import paho.mqtt.client, dotenv, cryptography; print('deps ok')"
```

> Every future `python` command in this runbook assumes the venv is active. If you open a new terminal tab, run `source .venv/bin/activate` again.

---

## Step 2 — Fill in `admin/.env` 🔴 (A, 10 min)

```bash
cp admin/.env.example admin/.env
```

Generate the three secrets you don't have yet, one at a time, and paste each into the file:

```bash
openssl rand -hex 32      # → JWT_SECRET
openssl rand -hex 24      # → ADMIN_API_TOKEN
openssl rand -hex 16      # → POSTGRES_PASSWORD
```

Then open `admin/.env` and set:

| Variable | Value |
|---|---|
| `MQTT_HOST` | your cluster URL, **without** `https://` and without a trailing slash — just `abc123....s1.eu.hivemq.cloud` |
| `MQTT_PORT` | `8883` |
| `MQTT_USERNAME` | `convoy_server` |
| `MQTT_PASSWORD` | the password you set for `convoy_server` |
| `POSTGRES_PASSWORD` | your `openssl rand -hex 16` output |
| `DATABASE_URL` | same password inside the URL: `postgresql+asyncpg://convoy:<THAT_PASSWORD>@localhost:5432/convoy` |
| `ADMIN_API_TOKEN` | your `rand -hex 24` output |
| `JWT_SECRET` | your `rand -hex 32` output |

Leave every other value at its default. Verify nothing was missed:

```bash
grep CHANGE_ME admin/.env      # must print NOTHING
```

---

## Step 3 — Generate the signing keypair (A, 2 min)

Run once, ever. Regenerating later invalidates every provisioned device.

```bash
python tools/keygen.py --out admin/secrets
ls -l admin/secrets/
```

You should see `convoy_ed25519_private.pem` at permission `-rw-------` (0600), plus the `.pem` and `.raw` public keys. The hex public key printed to the terminal is worth pasting into `docs/Memory.md` — it is public, and having it recorded helps when you flash the ESP32s.

---

## Step 4 — Start Postgres and Redis (A, 5 min)

🔴 Launch Docker Desktop first and wait for the whale icon in the menu bar to stop animating.

```bash
cd admin
docker compose -f docker-compose.admin.yml --env-file .env up -d
docker compose -f docker-compose.admin.yml ps
```

Both containers must show `healthy` (give it ~15 seconds). Verify the database really works:

```bash
docker exec -it convoy_postgres psql -U convoy -d convoy -c "SELECT version();"
docker exec -it convoy_redis redis-cli ping        # → PONG
```

> If Postgres fails with "port already allocated", something else uses 5432. Change `POSTGRES_PORT` in `.env` to `5433` and re-run.

---

## Step 5 — Prove the broker link (A, 5 min)

Still inside `admin/`:

```bash
python link_check.py --ping
```

Expected output:
```
[14:02:11] dialling abc123....s1.eu.hivemq.cloud:8883 ...
[14:02:12] connected to abc123....s1.eu.hivemq.cloud:8883 as convoy_server
[14:02:12] subscribed to convoy/v1/d/+/#
[14:02:12] waiting for devices...
```

**Leave this terminal open for the rest of the runbook.** It is your live view of the fleet.

| Error | Cause |
|---|---|
| `rc=5 not authorised` | Wrong `MQTT_USERNAME`/`MQTT_PASSWORD` |
| `getaddrinfo failed` | `MQTT_HOST` has `https://` or a trailing slash in it |
| `CERTIFICATE_VERIFY_FAILED` | `pip install --upgrade certifi`. Never disable verification |
| Hangs with no output | Port 8883 blocked. Set `MQTT_TRANSPORT=websockets` and `MQTT_PORT=8884` |

---

## Step 6 — Smoke-test one device on the Mac (A, 10 min) — TEMPORARY

This breaks the "admin runs no devices" rule on purpose, just once, so you can prove the full loop alone before coordinating four laptops. You will delete it at the end of this step.

Open a **second terminal**:

```bash
cd ~/major-project/convoy/device
cp .env.example .env
```

🔴 Edit `device/.env`: set `MQTT_HOST` (same cluster) and use `MQTT_USERNAME=convoy_device_b` with its password.

```bash
docker build -t convoy/tcu-sim:0.1 .

docker run --rm --name tcu_smoke \
  --env-file .env \
  -e DEVICE_ID=tcu_smoke_001 \
  -e BATTERY_LEVEL=87 \
  -e NETWORK_QUALITY=5 \
  -e FLEET_TAG=smoketest \
  convoy/tcu-sim:0.1
```

Watch the **first** terminal. Within two seconds:

```
[14:09:33] HELLO    tcu_smoke_001  v1.3.0 model=tcu-sim-v1
[14:09:43] PONG     tcu_smoke_001  rtt=  91.4 ms

  ── FLEET ROSTER 14:09:45 ──────────────────
  DEVICE          TYPE      VER       BATT  NET   MSGS  STATE
  tcu_smoke_001   tcu-sim   1.3.0      87%    5      4  online
  total=1
```

Now press `Ctrl-C` in the device terminal. The roster should flip that row to `OFFLINE` within 20 seconds — that is the MQTT last will firing, with no polling anywhere.

**This is the moment the transport layer is proven.** Screenshot it.

Then clean up, because the Mac must run zero devices from here on:
```bash
docker ps -a | grep tcu_smoke        # should be empty; --rm removed it
rm device/.env                       # the Mac does not need device credentials
```

---

## Step 7 — Create the GitHub repo (A, 10 min)

Now the repo exists and `git clone` in the setup docs means something.

```bash
cd ~/major-project/convoy
git init -b main
git add .
git status
```

🔴 **STOP AND READ THE OUTPUT.** Confirm you do **not** see:
- `admin/.env`
- `device/.env`
- any `.pem` or `.raw` file

You *should* see `.env.example`, `.env.dashboard.example`, and `admin/secrets/.gitkeep`. If a real `.env` or key appears, `.gitignore` didn't copy correctly — fix it before committing. A key that reaches GitHub, even in a deleted commit, is compromised forever.

```bash
git commit -m "feat: phase 1 transport layer, docs, and device simulation"
```

🔴 On github.com, create a new **private** empty repository named `convoy`. Do not add a README, licence, or .gitignore — you already have them.

```bash
git remote add origin https://github.com/<your-username>/convoy.git
git push -u origin main
```

---

## Step 8 — Bring up Laptop B (W, 20 min)

🔴 On the Windows laptop: install Docker Desktop with the WSL2 backend, reboot, launch it once. Install Git for Windows.

🔴 Windows Settings → System → Power & battery → Screen and sleep → **"When plugged in, put my device to sleep after" = Never.** A sleeping laptop kills every container mid-demo.

```powershell
cd C:\Users\<you>\Documents
git clone https://github.com/<your-username>/convoy.git
cd convoy\device
copy .env.example .env
notepad .env
```

🔴 In `.env`: set `MQTT_HOST` and `MQTT_USERNAME=convoy_device_b` with its password. Save.

```powershell
docker compose -f docker-compose.laptopB.yml up --build -d
docker compose -f docker-compose.laptopB.yml logs -f
```

First build takes 2–3 minutes. Then look at the Mac:

```
  ── FLEET ROSTER 14:31:02 ──────────────────
  DEVICE          TYPE      VER       BATT  NET   MSGS  STATE
  tcu_B_001       tcu-sim   1.3.0      90%    5     12  online
  tcu_B_002       tcu-sim   1.3.0      86%    5     12  online
  tcu_B_003       tcu-sim   1.3.0      83%    4     12  online
  tcu_B_004       tcu-sim   1.3.0      80%    4     12  online
  tcu_B_005       tcu-sim   1.3.0      78%    5     12  online
  total=5
```

🔴 **Now do the network-independence test.** On the Windows laptop, disconnect from WiFi and tether to your phone's hotspot instead. The containers reconnect on their own within ~30 seconds and the roster repopulates. Screenshot the Mac roster while the two machines are provably on different networks. That screenshot is your evidence for the project's hardest requirement, and it belongs in the report.

---

## Step 9 — Laptops C and D (W, 10 min each)

Identical to Step 8, changing only two things:

| Laptop | Credential in `.env` | Compose file |
|---|---|---|
| C | `convoy_device_c` | `docker-compose.laptopC.yml` |
| D | `convoy_device_d` | `docker-compose.laptopD.yml` |

On Laptop D, confirm the roster shows `tcu_D_004` at 8% battery and `tcu_D_005` at network quality 1. Those two are your staged failures, and they should be visibly wrong from the moment they connect.

---

## Step 10 — Phase 1 acceptance

Run through this and tick every box before moving on.

- [ ] Roster shows `total=15`
- [ ] At least one laptop is on a hotspot, not the shared WiFi, and still appears
- [ ] `docker stop tcu_B_003` → that row flips to OFFLINE within 20 s
- [ ] `docker start tcu_B_003` → it returns with `v1.3.0` intact (state volume works)
- [ ] `--ping` returns an RTT for all 15
- [ ] `tcu_D_004` reads 8% and `tcu_D_005` reads network 1
- [ ] `git status` is clean, no `.env`, no `.pem`
- [ ] You have screenshots of: the roster at 15 devices, the offline transition, and the hotspot test

Update `docs/Memory.md`: mark the transport row done, record your broker hostname and public key hex, and close question Q5 (venue port test) with what you found.

---

## What comes next

**Phase 2 — the backend.** Database schema and Alembic migrations, the MQTT bridge that turns messages into audited events, the campaign orchestrator, and the adaptive rollout engine with its table-driven test. At the end of Phase 2 you can launch a campaign from a terminal and watch batch sizes shrink — no UI yet.

**Phase 3** is the Next.js dashboard, **Phase 4** the ESP32 firmware. Order matters: the boards are far easier to debug once the server side is known-good.

Two things to settle before Phase 4, both open in Memory.md: confirm your ESP32 modules have 4 MB flash (`esptool.py flash_id`), and buy the CP2102/CH340 driver time now rather than the week of the demo.