// ===========================================================================
// CONVOY — ESP32 Telematics Control Unit
// Stage 2: connectivity, telemetry, display, and the full OTA path —
// Ed25519 verification, chunked download into the inactive flash partition,
// A/B installation, self-confirmation, and automatic reversion.
//
// This board speaks the SAME protocol as the Dockerised simulators. The server
// has no ESP32-specific code path; `device_type` exists only so the dashboard
// can label it. That is the point of having written the protocol down rather
// than letting it emerge from one implementation: a second implementation, in
// a different language on different hardware, either interoperates or reveals
// that the specification was ambiguous.
//
// Stage 3 will add payload decryption (X25519 + AES-256-GCM) for parity with
// the simulator when encryption is enabled.
//
// Board:            ESP32 Dev Module
// Partition scheme: Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)
// Libraries:        PubSubClient, ArduinoJson, Adafruit SSD1306, Adafruit GFX
// ===========================================================================

#include <Adafruit_GFX.h>
#include <Crypto.h>
#include <Ed25519.h>
#include <SHA256.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <mbedtls/base64.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>

#include "config.h"

// --------------------------------------------------------------- display ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool displayReady = false;

// ------------------------------------------------------------------ mqtt ---
WiFiClientSecure netClient;
PubSubClient mqtt(netClient);
Preferences prefs;

String currentVersion = INITIAL_VERSION;
String activeSlot = "A";
unsigned long lastHeartbeat = 0;
unsigned long bootMillis = 0;

// Topics, built once. Constructing them inline at each call site is how a
// typo produces a device that silently never receives offers.
String T_HELLO, T_HEALTH, T_STATUS, T_PONG, T_CMD, T_CMD_ALL;
String T_OTA_OFFER, T_OTA_CHUNK, T_OTA_ACK, T_OTA_PROGRESS, T_OTA_RESULT;

// ---------------------------------------------------------------- OTA state --
// One in-flight update. `active` guards every chunk handler: a chunk arriving
// outside an accepted offer is a stray from a cancelled campaign and must be
// dropped rather than written to flash.
struct OtaSession {
  bool active = false;
  String campaignId;
  String firmwareId;
  String version;
  uint32_t versionCode = 0;
  uint32_t chunkCount = 0;
  uint32_t chunkSize = 0;
  uint32_t sizeBytes = 0;
  uint32_t nextIndex = 0;
  bool isRollback = false;
  // Hex SHA-256 of each chunk, from the signed manifest. Held in RAM: 512
  // chunks at 64 characters is 32 KB, which fits comfortably, and a 1 MB
  // firmware image is only 128 chunks.
  std::vector<String> chunkHashes;
  String wholeSha256;
};
OtaSession ota;

// Forward declarations. Arduino generates prototypes for functions in the
// main .ino automatically, but only for simple signatures, and relying on that
// is how a working sketch breaks after an unrelated edit. Declaring them
// explicitly costs three lines and removes the ordering constraint entirely.
void handleOffer(const String& payload);
void handleChunk(const String& payload);
void installUpdate();
void confirmBootIfPending();
void publishResult(bool success, const char* reason, const String& detail = "",
                   int chunkIndex = -1);
// If the bootloader reverted a bad image, the campaign id is stashed here in
// setup() and reported once the broker connection is up.
String pendingAutoRollbackReport = "";

// Reason codes. The same closed vocabulary the server and the Python simulator
// use — a shared taxonomy is what lets one query explain an outcome regardless
// of which kind of device produced it.
namespace Reason {
const char* SUCCESS = "SUCCESS";
const char* LOW_BATTERY = "FAILED_LOW_BATTERY";
const char* CHUNK_HASH = "FAILED_CHUNK_HASH_MISMATCH";
const char* IMAGE_HASH = "FAILED_IMAGE_HASH_MISMATCH";
const char* SIG_INVALID = "FAILED_SIGNATURE_INVALID";
const char* ANTI_ROLLBACK = "FAILED_ANTI_ROLLBACK";
const char* FLASH_WRITE = "FAILED_FLASH_WRITE";
const char* ROLLED_BACK_MANUAL = "ROLLED_BACK_MANUAL";
}  // namespace Reason

// ===========================================================================
// LEDs — one meaning per colour, never two lit at once.
// ===========================================================================
enum LedState { LED_IDLE, LED_BUSY, LED_FAULT, LED_OFF };

void setLed(LedState s) {
  digitalWrite(PIN_LED_GREEN, s == LED_IDLE);
  digitalWrite(PIN_LED_BLUE, s == LED_BUSY);
  digitalWrite(PIN_LED_RED, s == LED_FAULT);
}

// ===========================================================================
// OLED — the board is a display too, and it follows the same signal
// vocabulary as the dashboard. Someone reading the board from 30 cm and
// someone reading the projector from 10 feet see the same words for the same
// event, which is the whole purpose of having a shared reason-code taxonomy.
// ===========================================================================
void screen(const String& line1, const String& line2 = "",
            const String& line3 = "", const String& line4 = "") {
  if (!displayReady) return;
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(line1);
  if (line2.length()) { display.setCursor(0, 16); display.println(line2); }
  if (line3.length()) { display.setCursor(0, 32); display.println(line3); }
  if (line4.length()) { display.setCursor(0, 48); display.println(line4); }
  display.display();
}

void screenBanner(const String& title, const String& detail) {
  if (!displayReady) return;
  display.clearDisplay();
  display.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, SSD1306_WHITE);
  display.setTextColor(SSD1306_BLACK);
  display.setTextSize(1);
  display.setCursor(4, 14);
  display.println(title);
  display.setCursor(4, 34);
  display.println(detail);
  display.display();
}

// ===========================================================================
// Message envelope — every message carries schema, msg_id, device_id and ts,
// exactly as the simulator does. The bridge deduplicates on msg_id, so a
// device that omitted it would have its QoS 1 redeliveries counted twice.
// ===========================================================================
String newMsgId() {
  char buf[33];
  for (int i = 0; i < 32; i++) {
    // esp_random() is the hardware RNG, not the pseudo-random Arduino random().
    buf[i] = "0123456789abcdef"[esp_random() & 0x0F];
  }
  buf[32] = '\0';
  return String(buf);
}

void addEnvelope(JsonDocument& doc, const char* schema) {
  doc["schema"] = schema;
  doc["msg_id"] = newMsgId();
  doc["device_id"] = DEVICE_ID;
  doc["ts"] = (double)millis() / 1000.0;
}

// ===========================================================================
// Telemetry
// ===========================================================================
void publishHello(const char* trigger) {
  JsonDocument doc;
  addEnvelope(doc, "convoy.hello.v1");
  doc["device_type"] = "esp32";
  doc["model"] = DEVICE_MODEL;
  doc["hw_rev"] = "A1";
  doc["fleet_tag"] = FLEET_TAG;
  doc["current_version"] = currentVersion;
  doc["active_slot"] = activeSlot;
  doc["battery"] = BATTERY_PERCENT;
  doc["network_quality"] = NETWORK_QUALITY;
  doc["resume_pending"] = false;
  doc["agent"] = "esp32-tcu/0.1";
  doc["trigger"] = trigger;

  String out;
  serializeJson(doc, out);
  mqtt.publish(T_HELLO.c_str(), out.c_str());

  Serial.printf("[%s] announced v%s battery=%d%% net=%d (trigger=%s)\n",
                DEVICE_ID, currentVersion.c_str(), BATTERY_PERCENT,
                NETWORK_QUALITY, trigger);
}

void publishHealth() {
  JsonDocument doc;
  addEnvelope(doc, "convoy.health.v1");
  doc["battery"] = BATTERY_PERCENT;
  doc["network_quality"] = NETWORK_QUALITY;
  doc["uptime_s"] = (millis() - bootMillis) / 1000;
  doc["current_version"] = currentVersion;
  doc["device_type"] = "esp32";
  doc["model"] = DEVICE_MODEL;

  String out;
  serializeJson(doc, out);
  mqtt.publish(T_HEALTH.c_str(), out.c_str());
}

void publishStatus(bool online, const char* reason) {
  JsonDocument doc;
  addEnvelope(doc, "convoy.status.v1");
  doc["online"] = online;
  if (reason) doc["reason"] = reason;

  String out;
  serializeJson(doc, out);
  // Retained: the broker holds the last status so a server that connects
  // later immediately knows this device exists and whether it is up.
  mqtt.publish(T_STATUS.c_str(), out.c_str(), true);
}

// ===========================================================================
// Inbound commands
// ===========================================================================
void onMessage(char* topic, byte* payload, unsigned int length) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) {
    Serial.println("undecodable message");
    return;
  }

  String t = String(topic);
  if (t == T_OTA_OFFER) {
    handleOffer(String((char*)payload).substring(0, length));
    return;
  }
  if (t == T_OTA_CHUNK) {
    handleChunk(String((char*)payload).substring(0, length));
    return;
  }

  const char* cmd = doc["cmd"] | "";

  if (strcmp(cmd, "ping") == 0) {
    JsonDocument reply;
    addEnvelope(reply, "convoy.pong.v1");
    reply["sent_at"] = doc["sent_at"];
    String out;
    serializeJson(reply, out);
    mqtt.publish(T_PONG.c_str(), out.c_str());

  } else if (strcmp(cmd, "announce") == 0) {
    // The server has restarted and is rebuilding its picture of the fleet.
    // Jitter the reply so a large fleet does not answer in the same
    // millisecond and stampede the broker.
    int jitter = doc["jitter_s"] | 2;
    delay(random(0, jitter * 1000));
    publishHello("announce");

  } else {
    Serial.printf("unhandled cmd=%s (OTA commands arrive in stage 2)\n", cmd);
  }
}


// ===========================================================================
// OTA
// ===========================================================================

/** Hex string to bytes. Returns false on any non-hex character. */
bool hexToBytes(const String& hex, uint8_t* out, size_t outLen) {
  if (hex.length() != outLen * 2) return false;
  for (size_t i = 0; i < outLen; i++) {
    char hi = hex[i * 2], lo = hex[i * 2 + 1];
    auto nib = [](char c) -> int {
      if (c >= '0' && c <= '9') return c - '0';
      if (c >= 'a' && c <= 'f') return c - 'a' + 10;
      if (c >= 'A' && c <= 'F') return c - 'A' + 10;
      return -1;
    };
    int h = nib(hi), l = nib(lo);
    if (h < 0 || l < 0) return false;
    out[i] = (uint8_t)((h << 4) | l);
  }
  return true;
}

String bytesToHex(const uint8_t* data, size_t len) {
  static const char* d = "0123456789abcdef";
  String out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    out += d[data[i] >> 4];
    out += d[data[i] & 0x0F];
  }
  return out;
}

void publishResult(bool success, const char* reason, const String& detail,
                   int chunkIndex) {
  JsonDocument doc;
  addEnvelope(doc, "convoy.result.v1");
  doc["campaign_id"] = ota.campaignId;
  doc["success"] = success;
  doc["reason_code"] = reason;
  doc["version"] = success ? ota.version : currentVersion;
  doc["active_slot"] = activeSlot;
  doc["battery"] = BATTERY_PERCENT;
  doc["network_quality"] = NETWORK_QUALITY;
  if (detail.length()) doc["detail"] = detail;
  if (chunkIndex >= 0) doc["chunk_index"] = chunkIndex;

  String out;
  serializeJson(doc, out);
  mqtt.publish(T_OTA_RESULT.c_str(), out.c_str());
}

void failUpdate(const char* reason, const String& detail, int chunkIndex = -1);

void failUpdate(const char* reason, const String& detail, int chunkIndex) {
  Serial.printf("UPDATE FAILED %s — %s\n", reason, detail.c_str());
  setLed(LED_FAULT);
  screenBanner("UPDATE ABORTED", detail);

  // Abandon the partially written slot. The RUNNING partition was never
  // touched, which is the entire reason A/B exists: a failure here costs a
  // download, not a device.
  if (ota.active) Update.abort();

  publishResult(false, reason, detail, chunkIndex);
  ota.active = false;
  ota.chunkHashes.clear();
}

/**
 * Verify the offer, then decide whether to accept it.
 *
 * The manifest arrives as base64 of the exact bytes the server signed. It is
 * verified AS RECEIVED and only then parsed. That ordering matters twice over:
 * nothing attacker-controlled influences a decision before it has been proven
 * authentic, and the board never has to reproduce a canonical JSON encoding,
 * which ArduinoJson cannot do.
 */
void handleOffer(const String& payload) {
  JsonDocument wire;
  if (deserializeJson(wire, payload)) {
    Serial.println("offer: undecodable envelope");
    return;
  }

  const char* manifestB64 = wire["manifest_b64"] | "";
  const char* signatureHex = wire["signature"] | "";
  const char* sigAlg = wire["sig_alg"] | "";

  if (strcmp(sigAlg, "ed25519") != 0) {
    Serial.printf("offer REJECTED: unsupported sig_alg %s\n", sigAlg);
    return;
  }

  // ---- decode the signed bytes ------------------------------------------
  size_t b64Len = strlen(manifestB64);
  size_t signedLen = 0;
  std::vector<uint8_t> signedBytes(b64Len);  // decoded is always smaller
  if (mbedtls_base64_decode(signedBytes.data(), signedBytes.size(), &signedLen,
                            (const uint8_t*)manifestB64, b64Len) != 0) {
    Serial.println("offer REJECTED: bad base64");
    return;
  }
  signedBytes.resize(signedLen);

  // ---- verify BEFORE parsing --------------------------------------------
  uint8_t pubKey[32], sig[64];
  if (!hexToBytes(String(SERVER_PUBLIC_KEY_HEX), pubKey, 32)) {
    Serial.println("SERVER_PUBLIC_KEY_HEX is not 64 hex characters");
    return;
  }
  if (!hexToBytes(String(signatureHex), sig, 64)) {
    Serial.println("offer REJECTED: signature is not 128 hex characters");
    return;
  }

  unsigned long t0 = millis();
  bool ok = Ed25519::verify(sig, pubKey, signedBytes.data(), signedLen);
  Serial.printf("signature check took %lu ms\n", millis() - t0);

  if (!ok) {
    // The property this protects: an attacker who controls the network and the
    // broker still cannot get firmware installed, because they cannot produce
    // a signature the board accepts.
    Serial.println("offer REJECTED: FAILED_SIGNATURE_INVALID");
    setLed(LED_FAULT);
    screenBanner("REJECTED", "bad signature");

    JsonDocument ack;
    addEnvelope(ack, "convoy.ack.v1");
    JsonDocument probe;
    deserializeJson(probe, signedBytes.data(), signedLen);
    ack["campaign_id"] = probe["campaign_id"] | "unknown";
    ack["accepted"] = false;
    ack["reason_code"] = Reason::SIG_INVALID;
    String out;
    serializeJson(ack, out);
    mqtt.publish(T_OTA_ACK.c_str(), out.c_str());
    return;
  }

  // ---- now the fields can be trusted ------------------------------------
  JsonDocument m;
  if (deserializeJson(m, signedBytes.data(), signedLen)) {
    Serial.println("offer REJECTED: signed payload is not JSON");
    return;
  }

  String campaignId = m["campaign_id"] | "";
  String forDevice = m["device_id"] | "";
  uint32_t versionCode = m["version_code"] | 0;
  bool rollback = m["rollback"] | false;
  int minBattery = m["min_battery"] | 0;

  ota.campaignId = campaignId;

  if (forDevice != String(DEVICE_ID)) {
    // Bound into the signature, so a genuine offer captured off the wire
    // cannot be replayed at a different board.
    Serial.printf("offer REJECTED: addressed to %s\n", forDevice.c_str());
    publishResult(false, Reason::SIG_INVALID, "wrong device");
    return;
  }

  uint32_t floorCode = prefs.getUInt("minver", 0);
  if (versionCode < floorCode && !rollback) {
    // Anti-rollback. An attacker who cannot forge a signature could otherwise
    // replay a genuine OLD manifest to push the board back to a version with a
    // known vulnerability. Only a signed rollback flag permits it.
    Serial.printf("offer REJECTED: version_code %u below floor %u\n",
                  versionCode, floorCode);
    publishResult(false, Reason::ANTI_ROLLBACK, "downgrade refused");
    return;
  }

  if (m["enc_alg"].is<const char*>() &&
      strcmp(m["enc_alg"] | "none", "none") != 0) {
    // Stage 3. Refusing plainly is better than downloading ciphertext the
    // board cannot decrypt and failing at the hash check with a misleading
    // reason code.
    Serial.println("offer REJECTED: encrypted firmware not supported yet");
    publishResult(false, Reason::SIG_INVALID, "encryption unsupported");
    return;
  }

  // ---- local safety gate -------------------------------------------------
  if (BATTERY_PERCENT < minBattery) {
    String detail = "BATTERY " + String(BATTERY_PERCENT) + "% < MIN " +
                    String(minBattery) + "%";
    Serial.printf("offer REJECTED: %s\n", detail.c_str());
    setLed(LED_FAULT);
    screenBanner("UPDATE ABORTED", detail);

    JsonDocument ack;
    addEnvelope(ack, "convoy.ack.v1");
    ack["campaign_id"] = campaignId;
    ack["accepted"] = false;
    ack["reason_code"] = Reason::LOW_BATTERY;
    String out;
    serializeJson(ack, out);
    mqtt.publish(T_OTA_ACK.c_str(), out.c_str());
    publishResult(false, Reason::LOW_BATTERY, detail);
    return;
  }

  // ---- set up the session -----------------------------------------------
  ota.firmwareId = String((const char*)(m["firmware_id"] | ""));
  ota.version = String((const char*)(m["version"] | ""));
  ota.versionCode = versionCode;
  ota.chunkCount = m["chunk_count"] | 0;
  ota.chunkSize = m["chunk_size"] | 0;
  ota.sizeBytes = m["size"] | 0;
  ota.wholeSha256 = String((const char*)(m["sha256"] | ""));
  ota.isRollback = rollback;
  ota.nextIndex = 0;

  ota.chunkHashes.clear();
  ota.chunkHashes.reserve(ota.chunkCount);
  for (JsonVariant v : m["chunk_hashes"].as<JsonArray>()) {
    ota.chunkHashes.push_back(String(v.as<const char*>()));
  }
  if (ota.chunkHashes.size() != ota.chunkCount) {
    Serial.println("offer REJECTED: chunk hash count mismatch");
    publishResult(false, Reason::SIG_INVALID, "malformed manifest");
    return;
  }

  // Open the INACTIVE partition. Update.begin picks the one that is not
  // running, so the firmware currently executing is never overwritten.
  if (!Update.begin(ota.sizeBytes, U_FLASH)) {
    String detail = "no space: need " + String(ota.sizeBytes);
    Serial.printf("offer REJECTED: %s\n", detail.c_str());
    publishResult(false, Reason::FLASH_WRITE, detail);
    return;
  }

  ota.active = true;
  Serial.printf("offer verified: v%s -> %u chunks, %u bytes%s\n",
                ota.version.c_str(), ota.chunkCount, ota.sizeBytes,
                rollback ? " (ROLLBACK)" : "");
  setLed(LED_BUSY);
  screen(String(DEVICE_ID), "v" + currentVersion + " -> v" + ota.version,
         rollback ? "ROLLBACK" : "downloading", "0/" + String(ota.chunkCount));

  JsonDocument ack;
  addEnvelope(ack, "convoy.ack.v1");
  ack["campaign_id"] = campaignId;
  ack["accepted"] = true;
  ack["nonce"] = m["nonce"];
  String out;
  serializeJson(ack, out);
  mqtt.publish(T_OTA_ACK.c_str(), out.c_str());
}

/** A chunk: decode, hash-check the plaintext, write it to the inactive slot. */
void handleChunk(const String& payload) {
  if (!ota.active) return;

  JsonDocument doc;
  if (deserializeJson(doc, payload)) return;
  if (String((const char*)(doc["campaign_id"] | "")) != ota.campaignId) return;

  uint32_t index = doc["index"] | 0;
  if (index != ota.nextIndex) {
    // Out of order or already written. The server streams sequentially, so
    // this is a QoS 1 redelivery; dropping it keeps the slot a faithful
    // prefix of the image.
    return;
  }

  const char* dataB64 = doc["data"] | "";
  size_t b64Len = strlen(dataB64);
  size_t rawLen = 0;
  std::vector<uint8_t> raw(b64Len);
  if (mbedtls_base64_decode(raw.data(), raw.size(), &rawLen,
                            (const uint8_t*)dataB64, b64Len) != 0) {
    failUpdate(Reason::CHUNK_HASH, "chunk " + String(index) + " bad base64", index);
    return;
  }

  // Hash the plaintext against the value in the SIGNED manifest. A chunk that
  // was altered in transit fails here, before a byte of it reaches flash.
  SHA256 sha;
  uint8_t digest[32];
  sha.reset();
  sha.update(raw.data(), rawLen);
  sha.finalize(digest, sizeof(digest));

  if (bytesToHex(digest, 32) != ota.chunkHashes[index]) {
    failUpdate(Reason::CHUNK_HASH,
               "chunk " + String(index) + " hash mismatch", index);
    return;
  }

  if (Update.write(raw.data(), rawLen) != rawLen) {
    failUpdate(Reason::FLASH_WRITE, "flash write failed at " + String(index), index);
    return;
  }

  ota.nextIndex++;

  JsonDocument prog;
  addEnvelope(prog, "convoy.progress.v1");
  prog["campaign_id"] = ota.campaignId;
  prog["chunk_index"] = index;
  prog["chunk_count"] = ota.chunkCount;
  prog["percent"] = (100.0 * ota.nextIndex) / ota.chunkCount;
  String out;
  serializeJson(prog, out);
  mqtt.publish(T_OTA_PROGRESS.c_str(), out.c_str());

  // Refresh the display every few chunks. Redrawing on every chunk would spend
  // more time on I2C than on the download.
  if (index % 4 == 0 || ota.nextIndex == ota.chunkCount) {
    int pct = (100 * ota.nextIndex) / ota.chunkCount;
    String bar;
    for (int i = 0; i < 16; i++) bar += (i < pct * 16 / 100) ? '#' : '.';
    screen(String(DEVICE_ID), "v" + ota.version,
           bar + " " + String(pct) + "%",
           "CHUNK " + String(ota.nextIndex) + "/" + String(ota.chunkCount));
  }

  if (ota.nextIndex >= ota.chunkCount) installUpdate();
}

/**
 * Finish the write, point the bootloader at the new slot, and reboot.
 *
 * The running partition is still intact at this point. It stays intact until
 * the new image boots and confirms itself in setup(); if it cannot, the
 * bootloader reverts on the next restart.
 */
void installUpdate() {
  if (!Update.end(true)) {
    failUpdate(Reason::IMAGE_HASH,
               "image verification failed: " + String(Update.errorString()));
    return;
  }

  Serial.printf("INSTALLED v%s (%u bytes) — rebooting to confirm\n",
                ota.version.c_str(), ota.sizeBytes);
  screen(String(DEVICE_ID), "v" + ota.version, "INSTALLED", "rebooting...");

  // Recorded BEFORE the reboot. The new image reads these on boot to know what
  // it is confirming, and to know what to report if it fails to.
  prefs.putString("pending", ota.version);
  prefs.putString("pending_campaign", ota.campaignId);
  prefs.putUInt("pending_code", ota.versionCode);
  prefs.putBool("rollback", ota.isRollback);
  prefs.putString("prev_version", currentVersion);

  publishResult(true, ota.isRollback ? Reason::ROLLED_BACK_MANUAL : Reason::SUCCESS);
  delay(600);  // let the publish leave before the radio dies
  ota.active = false;
  ESP.restart();
}

/**
 * Called early in setup(). Decides whether the image now running is trusted.
 *
 * ESP-IDF marks a freshly installed image PENDING_VERIFY. If it reaches this
 * point it has booted, joined WiFi and reached the broker, which is a
 * meaningful definition of "working", so it is marked valid. If it had
 * crashed before getting here, the bootloader would have reverted to the
 * previous slot on the next restart with no involvement from this code.
 *
 * This is the failure that hashes and signatures cannot catch: an image whose
 * bytes are exactly what the server sent, which simply does not run on this
 * device.
 */
void confirmBootIfPending() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  if (esp_ota_get_state_partition(running, &state) != ESP_OK) return;

  String pending = prefs.getString("pending", "");

  if (state == ESP_OTA_IMG_PENDING_VERIFY) {
    if (pending.length()) {
      currentVersion = pending;
      activeSlot = (activeSlot == "A") ? "B" : "A";
      prefs.putString("version", currentVersion);
      prefs.putString("slot", activeSlot);
      prefs.putUInt("minver", prefs.getUInt("pending_code", 0));
      prefs.remove("pending");
    }
    esp_ota_mark_app_valid_cancel_rollback();
    Serial.printf("new image confirmed: v%s slot %s\n",
                  currentVersion.c_str(), activeSlot.c_str());
  } else if (pending.length()) {
    // We are running an image that is already valid while a pending version is
    // recorded: the bootloader reverted. Report it, because from the server's
    // side an automatic rollback is otherwise indistinguishable from silence.
    String reverted = prefs.getString("prev_version", currentVersion);
    Serial.printf("ROLLED BACK automatically to v%s\n", reverted.c_str());
    prefs.remove("pending");
    pendingAutoRollbackReport = prefs.getString("pending_campaign", "");
  }
}

// ===========================================================================
// Connection
// ===========================================================================
void connectWifi() {
  screen("CONVOY", String("connecting"), String(WIFI_SSID));
  setLed(LED_BUSY);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("connecting to WiFi %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected, ip=%s rssi=%d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  screen("CONVOY", "wifi ok", WiFi.localIP().toString());
}

void connectMqtt() {
  netClient.setCACert(BROKER_ROOT_CA);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  // Chunk payloads in stage 2 are ~11 KB after base64. The default 256-byte
  // buffer would silently drop them, so it is raised now rather than being
  // discovered as a mystery later.
  mqtt.setBufferSize(16384);
  mqtt.setKeepAlive(20);

  while (!mqtt.connected()) {
    String clientId = String(DEVICE_ID) + "-" + String(esp_random(), HEX);
    Serial.printf("connecting to broker %s:%d as %s\n",
                  MQTT_HOST, MQTT_PORT, clientId.c_str());
    screen("CONVOY", "broker...", String(MQTT_HOST).substring(0, 18));

    // Last Will: if this board loses power or drops off the network, the
    // BROKER publishes this on its behalf. Offline detection therefore needs
    // no polling at all.
    JsonDocument will;
    will["schema"] = "convoy.status.v1";
    will["device_id"] = DEVICE_ID;
    will["online"] = false;
    will["reason"] = "last_will";
    String willPayload;
    serializeJson(will, willPayload);

    if (mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD,
                     T_STATUS.c_str(), 1, true, willPayload.c_str())) {
      Serial.println("broker connected");
      mqtt.subscribe(T_CMD.c_str(), 1);
      mqtt.subscribe(T_CMD_ALL.c_str(), 1);
      mqtt.subscribe(T_OTA_OFFER.c_str(), 1);
      mqtt.subscribe(T_OTA_CHUNK.c_str(), 1);
      publishStatus(true, nullptr);
      publishHello("connect");
      setLed(LED_IDLE);

      if (pendingAutoRollbackReport.length()) {
        JsonDocument doc;
        addEnvelope(doc, "convoy.result.v1");
        doc["campaign_id"] = pendingAutoRollbackReport;
        doc["success"] = false;
        doc["reason_code"] = "ROLLED_BACK_AUTOMATIC";
        doc["version"] = currentVersion;
        doc["detail"] = "new image did not confirm; bootloader reverted";
        doc["battery"] = BATTERY_PERCENT;
        doc["network_quality"] = NETWORK_QUALITY;
        String out;
        serializeJson(doc, out);
        mqtt.publish(T_OTA_RESULT.c_str(), out.c_str());
        pendingAutoRollbackReport = "";
      }
    } else {
      int st = mqtt.state();
      Serial.printf("broker refused, state=%d — retrying in 3s\n", st);
      // -2 is a TLS/connection failure (check the CA and the host);
      //  4 is bad credentials; 5 is not authorised.
      screen("CONVOY", "broker refused", "state " + String(st));
      setLed(LED_FAULT);
      delay(3000);
    }
  }
}

// ===========================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  bootMillis = millis();

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_BLUE, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  setLed(LED_OFF);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  // 0x3C is the usual address for a 0.96" SSD1306. A few modules use 0x3D.
  displayReady = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!displayReady) {
    // Not fatal. A device that refuses to do its job because a display is
    // missing has confused its output with its purpose.
    Serial.println("SSD1306 not found at 0x3C — continuing without display");
  }

  prefs.begin("convoy", false);
  currentVersion = prefs.getString("version", INITIAL_VERSION);
  activeSlot = prefs.getString("slot", "A");

  confirmBootIfPending();

  Serial.printf("\n=== CONVOY %s === v%s slot %s\n",
                DEVICE_ID, currentVersion.c_str(), activeSlot.c_str());

  String root = MQTT_TOPIC_ROOT;
  String id = DEVICE_ID;
  T_HELLO  = root + "/d/" + id + "/hello";
  T_HEALTH = root + "/d/" + id + "/health";
  T_STATUS = root + "/d/" + id + "/status";
  T_PONG   = root + "/d/" + id + "/pong";
  T_CMD    = root + "/s/" + id + "/cmd";
  T_CMD_ALL = root + "/s/all/cmd";
  T_OTA_OFFER = root + "/s/" + id + "/ota/offer";
  T_OTA_CHUNK = root + "/s/" + id + "/ota/chunk";
  T_OTA_ACK = root + "/d/" + id + "/ota/ack";
  T_OTA_PROGRESS = root + "/d/" + id + "/ota/progress";
  T_OTA_RESULT = root + "/d/" + id + "/ota/result";

  screen("CONVOY", String(DEVICE_ID), "v" + currentVersion, "booting");
  connectWifi();
  connectMqtt();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    setLed(LED_FAULT);
    screen(DEVICE_ID, "wifi lost", "reconnecting");
    connectWifi();
  }
  if (!mqtt.connected()) {
    setLed(LED_FAULT);
    connectMqtt();
  }
  mqtt.loop();

  if (millis() - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = millis();
    publishHealth();

    String batt = String(BATTERY_PERCENT) + "%";
    screen(String(DEVICE_ID),
           "v" + currentVersion,
           "batt " + batt + "  net " + String(NETWORK_QUALITY),
           "ONLINE  slot " + activeSlot);
  }
}
