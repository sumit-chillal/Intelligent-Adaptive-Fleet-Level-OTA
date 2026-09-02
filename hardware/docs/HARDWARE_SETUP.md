# Hardware Setup — ESP32 TCU

Stage 1: get a board connected, reporting telemetry, and visible on the
dashboard alongside the fifteen simulated devices. OTA comes in stage 2.

Confirmed hardware: ESP32-D0WD-V3, **4 MB flash** (verified with
`esptool flash-id`), which is enough for two 1.9 MB OTA partitions and
therefore for genuine A/B rollback. SSD1306 0.96" I2C OLED, 4-pin.

Allow about 90 minutes for the first board, 15 for each one after.

---

## Who does what

| Task | Machine |
|---|---|
| Wiring, Arduino IDE, flashing | 🅓 A Windows laptop with the board plugged in |
| Broker credentials, public key | 🅐 Mac (already done) |
| Watching the device appear | 🅐 Mac dashboard |

The Mac never has a board attached. Boards are flashed over USB from a Windows
laptop and then run on their own power, reaching the broker over WiFi.

---

## Step 1 — Wiring (15 min per board)

Power the board from USB while wiring, but **unplug it before changing
connections**.

### OLED (4-pin I2C)

| OLED pin | ESP32 pin |
|---|---|
| VCC | 3V3 — **not** VIN. The module is a 3.3 V part |
| GND | GND |
| SCL | GPIO 22 |
| SDA | GPIO 21 |

### LEDs

Each LED needs a resistor in series, 220 Ω to 330 Ω. Without one the GPIO
sources more current than it is rated for and the pin degrades over time.

| LED | ESP32 pin | Meaning |
|---|---|---|
| Green | GPIO 25 | idle / verified |
| Blue | GPIO 26 | offered / downloading / installing |
| Red | GPIO 27 | failed |

Long leg (anode) to the resistor and then to the GPIO; short leg (cathode) to
GND.

### Check before powering on

- OLED VCC is on **3V3**, not VIN or 5V
- No wire bridges 3V3 to GND
- Every LED has a resistor
- All grounds share the board's GND rail

---

## Step 2 — Arduino IDE (20 min, once per laptop)

**Board support.** File → Preferences → Additional Board Manager URLs, add:

```
https://espressif.github.io/arduino-esp32/package_esp32_index.json
```

Then Tools → Board → Boards Manager, search `esp32`, install **esp32 by
Espressif Systems**.

**Libraries.** Tools → Manage Libraries, install each of these:

| Library | Author | Used for |
|---|---|---|
| PubSubClient | Nick O'Leary | MQTT |
| ArduinoJson | Benoit Blanchon | message encoding |
| Adafruit SSD1306 | Adafruit | OLED |
| Adafruit GFX Library | Adafruit | OLED text |
| Crypto | Rhys Weatherley | Ed25519, SHA-256, X25519, AES-GCM — **stage 2/3** |

Install `Crypto` now even though stage 1 does not use it. ESP-IDF's bundled
mbedTLS does not include Ed25519, which is why this library is needed rather
than the platform's own crypto.

**Board settings.** Tools menu:

| Setting | Value |
|---|---|
| Board | ESP32 Dev Module |
| Partition Scheme | **Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)** |
| Flash Size | 4MB (32Mb) |
| Upload Speed | 921600 (drop to 115200 if uploads fail) |
| Port | the COM port that appears when the board is plugged in |

The partition scheme is the setting that matters. It creates two application
partitions of 1.9 MB, which is what makes A/B installation possible: the
running firmware is never overwritten, so an image that verifies perfectly and
then fails to boot costs nothing.

**If no COM port appears**, install the USB-serial driver — CP2102 or CH340
depending on the board. This is the single most common time sink; do it before
demo week, not during it.

---

## Step 3 — Get the broker's root certificate (5 min, 🅐 Mac)

The firmware validates the broker's certificate. It does **not** call
`setInsecure()`: a device that skips validation will talk to anything claiming
to be the broker, which throws away the confidentiality half of the design to
save three lines of setup.

```bash
openssl s_client -showcerts -connect b8ec2aadc24f4720a99bfd631ca7872c.s1.eu.hivemq.cloud:8883 </dev/null 2>/dev/null | awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/'
```

Several certificates print. Copy the **last** one, including both
`-----BEGIN CERTIFICATE-----` and `-----END CERTIFICATE-----`. That is the
root of the chain.

---

## Step 4 — Configure the board (5 min per board)

In the sketch folder, copy the template:

```
cp config.h.example config.h
```

Edit `config.h`:

| Setting | Value |
|---|---|
| `DEVICE_ID` | `esp32_001`, `esp32_002`, `esp32_003` — **different on every board** |
| `BATTERY_PERCENT` | 87, 84, and **8** on `esp32_003` |
| `WIFI_SSID` / `WIFI_PASSWORD` | your hotspot |
| `MQTT_HOST` | your cluster hostname, no scheme, no port |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | `convoy_esp32` and its password |
| `SERVER_PUBLIC_KEY_HEX` | already filled in |
| `BROKER_ROOT_CA` | the certificate from step 3 |

Two boards sharing a `DEVICE_ID` will fight over the same MQTT client identity
and disconnect each other in a loop, which looks exactly like a flaky network.

---

## Step 5 — Flash (5 min per board)

Open `esp32-tcu.ino`, select the right port, and upload. If it stalls at
`Connecting.....`, hold the **BOOT** button while it starts.

Open Serial Monitor at **115200 baud**. Expect:

```
=== CONVOY esp32_001 === v1.3.0 slot A
connecting to WiFi your-hotspot....
WiFi connected, ip=192.168.43.12 rssi=-54
connecting to broker xxxx.s1.eu.hivemq.cloud:8883 as esp32_001-a3f9
broker connected
[esp32_001] announced v1.3.0 battery=87% net=5 (trigger=connect)
```

The OLED should show the device id, version, battery and network, and the green
LED should be lit.

---

## Step 6 — Confirm it joined the fleet (🅐 Mac)

```bash
cd ~/Documents/Major-Project/convoy/backend
python manage.py devices
```

`esp32_001` appears alongside the simulated units, with `device_type` of
`esp32`. Open the dashboard and it is a tile like any other; click it and the
detail panel shows its health history and event timeline.

**This is the moment worth screenshotting.** A physical board on a phone
hotspot, a container on a Windows laptop, and the management server on a Mac,
all in one fleet view, with the server unable to tell them apart except by a
label. That is the decoupling requirement demonstrated rather than asserted.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No COM port | USB-serial driver missing | install CP2102 or CH340 |
| Upload stalls at `Connecting...` | board not in bootloader | hold BOOT during upload |
| `broker refused, state=-2` | TLS failure | wrong or truncated `BROKER_ROOT_CA`, or wrong host |
| `broker refused, state=4` or `5` | credentials | check `convoy_esp32` username and password |
| OLED blank, everything else fine | wrong I2C address | try `0x3D` in `display.begin` |
| Board reboots repeatedly | brownout | use a better USB cable or a powered hub; WiFi draws current in bursts |
| Device appears then vanishes | duplicate `DEVICE_ID` | each board needs its own |

---

## What comes next

**Stage 2 — OTA.** Ed25519 verification with the `Crypto` library, chunked
download written directly into the inactive partition via `Update.h`, resume
state in NVS, install with `esp_ota_set_boot_partition`, self-confirmation with
`esp_ota_mark_app_valid_cancel_rollback`, and automatic reversion when
confirmation does not arrive. The OLED gains a progress bar and the failure
banner.

**Stage 3 — encryption.** X25519 keypair generated on first boot and stored in
NVS, HKDF key derivation, AES-256-GCM chunk decryption, bringing the board to
full parity with the simulator.

Get stage 1 working on all three boards first. A firmware transfer debugged on
top of an unproven MQTT connection means never knowing which layer failed.