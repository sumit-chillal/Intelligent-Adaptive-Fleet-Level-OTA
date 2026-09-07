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
#include <esp_partition.h>
#include <esp_system.h>
#include <mbedtls/base64.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <sys/time.h>
#include <time.h>

#include "config.h"
#include "convoy_types.h"

// The two controllers share the Adafruit_GFX drawing API, so only the object
// type, the begin() call and the colour constants differ. Aliasing those three
// keeps every drawing call in this file identical for both.
#ifdef OLED_SH1106
  #include <Adafruit_SH110X.h>
  #define OLED_CLASS Adafruit_SH1106G
  #define OLED_WHITE SH110X_WHITE
  #define OLED_BLACK SH110X_BLACK
#else
  #include <Adafruit_SSD1306.h>
  #define OLED_CLASS Adafruit_SSD1306
  #define OLED_WHITE SSD1306_WHITE
  #define OLED_BLACK SSD1306_BLACK
#endif

// The Arduino loop task gets 8 KB of stack by default, and every OTA chunk is
// processed inside the MQTT callback -- which already has the TLS stack
// beneath it, and then adds JSON parsing, base64 decoding, SHA-256 and a flash
// write on top.
//
// A stack overflow on ESP32 does not fail gracefully. It corrupts whatever sits
// below the stack and the board panics and reboots, which from the server looks
// exactly like a device that went offline mid-download: the last will fires,
// the campaign times out, and nothing anywhere says "stack".
//
// 16 KB costs 8 KB of RAM out of 320 KB and removes the entire failure mode.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

// ---------------------------------------------------------------------------
// Types and file-scope state, declared BEFORE anything that mentions them.
//
// Arduino generates prototypes for every function in the .ino and inserts them
// near the top of the file. If a type appears in a signature but is declared
// further down, the generated prototype references a type that does not exist
// yet and the compile fails on a line nobody wrote. The same applies to file
// scope variables used by functions defined above them.
//
// Declaring both here, before any function, removes the ordering constraint
// entirely rather than relying on where the IDE happens to place things.
// ---------------------------------------------------------------------------

// One decode buffer for the whole download, allocated when the offer is
// accepted and freed when it ends.
//
// An earlier version allocated an 11 KB vector inside the chunk handler for
// every chunk, on top of a String copy and a substring copy of the same
// message. Roughly 44 KB of allocate-and-free per chunk left the heap too
// fragmented to find a contiguous block after about nine chunks, which is
// exactly where the download stopped. Free memory was never short; CONTIGUOUS
// free memory was.
static uint8_t* chunkBuf = nullptr;
static size_t chunkBufLen = 0;

// --------------------------------------------------------------- display ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
OLED_CLASS display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool displayReady = false;
uint8_t oledAddr = 0;   // filled in by the I2C scan at boot

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
OtaSession ota;

// Forward declarations. Arduino generates prototypes for functions in the
// main .ino automatically, but only for simple signatures, and relying on that
// is how a working sketch breaks after an unrelated edit. Declaring them
// explicitly costs three lines and removes the ordering constraint entirely.
void handleOffer(const uint8_t* payload, size_t len);
void handleChunk(const uint8_t* payload, size_t len);
void installUpdate();
void confirmBootIfPending();
void publishResult(bool success, const char* reason, const String& detail = "",
                   int chunkIndex = -1);
// If the bootloader reverted a bad image, the campaign id is stashed here in
// setup() and reported once the broker connection is up.
String pendingAutoRollbackReport = "";
// What to show on the OLED for the first few seconds after boot, so an
// automatic revert is visible on the board itself and not only in a log.
String bootBanner = "";
String bootDetail = "";
bool revertedOnBoot = false;

// A freshly installed image is on probation until it proves it can reach the
// broker. 90 seconds is generous for a slow hotspot and still short enough
// that a demonstration does not stall.
static const int PROBATION_SECONDS = 90;
bool onProbation = false;
unsigned long probationStarted = 0;

void revertToPrevious();

// ===========================================================================
// LEDs — one meaning per colour, never two lit at once.
// ===========================================================================
LedState ledState = LED_OFF;

void setLed(LedState s) {
  ledState = s;
  digitalWrite(PIN_LED_GREEN, s == LED_IDLE);
  digitalWrite(PIN_LED_BLUE, s == LED_BUSY);
  digitalWrite(PIN_LED_RED, s == LED_FAULT);
}

/** Called from loop(). Only LED_REVERTED animates; everything else holds. */
void serviceLed() {
  if (ledState != LED_REVERTED) return;
  bool on = (millis() / 400) % 2;
  digitalWrite(PIN_LED_RED, on);
  digitalWrite(PIN_LED_GREEN, !on);
  digitalWrite(PIN_LED_BLUE, LOW);
}

/**
 * The state word, set large.
 *
 * Same vocabulary as the dashboard: a person reading the board from 30 cm and
 * a person reading the projector from ten feet see the same word for the same
 * event. A separate set of labels for the device would mean translating
 * between them mid-demonstration.
 */
void screenState(const String& word, const String& line2 = "",
                 const String& line3 = "") {
  if (!displayReady) return;
  display.clearDisplay();
  display.setTextColor(OLED_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.println(word);
  display.setTextSize(1);
  if (line2.length()) { display.setCursor(0, 24); display.println(line2); }
  if (line3.length()) { display.setCursor(0, 38); display.println(line3); }
  display.setCursor(0, 54);
  display.print(DEVICE_ID);
  display.display();
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
  display.setTextColor(OLED_WHITE);
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
  display.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, OLED_WHITE);
  display.setTextColor(OLED_BLACK);
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

  // Log every inbound message before doing anything with it. When a device
  // goes silent the first question is always whether the message arrived at
  // all, and without this line that question is unanswerable from the board.
  Serial.printf("<- %s (%u bytes, heap %u)\n", topic, length, ESP.getFreeHeap());

  if (t == T_OTA_OFFER) {
    handleOffer(payload, length);
    return;
  }
  if (t == T_OTA_CHUNK) {
    handleChunk(payload, length);
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
  screenState("FAILED", reason, detail.substring(0, 21));

  // Abandon the partially written slot. The RUNNING partition was never
  // touched, which is the entire reason A/B exists: a failure here costs a
  // download, not a device.
  if (ota.active) Update.abort();

  publishResult(false, reason, detail, chunkIndex);
  ota.active = false;
  ota.chunkHashes.clear();
  ota.chunkHashes.shrink_to_fit();
  free(chunkBuf);
  chunkBuf = nullptr;
  chunkBufLen = 0;
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
void handleOffer(const uint8_t* payload, size_t len) {
  Serial.printf("offer received: %u bytes, free heap %u\n",
                len, ESP.getFreeHeap());

  JsonDocument wire;
  // Parse straight from the network buffer. Wrapping it in a String first
  // would copy 12 KB for no benefit, and that copy is exactly the kind of
  // short-lived large allocation that fragments the heap.
  DeserializationError err = deserializeJson(wire, payload, len);
  if (err) {
    // NoMemory here means the JSON parsed larger than the heap allows, which
    // is a different problem from a truncated message and needs a different
    // fix, so the reason is printed rather than swallowed.
    Serial.printf("offer: undecodable envelope (%s)\n", err.c_str());
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
    screenState("REFUSED", "bad signature", "not from our server");

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
  DeserializationError merr = deserializeJson(m, signedBytes.data(), signedLen);
  if (merr) {
    Serial.printf("offer REJECTED: signed payload not parseable (%s), "
                  "%u bytes, heap %u\n", merr.c_str(), signedLen,
                  ESP.getFreeHeap());
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
    screenState("REFUSED", Reason::LOW_BATTERY, detail.substring(0, 21));

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

  // Convert hex to bytes once, here, rather than comparing hex strings 128
  // times during the download.
  ota.chunkHashes.assign((size_t)ota.chunkCount * 32, 0);
  size_t idx = 0;
  for (JsonVariant v : m["chunk_hashes"].as<JsonArray>()) {
    if (idx >= ota.chunkCount) break;
    if (!hexToBytes(String(v.as<const char*>()),
                    &ota.chunkHashes[idx * 32], 32)) {
      Serial.println("offer REJECTED: malformed chunk hash");
      publishResult(false, Reason::SIG_INVALID, "malformed manifest");
      return;
    }
    idx++;
  }
  if (idx != ota.chunkCount) {
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
  screenState(rollback ? "ROLLBACK" : "OFFER OK",
              "v" + currentVersion + " -> v" + ota.version,
              String(ota.chunkCount) + " chunks verified");

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
void handleChunk(const uint8_t* payload, size_t len) {
  if (!ota.active) return;

  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;

  const char* cid = doc["campaign_id"] | "";
  if (ota.campaignId != cid) return;

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
  if (chunkBuf == nullptr || chunkBufLen < b64Len) {
    free(chunkBuf);
    chunkBuf = (uint8_t*)malloc(b64Len);
    chunkBufLen = b64Len;
    if (chunkBuf == nullptr) {
      failUpdate(Reason::FLASH_WRITE, "out of memory for chunk buffer", index);
      chunkBufLen = 0;
      return;
    }
  }
  if (mbedtls_base64_decode(chunkBuf, chunkBufLen, &rawLen,
                            (const uint8_t*)dataB64, b64Len) != 0) {
    failUpdate(Reason::CHUNK_HASH, "chunk " + String(index) + " bad base64", index);
    return;
  }
  uint8_t* raw = chunkBuf;

  // Hash the plaintext against the value in the SIGNED manifest. A chunk that
  // was altered in transit fails here, before a byte of it reaches flash.
  SHA256 sha;
  uint8_t digest[32];
  sha.reset();
  sha.update(raw, rawLen);
  sha.finalize(digest, sizeof(digest));

  // Compare 32 bytes, not 64 hex characters. No allocation, no String.
  if (memcmp(digest, &ota.chunkHashes[(size_t)index * 32], 32) != 0) {
    failUpdate(Reason::CHUNK_HASH,
               "chunk " + String(index) + " hash mismatch", index);
    return;
  }

  if (Update.write(raw, rawLen) != rawLen) {
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
  if (index % 8 == 0 || ota.nextIndex == ota.chunkCount) {
    Serial.printf("chunk %u/%u  heap %u  stack free %u\n",
                  ota.nextIndex, ota.chunkCount, ESP.getFreeHeap(),
                  uxTaskGetStackHighWaterMark(NULL));
  }
  if (index % 4 == 0 || ota.nextIndex == ota.chunkCount) {
    int pct = (100 * ota.nextIndex) / ota.chunkCount;
    if (displayReady) {
      display.clearDisplay();
      display.setTextColor(OLED_WHITE);
      display.setTextSize(1);
      display.setCursor(0, 0);
      display.println("DOWNLOADING");
      display.setCursor(0, 12);
      display.print("v");
      display.print(currentVersion);
      display.print(" -> v");
      display.println(ota.version);

      // Drawn rather than spelled out in characters: a filling bar is
      // readable from across a room, where "CHUNK 88/135" is not.
      display.drawRect(0, 28, 128, 12, OLED_WHITE);
      display.fillRect(2, 30, (124 * pct) / 100, 8, OLED_WHITE);

      display.setCursor(0, 44);
      display.print(ota.nextIndex);
      display.print("/");
      display.print(ota.chunkCount);
      display.print("  ");
      display.print(pct);
      display.println("%");
      display.setCursor(0, 56);
      display.print(DEVICE_ID);
      display.display();
    }
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
  screenState("INSTALLED", "v" + ota.version, "rebooting to confirm");

  // Recorded BEFORE the reboot. The new image reads these on boot to know what
  // it is confirming, and to know what to report if it fails to.
  // Record WHERE the image went, not just what it was. On the next boot the
  // running partition's address is the unambiguous answer to "did the update
  // take", and it needs something to be compared against.
  const esp_partition_t* target = esp_ota_get_next_update_partition(NULL);
  const esp_partition_t* current = esp_ota_get_running_partition();
  prefs.putUInt("pending_addr", target ? target->address : 0);
  // Where to go back to if the new image cannot prove itself. Recorded now,
  // while the running partition is still the old one.
  prefs.putUInt("prev_addr", current ? current->address : 0);
  prefs.putString("pending", ota.version);
  prefs.putString("pending_campaign", ota.campaignId);
  prefs.putUInt("pending_code", ota.versionCode);
  prefs.putBool("rollback", ota.isRollback);
  prefs.putString("prev_version", currentVersion);
  // Record WHICH partition the new image was written to. On the next boot,
  // comparing the running partition against this is what distinguishes "the
  // new image booted" from "the bootloader reverted" -- see
  // confirmBootIfPending for why the image-state flag cannot be used.
  const esp_partition_t* next = esp_ota_get_next_update_partition(NULL);
  prefs.putUInt("pending_addr", next ? next->address : 0);

  ota.chunkHashes.clear();
  ota.chunkHashes.shrink_to_fit();
  free(chunkBuf);
  chunkBuf = nullptr;
  chunkBufLen = 0;

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
/**
 * Put the previous image back and restart.
 *
 * Called when a newly installed image has not reached the broker within the
 * probation window. The old partition was never erased, so this is a matter of
 * pointing the bootloader back at it -- the whole reason A/B exists.
 */
void revertToPrevious() {
  uint32_t prevAddr = prefs.getUInt("prev_addr", 0);
  const esp_partition_t* prev = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, NULL);

  // Walk the app partitions for the one we came from.
  esp_partition_iterator_t it = esp_partition_find(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, NULL);
  const esp_partition_t* target = nullptr;
  while (it != NULL) {
    const esp_partition_t* part = esp_partition_get(it);
    if (part->address == prevAddr) { target = part; break; }
    it = esp_partition_next(it);
  }
  if (it) esp_partition_iterator_release(it);

  if (target == nullptr) {
    Serial.println("cannot revert: previous partition not found");
    prefs.remove("pending");
    onProbation = false;
    return;
  }

  Serial.printf("PROBATION FAILED — reverting to the previous image at "
                "0x%08x\n", (unsigned)prevAddr);
  screenState("REVERTING", "v" + currentVersion + " unreachable",
              "restoring previous");
  setLed(LED_REVERTED);
  delay(2500);

  // Mark what happened BEFORE the restart, so the restored image can report it.
  prefs.putBool("was_reverted", true);
  prefs.putString("version", prefs.getString("prev_version", "unknown"));
  prefs.remove("pending");
  prefs.remove("pending_addr");

  esp_ota_set_boot_partition(target);
  delay(200);
  ESP.restart();
}

void confirmBootIfPending() {
  // A restored image announces the revert once, on its first boot back.
  if (prefs.getBool("was_reverted", false)) {
    prefs.putBool("was_reverted", false);
    revertedOnBoot = true;
    bootBanner = "REVERTED";
    bootDetail = "bad image rejected";
    pendingAutoRollbackReport = prefs.getString("pending_campaign", "");
    Serial.printf("ROLLED BACK automatically — running v%s again\n",
                  currentVersion.c_str());
  }

  const esp_partition_t* running = esp_ota_get_running_partition();
  String pending = prefs.getString("pending", "");
  uint32_t expectedAddr = prefs.getUInt("pending_addr", 0);

  if (!pending.length()) {
    // Nothing was installed since the last boot. Normal start.
    return;
  }

  // Compare PARTITIONS, not image state.
  //
  // The first version of this checked for ESP_OTA_IMG_PENDING_VERIFY, on the
  // assumption that a freshly installed image always boots in that state. It
  // only does when the bootloader is built with rollback support, which the
  // stock Arduino core is not -- so a perfectly successful update booted
  // already-valid, this function concluded the bootloader had reverted, and
  // the device reported an automatic rollback that never happened while
  // actually running the new firmware.
  //
  // Which partition is executing is not a matter of interpretation. If it is
  // the one the update was written to, the update took. If it is the other
  // one, the bootloader really did revert.
  if (running->address == expectedAddr) {
    // The new image is executing. That is necessary but NOT sufficient: an
    // image can boot and still be useless, and the useful definition of a
    // working TCU is one that can reach the fleet server.
    //
    // So the image enters PROBATION here rather than being declared good. It
    // is confirmed only once the broker connection succeeds. If that does not
    // happen within the deadline, revertToPrevious() puts the old image back.
    //
    // This is application-level self-healing, not the bootloader's rollback.
    // ESP-IDF can revert an image that fails to boot at all, but only when the
    // bootloader is built with CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE, which
    // the stock Arduino core does not enable. Rather than claim a capability
    // the platform does not provide, this covers the failure that IS reachable
    // from here: firmware that runs but cannot do its job.
    onProbation = true;
    probationStarted = millis();
    currentVersion = pending;
    activeSlot = (running->address == 0x10000) ? "A" : "B";
    prefs.putString("version", currentVersion);
    prefs.putString("slot", activeSlot);
    prefs.putUInt("minver", prefs.getUInt("pending_code", 0));

    // Cancel the pending-verify state if the bootloader is using one. Harmless
    // when it is not, and essential when it is: without it the next reboot
    // reverts a working image.
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK &&
        state == ESP_OTA_IMG_PENDING_VERIFY) {
      esp_ota_mark_app_valid_cancel_rollback();
    }

    Serial.printf("new image RUNNING: v%s from %s at 0x%08x — on probation, "
                  "must reach the broker within %d s\n",
                  currentVersion.c_str(), running->label,
                  (unsigned)running->address, PROBATION_SECONDS);
    bootBanner = "PROBATION";
    bootDetail = "v" + currentVersion + " must reach server";
  } else {
    // Running from a different partition than the one we wrote. The bootloader
    // rejected the new image and fell back.
    String reverted = prefs.getString("prev_version", currentVersion);
    Serial.printf("ROLLED BACK automatically to v%s "
                  "(expected 0x%08x, running 0x%08x from %s)\n",
                  reverted.c_str(), (unsigned)expectedAddr,
                  (unsigned)running->address, running->label);
    prefs.remove("pending");
    prefs.remove("pending_addr");
    pendingAutoRollbackReport = prefs.getString("pending_campaign", "");
    bootBanner = "REVERTED";
    bootDetail = "bad image; back on v" + reverted;
    revertedOnBoot = true;
  }
}

// ===========================================================================
// Connection
// ===========================================================================
void syncClock();

void connectWifi() {
  screen("CONVOY", String("connecting"), String(WIFI_SSID));
  setLed(LED_BUSY);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("connecting to WiFi %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
    // Same reasoning as the broker loop: an image whose WiFi settings are
    // wrong would otherwise spin here forever, past its own deadline.
    if (onProbation &&
        millis() - probationStarted > PROBATION_SECONDS * 1000UL) {
      Serial.println();
      revertToPrevious();
    }
  }
  Serial.printf("\nWiFi connected, ip=%s rssi=%d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  screen("CONVOY", "wifi ok", WiFi.localIP().toString());
  syncClock();
}

/**
 * Get the wall-clock time before attempting TLS.
 *
 * An ESP32 has no battery-backed real-time clock. It boots believing it is
 * 1 January 1970, and certificate validation compares the certificate's
 * notBefore and notAfter dates against that. Against a 1970 clock every real
 * certificate looks "not yet valid", so the handshake fails -- and it fails
 * with a bare connection error that says nothing about time, which sends you
 * hunting for a wrong password or a truncated certificate instead.
 *
 * The alternative is setInsecure(), which makes the symptom disappear by
 * disabling the check that produced it. That would leave the board willing to
 * talk to anything presenting any certificate, which is precisely the attack
 * the design spends an Ed25519 signature defending against elsewhere.
 */
void syncClock() {
  // Seed the clock from the BUILD TIME before trying NTP.
  //
  // Certificate validation only needs the year to be roughly right. The build
  // timestamp is baked in by the compiler and is always in the recent past,
  // which satisfies notBefore and notAfter without any network at all.
  //
  // This matters because NTP is not always reachable. Many phone hotspots
  // block outbound UDP port 123, so a device tethered to one waits forever for
  // a reply that will never come, and the failure looks like a TLS problem
  // rather than a blocked port.
  //
  // A build-time seed is not a substitute for real time -- it drifts, and a
  // board flashed months ago would think it is months ago -- so NTP still runs
  // afterwards to correct it. It just is not a precondition for connecting.
  struct tm build = {0};
  if (strptime(__DATE__ " " __TIME__, "%b %d %Y %H:%M:%S", &build)) {
    time_t seed = mktime(&build);
    struct timeval tv = {.tv_sec = seed, .tv_usec = 0};
    settimeofday(&tv, nullptr);
    Serial.printf("clock seeded from build time: %s %s\n", __DATE__, __TIME__);
  }

  configTime(0, 0, "pool.ntp.org", "time.google.com", "time.cloudflare.com");
  Serial.print("waiting for NTP");
  screen("CONVOY", "syncing clock", "for TLS...");

  time_t now = 0;
  int tries = 0;
  // Ten attempts, not forty. If NTP is blocked, waiting longer will not help,
  // and the build-time seed is already good enough to proceed.
  while (tries < 10) {
    delay(500);
    Serial.print(".");
    time(&now);
    if (now > 1600000000 && tries > 2) break;
    tries++;
  }

  time(&now);
  if (now < 1600000000) {
    Serial.println("\nWARNING: clock is not set and the build-time seed "
                   "failed. TLS will fail: a certificate cannot be "
                   "date-checked without a clock.");
    screen("CONVOY", "clock FAILED", "TLS will fail");
    return;
  }

  struct tm t;
  gmtime_r(&now, &t);
  Serial.printf("\ntime synced: %04d-%02d-%02d %02d:%02d:%02d UTC\n",
                t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
                t.tm_hour, t.tm_min, t.tm_sec);
}

void connectMqtt() {
#ifdef ALLOW_INSECURE_TLS
  // DIAGNOSTIC ONLY. Accepts ANY certificate, which means the board will
  // happily talk to anyone impersonating the broker. It exists solely to
  // answer the question "is my CA wrong?" in one upload, and must never be
  // left enabled: it disables the check that the rest of this design spends an
  // Ed25519 signature defending.
  //
  // It is behind a #define rather than a commented-out line because a comment
  // is easy to leave in place by accident, and this failure mode is silent --
  // everything works, and nothing tells you the connection is unauthenticated.
  netClient.setInsecure();
  Serial.println();
  Serial.println("****************************************************");
  Serial.println("** WARNING: ALLOW_INSECURE_TLS is enabled.        **");
  Serial.println("** Certificate validation is OFF. Diagnostic use  **");
  Serial.println("** only -- remove the #define in config.h before  **");
  Serial.println("** any demonstration or measurement.              **");
  Serial.println("****************************************************");
  Serial.println();
  screen("!! INSECURE TLS !!", "cert check OFF", "diagnostic only");
  delay(1500);
#else
  netClient.setCACert(BROKER_ROOT_CA);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  // Two different large payloads have to fit in this buffer.
  //
  // A chunk is ~11 KB after base64. The OFFER is bigger and less obvious: the
  // manifest carries one SHA-256 per chunk, so a 1 MB image at 8 KB chunks
  // means 128 hashes of 64 characters — over 8 KB of hashes alone, ~12 KB once
  // the manifest is base64-encoded inside the envelope.
  //
  // PubSubClient does not report an oversized packet. It drops it and returns
  // to the loop, so the symptom is a device that stays silent while the server
  // waits for an ack it will never receive. 24 KB leaves room for a 2 MB image
  // at the same chunk size.
  if (!mqtt.setBufferSize(24576)) {
    Serial.println("FATAL: could not allocate the 24 KB MQTT buffer");
  }
  mqtt.setKeepAlive(20);
  // A 12 KB payload over TLS on a weak link can take a while to assemble.
  // The 15 s default can expire mid-read and abandon a valid message.
  mqtt.setSocketTimeout(30);

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

      if (onProbation) {
        // Reaching the broker is the pass condition. Only now is the image
        // recorded as good and the fallback discarded.
        onProbation = false;
        prefs.remove("pending");
        prefs.remove("pending_addr");
        esp_ota_img_states_t st;
        const esp_partition_t* run = esp_ota_get_running_partition();
        if (esp_ota_get_state_partition(run, &st) == ESP_OK &&
            st == ESP_OTA_IMG_PENDING_VERIFY) {
          esp_ota_mark_app_valid_cancel_rollback();
        }
        Serial.printf("PROBATION PASSED — v%s confirmed\n",
                      currentVersion.c_str());
        screenState("CONFIRMED", "v" + currentVersion, "reached the server");
        delay(2000);
      }

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
      // The probation deadline has to be checked HERE, not only in loop().
      //
      // connectMqtt() spins on `while (!mqtt.connected())` and does not return
      // until it succeeds, so an image that can never reach the broker never
      // reaches loop() either -- and the deadline that was supposed to rescue
      // it is in loop(). The first version of this reverted nothing and
      // retried a dead host indefinitely.
      //
      // A recovery path that only runs when the system is healthy enough to
      // get back to the main loop is not a recovery path.
      if (onProbation &&
          millis() - probationStarted > PROBATION_SECONDS * 1000UL) {
        revertToPrevious();
        return;   // unreachable: revertToPrevious restarts the board
      }

      Serial.printf("broker refused, state=%d — retrying in 3s\n", st);
      if (st == -2) {
        // -2 is a TLS/connection failure, which has three usual causes and no
        // way to tell them apart from the code alone. Naming them beats
        // guessing.
        time_t now;
        time(&now);
        Serial.printf("  state=-2 is a TLS failure. Check: clock synced "
                      "(epoch now %ld, must be > 1600000000), "
                      "BROKER_ROOT_CA complete, MQTT_HOST correct.\n",
                      (long)now);
      }
      // -2 is a TLS/connection failure (check the CA and the host);
      //  4 is bad credentials; 5 is not authorised.
      if (onProbation) {
        long left = PROBATION_SECONDS -
                    (long)((millis() - probationStarted) / 1000);
        screenState("PROBATION", "v" + currentVersion + " unproven",
                    "revert in " + String(left > 0 ? left : 0) + "s");
      } else {
        screen("CONVOY", "broker refused", "state " + String(st));
      }
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
  // 100 kHz rather than the 400 kHz default. Breadboard jumpers have enough
  // capacitance to corrupt the faster clock, which shows up as a display full
  // of noise rather than as an error.
  Wire.setClock(100000);

  // Scan the bus before assuming an address.
  //
  // These modules ship at 0x3C or 0x3D and look identical. Hard-coding one of
  // them turns a wrong guess into "the display does nothing", with no way to
  // tell that apart from a wiring fault, a dead module, or a power problem.
  // Listing what actually responds separates all four in one line.
  Serial.print("I2C scan:");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf(" 0x%02X", addr);
      found++;
      if (addr == 0x3C || addr == 0x3D) oledAddr = addr;
    }
  }
  if (found == 0) {
    Serial.println(" nothing responded");
    Serial.println("  -> nothing is on the bus at all. Check: VCC on 3V3 "
                   "(not VIN), GND connected, SDA on D21, SCK/SCL on D22, "
                   "and that the jumpers are seated firmly.");
  } else {
    Serial.printf("  (%u device(s))\n", found);
  }

  if (oledAddr) {
#ifdef OLED_SH1106
    displayReady = display.begin(oledAddr, true);
    const char* driver = "SH1106";
#else
    displayReady = display.begin(SSD1306_SWITCHCAPVCC, oledAddr);
    const char* driver = "SSD1306";
#endif
    Serial.printf("%s at 0x%02X: %s\n", driver, oledAddr,
                  displayReady ? "initialised" : "responded but init failed");
    if (displayReady) {
      Serial.println("  If the screen shows speckle rather than text, the "
                     "controller is the other one: toggle OLED_SH1106 in "
                     "config.h.");
    }
  } else {
    // Not fatal. A device that refuses to do its job because a display is
    // missing has confused its output with its purpose.
    Serial.println("no OLED found — continuing without a display");
  }

  if (displayReady) {
    // Prove the panel works before any application logic runs. If this shows
    // and later screens do not, the fault is in what is drawn, not the wiring.
    display.clearDisplay();
    display.setTextColor(OLED_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println("CONVOY");
    display.setCursor(0, 16);
    display.print("OLED OK 0x");
    display.println(oledAddr, HEX);
#ifdef OLED_SH1106
    display.setCursor(64, 16);
    display.println("SH1106");
#endif
    display.setCursor(0, 32);
    display.println(DEVICE_ID);
    display.display();
    delay(1500);
  }

  prefs.begin("convoy", false);
  currentVersion = prefs.getString("version", INITIAL_VERSION);
  activeSlot = prefs.getString("slot", "A");

  confirmBootIfPending();

  Serial.printf("\n=== CONVOY %s === v%s slot %s\n",
                DEVICE_ID, currentVersion.c_str(), activeSlot.c_str());
  Serial.printf("free heap at boot: %u bytes\n", ESP.getFreeHeap());
  Serial.printf("loop task stack:   %u bytes free\n",
                uxTaskGetStackHighWaterMark(NULL));

  // If the last boot was a panic rather than a normal restart, say so. An
  // unexplained reboot in the middle of a download is otherwise invisible from
  // this end, and indistinguishable from a network drop at the other.
  esp_reset_reason_t reason = esp_reset_reason();
  if (reason == ESP_RST_PANIC) {
    Serial.println("!! previous boot ended in a PANIC (stack overflow or "
                   "invalid memory access) !!");
  } else if (reason == ESP_RST_BROWNOUT) {
    Serial.println("!! previous boot ended in a BROWNOUT — the supply voltage "
                   "dipped. Use a better cable or a powered hub. !!");
  } else if (reason == ESP_RST_TASK_WDT || reason == ESP_RST_INT_WDT) {
    Serial.println("!! previous boot ended in a WATCHDOG reset — something "
                   "blocked for too long !!");
  }

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

  if (bootBanner.length()) {
    screenState(bootBanner, bootDetail, "");
    setLed(revertedOnBoot ? LED_REVERTED : LED_IDLE);
    delay(4000);   // long enough to read and photograph
  } else {
    screen("CONVOY", String(DEVICE_ID), "v" + currentVersion, "booting");
  }
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
  serviceLed();

  if (onProbation && millis() - probationStarted > PROBATION_SECONDS * 1000UL) {
    revertToPrevious();
  }

  if (millis() - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = millis();
    publishHealth();

    if (revertedOnBoot) {
      screenState("REVERTED", "running v" + currentVersion,
                  "last update rejected");
    } else {
      screenState("ONLINE", "v" + currentVersion + "  slot " + activeSlot,
                  "batt " + String(BATTERY_PERCENT) + "%  net " +
                  String(NETWORK_QUALITY));
    }
  }
}
