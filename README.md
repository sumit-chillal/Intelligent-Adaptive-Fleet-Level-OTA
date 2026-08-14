# CONVOY — Adaptive Fleet-Level OTA Framework

Phase 1 of the build: the **transport layer**. One repository, three roles. Each machine uses only the folder that belongs to it.

---

## Repository structure (complete, as of Phase 1)

```
convoy/
├── README.md                          ← you are here
├── .gitignore                         ← protects secrets; verify before first push
│
├── admin/                             ★ MAC ONLY (Laptop A)
│   ├── .env.example                   credential template → copy to .env
│   ├── .env.dashboard.example         Next.js env template (Phase 3)
│   ├── docker-compose.admin.yml       Postgres + Redis
│   ├── link_check.py                  live fleet roster over MQTT
│   ├── requirements.txt
│   └── secrets/                       generated keys land here (gitignored)
│       └── .gitkeep
│
├── device/                            ★ WINDOWS ONLY (Laptops B, C, D)
│   ├── .env.example                   broker credentials → copy to .env
│   ├── .dockerignore
│   ├── Dockerfile                     ONE image, MANY containers
│   ├── requirements.txt
│   ├── tcu_agent.py                   the simulated TCU
│   ├── docker-compose.laptopB.yml     tcu_B_001..005, all healthy
│   ├── docker-compose.laptopC.yml     tcu_C_001..005, all healthy
│   └── docker-compose.laptopD.yml     tcu_D_001..005, two staged failures
│
├── tools/                             ★ MAC ONLY
│   └── keygen.py                      Ed25519 signing keypair, run once ever
│
└── docs/
    ├── PRD.md
    ├── Architecture.md
    ├── Rules.md
    ├── Design.md
    ├── Memory.md
    ├── SETUP.md
    └── DEPLOYMENT.md
```

### What lands where in later phases

```
convoy/
├── backend/          Phase 2 — FastAPI, MQTT bridge, orchestrator, adaptive engine, Alembic   [Mac]
├── dashboard/        Phase 3 — Next.js                                                        [Mac]
├── device/           Phase 2 — OTA handlers merge into tcu_agent.py                    [Windows]
└── hardware/         Phase 4 — PlatformIO ESP32 project                    [Windows, flashing]
```

---

## Which machine clones what

**All four laptops clone the same repository.** They simply run different folders. Nothing is duplicated, and the device laptops never see the signing key because it is generated locally on the Mac and gitignored.

| Machine | Uses | Ignores |
|---|---|---|
| Laptop A (Mac) | `admin/`, `tools/`, `docs/`, later `backend/` + `dashboard/` | `device/` |
| Laptop B/C/D (Windows) | `device/` | everything else |

---

## Quickstart — Laptop A (Mac)

```bash
git clone <your-repo-url> convoy && cd convoy

python3 -m venv .venv && source .venv/bin/activate
pip install -r admin/requirements.txt

cp admin/.env.example admin/.env
#   → edit admin/.env: MQTT_HOST, MQTT_USERNAME=convoy_server, MQTT_PASSWORD
#   → generate the rest:  openssl rand -hex 32

python tools/keygen.py --out admin/secrets

cd admin
docker compose -f docker-compose.admin.yml --env-file .env up -d
python link_check.py --ping          # leave this running
```

## Quickstart — Laptop B / C / D (Windows PowerShell)

```powershell
git clone <your-repo-url> convoy
cd convoy\device

copy .env.example .env
#   → edit .env: MQTT_HOST, and the credential for THIS laptop
#      Laptop B → convoy_device_b     Laptop C → convoy_device_c     Laptop D → convoy_device_d

docker compose -f docker-compose.laptopB.yml up --build -d
docker compose -f docker-compose.laptopB.yml logs -f
```

Within about two seconds, five rows appear on the Mac's roster. If those laptops are on different networks, that single screen is the proof of the project's hardest requirement.

---

## Phase 1 done means

- [ ] `link_check.py` shows all 15 containers with `total=15`
- [ ] At least one laptop is on mobile data, not the shared WiFi, and still appears
- [ ] `docker stop tcu_B_003` flips that row to OFFLINE within 20 s (last will works)
- [ ] `--ping` returns a round-trip time for every device
- [ ] `git status` shows no `.env` and no `secrets/*.pem`

Then Phase 2 begins: database schema, MQTT bridge, orchestrator, adaptive engine.