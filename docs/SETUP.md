# Local Setup Guide — CONVOY

Everything here runs on free tiers. Nothing in this guide costs money.

Steps marked **🔴 MANUAL** cannot be scripted — a human must do them in a browser, with a USB cable, or on a physical device. Everything else is a command.

---

## 0. Who does what

| Machine | OS | Runs | Cables attached |
|---|---|---|---|
| **Laptop A** | macOS | Postgres, Redis, FastAPI, Next.js, projector | **none** |
| **Laptop B** | Windows | 5 TCU containers | none |
| **Laptop C** | Windows | 5 TCU containers | none |
| **Laptop D** | Windows | 5 TCU containers (2 fail) | none |
| **ESP32 ×3** | — | firmware | **USB → a Windows laptop, for flashing only** |

**Answer to "is all hardware on Windows?": yes.** The Mac never has a cable in it. The ESP32 boards connect to a Windows laptop over USB *only while being flashed*. After flashing they run on their own power (USB power bank or the laptop's USB port purely as a 5 V supply) and talk to the broker over WiFi. At runtime the boards are not "connected to" any computer — that's the whole point of the design. Do the flashing on a Windows laptop: CP2102/CH340 drivers are simplest there, and it keeps the Mac clean for the demo.

---

## 1. Free-tier accounts 🔴 MANUAL

| Service | Free tier | What you use it for |
|---|---|---|
| **HiveMQ Cloud** | Serverless plan: 100 concurrent connections, 10 GB/month | The MQTT broker — the only shared connection point |
| **GitHub** | Free | Repo + Actions CI |
| Neon *or* Supabase | Free Postgres (0.5 GB) | Only if you later deploy to the cloud; local demo uses Docker Postgres |
| Upstash Redis | Free (10k commands/day) | Same — cloud only |
| Render *or* Fly.io | Free instance | Backend hosting, later |
| Vercel | Hobby | Dashboard hosting, later |

**Set up HiveMQ Cloud now — nothing else works until this exists:**

1. Sign up at `console.hivemq.cloud` → create a **Serverless** cluster (free).
2. Copy the cluster URL: `xxxxxxxx.s1.eu.hivemq.cloud`, port `8883`.
3. Under **Access Management → Credentials**, create these users:
   - `convoy_server` — permission **Publish & Subscribe** (the admin/backend)
   - `convoy_device_b`, `convoy_device_c`, `convoy_device_d` — **Publish & Subscribe**
   - `convoy_esp32` — **Publish & Subscribe**
4. Write the passwords down once. HiveMQ does not show them again.
5. **Test it from your phone before touching any code**: install the *MQTT Analyzer* / *IoT MQTT Panel* app, connect with TLS on 8883, publish to `convoy/v1/d/test/hello`. If that works, the transport is proven.

> Free-tier note: 100 connections is plenty for 18 devices + backend + dashboard. For the 10,000-device scale test, run EMQX locally (`docker run -p 1883:1883 emqx/emqx:5`) instead of paying for a bigger cluster.

---

## 2. Laptop A — Admin (macOS)

### 2.1 Prerequisites 🔴 MANUAL
- Docker Desktop for Mac — install and **launch it once** so the daemon runs.
- Python 3.11 (`brew install python@3.11`)
- Node 20 (`brew install node@20`)
- Git

### 2.2 Clone and configure
```bash
git clone <your-repo> convoy && cd convoy/software

cp admin/.env.example admin/.env
```
🔴 **MANUAL** — open `admin/.env` and fill in `MQTT_HOST`, `MQTT_USERNAME`, `MQTT_PASSWORD`, and replace every `CHANGE_ME`. Generate the secrets properly:
```bash
openssl rand -hex 32      # use for JWT_SECRET
openssl rand -hex 24      # use for ADMIN_API_TOKEN and POSTGRES_PASSWORD
```

### 2.3 Generate the signing keypair — once, ever
```bash
pip install cryptography
python tools/keygen.py --out admin/secrets \
    --c-header ../hardware/esp32-tcu/include/server_pubkey.h
```
🔴 **MANUAL** — confirm `admin/secrets/` and `.env` are in `.gitignore` **before your first commit**. The private key leaking is the one mistake this project cannot recover from without re-provisioning every device.

### 2.4 Start the datastores
```bash
cd admin
docker compose -f docker-compose.admin.yml --env-file .env up -d
docker compose -f docker-compose.admin.yml ps     # both should be healthy
```

### 2.5 Prove the broker link before writing any feature code
```bash
pip install paho-mqtt python-dotenv
python link_check.py --ping
```
Leave this running. It prints a live fleet roster. It should say `connected` and then sit waiting. This is milestone zero.

### 2.6 Backend + dashboard (once the code exists)
```bash
cd ../backend && pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

cd ../dashboard && npm install
cp ../admin/.env.dashboard.example .env.local     # then edit
npm run dev            # http://localhost:3000
```

---

## 3. Laptops B, C, D — TCU fleet (Windows)

### 3.1 Prerequisites 🔴 MANUAL
- Docker Desktop for Windows, **WSL2 backend enabled** (the installer prompts; accept it).
- Git for Windows.
- In Docker Desktop → Settings → General, turn **"Start Docker Desktop when you log in"** ON.
- In Windows Settings → Power, set **"When plugged in, put my device to sleep after" = Never**. A sleeping laptop drops every container mid-demo.

### 3.2 Set up (identical on all three, only the compose file changes)
```powershell
git clone <your-repo> convoy
cd convoy\software\device
copy .env.example .env
```
🔴 **MANUAL** — edit `.env`: set `MQTT_HOST`, and use the credential for *this* laptop (`convoy_device_b` on B, `_c` on C, `_d` on D). Identity and battery/network profiles are already in the compose files — do not edit the Python.

### 3.3 Run
```powershell
# Laptop B
docker compose -f docker-compose.laptopB.yml up --build -d
docker compose -f docker-compose.laptopB.yml logs -f

# Laptop C
docker compose -f docker-compose.laptopC.yml up --build -d

# Laptop D  (tcu_D_004 and tcu_D_005 are the staged failures)
docker compose -f docker-compose.laptopD.yml up --build -d
```

Within ~2 seconds the roster on Laptop A shows five new rows per laptop. **That single moment — rows from a machine on a different WiFi appearing on the Mac — is the proof of Requirement 18.** Do it once before you build anything else, and put a screenshot of it in your report.

### 3.4 Useful
```powershell
docker compose -f docker-compose.laptopB.yml down          # stop, keep volumes
docker compose -f docker-compose.laptopB.yml down -v       # wipe resume state
docker restart tcu_B_003                                    # kill one device
docker compose -f docker-compose.laptopB.yml up -d --scale tcu_B_001=1
```

---

## 4. ESP32 boards 🔴 MANUAL (mostly)

Do all of this on a **Windows laptop**, not the Mac.

1. Install VS Code + the **PlatformIO** extension.
2. Plug in board 1 by USB. If it doesn't appear in Device Manager as a COM port, install the **CP2102** or **CH340** driver — 🔴 manual, and the single most common time-waster in this project. Do it a week early.
3. Wire the OLED and LEDs per `hardware/wiring/` (SDA 21, SCL 22, LED green 25, blue 26, red 27).
4. Copy `hardware/esp32-tcu/config.example.h` → `config.h` and fill in per board:
   ```c
   #define DEVICE_ID       "esp32_001"
   #define WIFI_SSID       "..."        // use a phone hotspot for the demo
   #define WIFI_PASSWORD   "..."
   #define MQTT_USERNAME   "convoy_esp32"
   #define MQTT_PASSWORD   "..."
   #define BATTERY_PERCENT 87           // esp32_003 gets 8
   ```
   `config.h` is gitignored. `server_pubkey.h` was generated by keygen and is committed — it is a public key, it is safe.
5. `pio run -t erase` then `pio run -t upload -t monitor`.
6. Repeat for boards 2 and 3. **Board 3 gets `BATTERY_PERCENT 8`** — that is the visible table-top failure.
7. 🔴 Confirm each module has **4 MB flash** (`esptool.py flash_id`). A 2 MB module cannot hold two OTA slots and the whole rollback story collapses.

---

## 5. Verification checklist

Run through this before every rehearsal.

- [ ] `link_check.py` on the Mac shows all 15 containers + 3 boards, `total=18`
- [ ] Every laptop is on a **different** network (turn WiFi off on one, put it on a hotspot, confirm it still appears)
- [ ] Kill a container → it flips to OFFLINE on the roster within 20 s (last will works)
- [ ] `docker compose ... up` a stopped container → it reappears with its version intact (state volume works)
- [ ] `--ping` shows round-trip times under ~300 ms for every device
- [ ] Postgres and Redis both report healthy
- [ ] `git status` shows no `.env` and no `secrets/`

---

## 6. Everything that must be done by a human, in one list

| # | Manual step | When | Why it can't be scripted |
|---|---|---|---|
| 1 | Create the HiveMQ Cloud cluster and credentials | Week 1 | Browser signup |
| 2 | Copy broker host/passwords into each `.env` | Week 1 | Secrets must not be in git |
| 3 | Run `keygen.py` once and verify `.gitignore` | Week 1 | Human judgement about what gets committed |
| 4 | Install Docker Desktop on all four laptops | Week 1 | GUI installer, requires reboot |
| 5 | Disable sleep on B/C/D | Before demo | OS setting |
| 6 | Install CP2102/CH340 USB driver on the flashing laptop | Week 1 | Signed driver install |
| 7 | Verify ESP32 flash size is 4 MB | Week 1 | Physical inspection of the boards |
| 8 | Wire OLED + LEDs on each board | Week 2 | Physical |
| 9 | Write per-board `config.h` and flash each board individually | Week 2 | Each board needs a distinct identity |
| 10 | Set `esp32_003` battery to 8 and `tcu_D_004/005` profiles | Before demo | Deliberate demo staging |
| 11 | Test port 8883 on the venue network; switch to WSS 443 if blocked | Day before | Depends on the venue |
| 12 | Set up the phone hotspot as the fallback network | Demo day | Physical |
| 13 | Record a backup screen capture of a full successful run | Night before | Insurance against venue WiFi |
| 14 | Seed one completed historical campaign so analytics isn't empty | Before demo | One command, but you must remember it |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `rc=5 not authorised` | Wrong username/password, or the credential lacks publish rights | Recheck HiveMQ Access Management |
| Connects then drops every ~15 s | Two clients sharing a client ID | Client IDs already carry a random suffix — check you didn't hardcode one |
| Nothing appears on the roster | Venue firewall blocks 8883 | Set `MQTT_TRANSPORT=websockets`, `MQTT_PORT=8884` |
| `SSL: CERTIFICATE_VERIFY_FAILED` on Windows | Missing CA bundle | `pip install certifi`; never disable verification |
| Containers vanish overnight | Laptop slept | Disable sleep; `restart: unless-stopped` handles the rest |
| ESP32 reboot loop after OTA | New image never self-confirmed | Expected — bootloader rolled back. Check the confirm call runs after MQTT reconnects |
| Postgres won't start | Port 5432 already in use | Change `POSTGRES_PORT` in `.env` |
