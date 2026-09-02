// ===========================================================================
// CONVOY — ESP32 Telematics Control Unit
// Stage 1: connectivity, telemetry, and the physical display layer.
//
// This board speaks the SAME protocol as the Dockerised simulators. The server
// has no ESP32-specific code path; `device_type` exists only so the dashboard
// can label it. That is the point of having written the protocol down rather
// than letting it emerge from one implementation: a second implementation, in
// a different language on different hardware, either interoperates or reveals
// that the specification was ambiguous.
//
// Stage 2 adds the OTA path (signature verification, chunked download into the
// inactive partition, resume, install, rollback). Stage 3 adds payload
// decryption. Connectivity is proven first, alone, because debugging a
// firmware transfer on top of an unproven MQTT connection means never knowing
// which layer failed.
//
// Board:            ESP32 Dev Module
// Partition scheme: Minimal SPIFFS (1.9MB APP with OTA / 190KB SPIFFS)
// Libraries:        PubSubClient, ArduinoJson, Adafruit SSD1306, Adafruit GFX
// ===========================================================================

#include <Adafruit_GFX.h>
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
      publishStatus(true, nullptr);
      publishHello("connect");
      setLed(LED_IDLE);
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
