# ESP32 Setup — complete walkthrough

Written for someone who has not done embedded work before. Every step says
where you are, what to type, and what you should see.

Two people are flashing boards on two Windows laptops. Both hit the same error;
Part 0 fixes it.

---

## Part 0 — Fixing `config.h: No such file or directory`

Two separate causes, both quick.

### Cause 1 — the file does not exist yet

`config.h.example` is a **template**. Arduino compiles `config.h`, which you
create by copying the template and filling it in. Copying is a step you do
once per laptop; it is not automatic, because the file contains your WiFi and
broker passwords and must never be committed.

### Cause 2 — angle brackets

Your line 32 reads:

```cpp
#include <config.h>      // WRONG
```

Angle brackets tell the compiler to search the **library** folders. Quotes tell
it to look **next to the sketch** first. Change it to:

```cpp
#include "config.h"      // correct
```

### Arduino's folder rule

Arduino IDE requires that a sketch live in a folder with the **same name** as
the `.ino` file, and every additional file the sketch uses must sit in that
same folder. So this works:

```
esp32-tcu/                 <- folder name
├── esp32-tcu.ino          <- must match the folder name
└── config.h               <- beside it, not in a subfolder
```

and this does not:

```
sketch_sep2a/
├── sketch_sep2a.ino
└── (no config.h)          <- the error you saw
```

### Do this now (both Windows laptops)

Work inside the repository rather than a separate folder, so `git pull` brings
you firmware updates.

Git Bash:

```bash
cd ~/Projects/convoy
git pull
cd hardware/esp32-tcu
ls
```

You should see `esp32-tcu.ino` and `config.h.example`. Then:

```bash
cp config.h.example config.h
ls
```

Now three files. Open `esp32-tcu.ino` in Arduino IDE by double-clicking it —
this makes Arduino treat `hardware/esp32-tcu` as the sketch folder, and
`config.h` appears as a second tab across the top.

Confirm line 32 uses quotes, not angle brackets.

---

## Part 1 — What you are building

Three ESP32 boards that behave exactly like the fifteen Docker containers
already in your fleet. Same MQTT topics, same message format, same signed
manifests, same reason codes. The server has no ESP32-specific code; it cannot
tell them apart except by a label.

That is the point worth defending in the viva. A protocol that only one
implementation speaks is not a protocol, it is a pair of programs that happen
to agree. A second implementation, in a different language on different
hardware, is what proves the specification was real.

Stage 1, which this guide covers, is connectivity only: the board joins WiFi,
connects to the broker over TLS, publishes telemetry, and shows its state on
the OLED and LEDs. No firmware transfer yet. That comes in stage 2, once the
connection is proven — debugging a download on top of an unproven connection
means never knowing which layer failed.

---

## Part 2 — Wiring

Unplug the board before changing any wire.

### The OLED

Your module has four pins. **Read the labels printed on the module itself.**
These displays ship in two different pin orders that look identical, and
reversing VCC and GND usually destroys the module.

| OLED pin (by label) | ESP32 pin (by label) |
|---|---|
| GND | any pin marked `GND` |
| VCC | `3V3` |
| SCL | `D22` |
| SDA | `D21` |

`3V3`, not `VIN` and not `5V`. The module is a 3.3 volt part.

### The LEDs

Each LED needs a resistor of 220 Ω to 330 Ω in series. Without one the GPIO
pin sources more current than it is rated for and degrades.

An LED has a long leg (positive, the anode) and a short leg (negative). Wire:

```
ESP32 pin  →  resistor  →  LED long leg
                            LED short leg  →  GND
```

| LED | ESP32 pin |
|---|---|
| Green | `D25` |
| Blue | `D26` |
| Red | `D27` |

### Before you power on

- OLED VCC is on `3V3`
- No wire connects `3V3` directly to `GND`
- Every LED has a resistor
- All grounds reach the board's `GND`

---

## Part 3 — Arduino IDE setup (once per laptop)

### Board support

File → Preferences → *Additional Board Manager URLs*, paste:

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

Tools → Board → Boards Manager. Search `esp32`. Install **esp32 by Espressif
Systems**. This takes several minutes.

### Libraries

Tools → Manage Libraries. Install each, matching the author exactly:

| Library | Author | Needed for |
|---|---|---|
| PubSubClient | Nick O'Leary | MQTT |
| ArduinoJson | Benoit Blanchon | messages |
| Adafruit SSD1306 | Adafruit | the display |
| Adafruit GFX Library | Adafruit | display text |
| Crypto | Rhys Weatherley | Ed25519 — **stage 2** |

Install `Crypto` now even though stage 1 does not use it. ESP32's built-in
mbedTLS does not include Ed25519, which is why this library is required rather
than the platform's own.

Adafruit SSD1306 will offer to install dependencies. Accept.

### Board settings

Tools menu, with the board plugged in:

| Setting | Value |
|---|---|
| Board | ESP32 Dev Module |
| **Partition Scheme** | **Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)** |
| Flash Size | 4MB (32Mb) |
| Upload Speed | 921600 — drop to 115200 if uploads fail |
| Port | the COM port that appears when you plug the board in |

The partition scheme is the setting that decides whether this project works.
It creates **two** application partitions of 1.9 MB each, so new firmware is
written to the one that is not running. The old version stays intact until the
new one proves it can boot. Choose any other scheme and there is only one
partition, so a bad image overwrites the working one and the board is dead.

### If no COM port appears

Install the USB-serial driver. Which one depends on the chip next to the USB
socket: **CP2102** (Silicon Labs) or **CH340** (WCH). Search for the driver by
that name, install, unplug and replug the board.

This is the most common source of lost time in this kind of project. Do it now,
not the week of the demo.

---

## Part 4 — The broker certificate (Mac, once)

The board verifies the broker's TLS certificate rather than calling
`setInsecure()`. A device that skips validation will talk to anything claiming
to be the broker, which discards the confidentiality half of the design.

On the Mac:

```bash
curl -s https://letsencrypt.org/certs/isrgrootx1.pem -o /tmp/root.pem
openssl x509 -in /tmp/root.pem -noout -subject -issuer -dates
cat /tmp/root.pem | pbcopy
```

Subject and issuer will both read `ISRG Root X1`. That identity is what makes
it a root certificate: it is signed by itself. HiveMQ Cloud uses Let's Encrypt,
so this is the anchor of its chain.

Send the contents to whoever is flashing boards.

---

## Part 5 — Configure each board

Every board needs its own `config.h`. Open the `config.h` tab in Arduino IDE
and set:

| Setting | Board 1 | Board 2 | Board 3 |
|---|---|---|---|
| `DEVICE_ID` | `esp32_001` | `esp32_002` | `esp32_003` |
| `BATTERY_PERCENT` | 87 | 84 | **8** |

Board 3 at 8% battery is the unit that refuses updates on stage, the physical
counterpart to `tcu_D_004`.

Same on all three:

| Setting | Value |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | your phone hotspot |
| `MQTT_HOST` | your cluster hostname — no `https://`, no port |
| `MQTT_USERNAME` | `convoy_esp32` |
| `MQTT_PASSWORD` | that credential's password |
| `BROKER_ROOT_CA` | the certificate from Part 4 |

Paste the certificate between `R"EOF(` and `)EOF"`, keeping the
`-----BEGIN CERTIFICATE-----` and `-----END CERTIFICATE-----` lines. There
should be one `BEGIN CERTIFICATE` and roughly 25 to 30 lines of characters
between the markers. A truncated certificate produces `state=-2`, which is
easy to mistake for a password problem.

**Two boards must never share a `DEVICE_ID`.** They would fight over the same
MQTT identity and disconnect each other in a loop, which looks exactly like a
flaky network.

---

## Part 6 — Flash

1. Plug in the board, select its port under Tools → Port.
2. Click **Upload** (the arrow).
3. If it stalls at `Connecting.....`, hold the **BOOT** button on the board
   until it starts writing, then release.
4. Open Serial Monitor (magnifier icon, top right) and set the baud rate to
   **115200** in the dropdown.

Expected output:

```
=== CONVOY esp32_001 === v1.3.0 slot A
connecting to WiFi your-hotspot....
WiFi connected, ip=192.168.43.12 rssi=-54
connecting to broker xxxx.s1.eu.hivemq.cloud:8883 as esp32_001-a3f9
broker connected
[esp32_001] announced v1.3.0 battery=87% net=5 (trigger=connect)
```

The OLED shows the device id, version, battery and network. The green LED is
lit.

---

## Part 7 — Confirm it joined the fleet

On the Mac:

```bash
cd ~/Documents/Major-Project/convoy/backend
python manage.py devices
```

`esp32_001` appears among the containers with `device_type` of `esp32`. Open
the dashboard, click its tile, and the detail panel shows its health history
and event timeline like any other device.

**Screenshot this.** A board on a phone hotspot, containers on two Windows
laptops, and the server on a Mac, all one fleet, with the server unable to
distinguish them except by a label. That is the decoupling requirement shown
rather than asserted.

Repeat Parts 5 and 6 for boards 2 and 3.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `config.h: No such file` | template not copied, or angle brackets | Part 0 |
| No COM port | USB-serial driver | install CP2102 or CH340 |
| Upload stalls at `Connecting...` | not in bootloader | hold BOOT during upload |
| `broker refused, state=-2` | TLS failure | certificate wrong or truncated; check the host has no `https://` |
| `broker refused, state=4` | bad credentials | check `convoy_esp32` username and password |
| `broker refused, state=5` | not authorised | credential lacks publish rights in HiveMQ |
| OLED dark, serial output normal | I²C address | change `0x3C` to `0x3D` in `display.begin` |
| OLED dark, nothing at all | wiring | check VCC is on 3V3 and the pin labels match |
| Board reboots repeatedly | power | better USB cable or a powered hub; WiFi draws current in bursts |
| Appears then disappears | duplicate `DEVICE_ID` | each board needs its own |
| Garbage in Serial Monitor | wrong baud | set 115200 |

---

## Why not the GitHub Releases approach

It is a reasonable-sounding design and it is worth being able to say why this
project does not use it.

In that design the operator clicks a button, a command goes out over MQTT, and
each device downloads a public `.bin` from GitHub over HTTPS. It is simple, and
it is what most hobby OTA tutorials show.

What it gives up:

| Property | This project | GitHub Releases |
|---|---|---|
| Firmware provenance | Ed25519 signature over a per-device manifest | whatever is at the URL |
| Confidentiality | AES-256-GCM, key wrapped per device | public download |
| Who gets it | server selects a cohort and gates on health | everyone who hears the command |
| Failure response | batch size adapts to observed failures | none |
| Progress | per-chunk, server-side | invisible to the server |
| Interruption | resumes at the last verified chunk | restarts |
| Rollback | signed, targeted at affected devices only | manual |
| Audit | append-only per-device event log | none |

Requirements 3, 9, 10, 14 and 15 all depend on the server being in the delivery
path. Moving the bytes to a public URL removes it from that path, and takes
those requirements with it.

There is a legitimate middle version, and the design already allows for it: the
manifest carries a reserved `download_url` field. A device could fetch the
image over HTTPS while the **signed manifest with its chunk hashes still
arrives over MQTT**. Signature verification, eligibility, batching, adaptive
decisions and audit all survive, and only the bulk transport changes. That is
the fallback if chunked delivery over MQTT proves too slow on the ESP32, and
the decision to take it should be made from a measurement rather than in
advance.
