// =====================================================================
//  Smart parking - ESP32 IoT gateway (one per parking lot)
//
//  Reads:   entrance/exit vehicle detectors, lane RFID readers, card-return
//           reader, one magnetic sensor + one local RFID reader per space
//  Drives:  barriers, card dispenser, space guidance LEDs
//  Talks to the backend over HTTP:
//     POST /api/gateways/<id>/events     (every sensor/reader event, queued + retried)
//     GET  /api/gateways/<id>/commands   (OPEN_BARRIER, DISPENSE_CARD, SPACE_INDICATOR)
//
//  Libraries (Arduino Library Manager): MFRC522 (GithubCommunity), ArduinoJson (v7), ESP32Servo
//  Board: "ESP32 Dev Module" (esp32 by Espressif)
//  Serial commands (115200): 'c' = recalibrate all magnetic sensors (spaces EMPTY!),
//                            'm' = toggle live magnetic deviation print
// =====================================================================
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <SPI.h>
#include <Wire.h>
#include <MFRC522.h>
#include <ESP32Servo.h>
#include <Preferences.h>

#include "config.h"
#include "MagneticSpaceSensor.h"

constexpr size_t N_READERS = sizeof(RFID_SS_PINS) / sizeof(RFID_SS_PINS[0]);
constexpr size_t N_LANES   = sizeof(LANES) / sizeof(LANES[0]);
constexpr size_t N_SPACES  = sizeof(SPACES) / sizeof(SPACES[0]);

// ------------------------------------------------------------------ state
MFRC522* readers[N_READERS];
Servo barriers[N_LANES];
Preferences prefs;

struct LaneState {
  bool rawLast = false, present = false;
  uint32_t rawChangedAt = 0;
  bool scanning = false; uint32_t scanStart = 0;
  bool barrierOpen = false; uint32_t barrierOpenedAt = 0;
  uint32_t dispenseUntil = 0;
  String lastUid; uint32_t lastUidAt = 0;
  String lastReturnUid; uint32_t lastReturnAt = 0;
};
LaneState lanes[N_LANES];

enum LedMode : uint8_t { LED_OFF, LED_ASSIGNED, LED_OK, LED_WRONG };
struct SpaceState {
  MagneticSpaceSensor sensor;
  uint32_t lastSample = 0;
  bool occupied = false; uint32_t occupiedAt = 0;
  bool faultReported = false;
  String lastUid; uint32_t lastPresenceCheck = 0;
  LedMode led = LED_OFF; uint32_t ledUntil = 0;
};
SpaceState spaces[N_SPACES];

bool printMagnetic = false;
uint32_t lastPoll = 0, lastWifiTry = 0;

// ------------------------------------------------------------------ event queue (survives Wi-Fi drops)
constexpr int QUEUE_SIZE = 40;
String eventQueue[QUEUE_SIZE];
int qHead = 0, qCount = 0;

void queueEvent(const char* type, const char* laneId, const char* spaceCode,
                const String& uid = "", int occupied = -1, float magnitude = NAN, const char* detail = nullptr) {
  JsonDocument doc;
  doc["type"] = type;
  if (laneId) doc["lane_id"] = laneId;
  if (spaceCode) doc["space_code"] = spaceCode;
  if (uid.length()) doc["uid"] = uid;
  if (occupied >= 0) doc["occupied"] = occupied == 1;
  if (!isnan(magnitude)) doc["magnitude"] = magnitude;
  if (detail) doc["detail"] = detail;
  String body; serializeJson(doc, body);
  if (qCount == QUEUE_SIZE) { qHead = (qHead + 1) % QUEUE_SIZE; qCount--; Serial.println("[queue] full, oldest dropped"); }
  eventQueue[(qHead + qCount) % QUEUE_SIZE] = body;
  qCount++;
  Serial.printf("[event] %s\n", body.c_str());
}

// Returns true when the event can leave the queue (accepted, or rejected as invalid).
bool postEvent(const String& body) {
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.begin(String(BACKEND_URL) + "/api/gateways/" + GATEWAY_ID + "/events");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.POST(body);
  if (code >= 400 && code < 500) Serial.printf("[http] event rejected %d: %s\n", code, http.getString().c_str());
  http.end();
  return (code >= 200 && code < 300) || (code >= 400 && code < 500);
}

void flushEvents() {
  for (int sent = 0; qCount > 0 && sent < 4 && WiFi.status() == WL_CONNECTED; sent++) {
    if (!postEvent(eventQueue[qHead])) break;          // keep order: retry later
    eventQueue[qHead] = "";
    qHead = (qHead + 1) % QUEUE_SIZE; qCount--;
  }
}

// ------------------------------------------------------------------ RFID
// WUPA wakes cards that are idle or halted, so a parked vehicle's tag can be read repeatedly.
bool readUid(int8_t idx, String& out) {
  if (idx < 0 || idx >= (int8_t)N_READERS) return false;
  MFRC522* r = readers[idx];
  byte atqa[2]; byte size = sizeof(atqa);
  MFRC522::StatusCode st = r->PICC_WakeupA(atqa, &size);
  if (st != MFRC522::STATUS_OK && st != MFRC522::STATUS_COLLISION) return false;
  if (!r->PICC_ReadCardSerial()) return false;
  out = "";
  for (byte i = 0; i < r->uid.size; i++) {
    if (r->uid.uidByte[i] < 0x10) out += '0';
    out += String(r->uid.uidByte[i], HEX);
  }
  out.toUpperCase();
  r->PICC_HaltA();
  return true;
}

void initReaders() {
  for (size_t i = 0; i < N_READERS; i++) { pinMode(RFID_SS_PINS[i], OUTPUT); digitalWrite(RFID_SS_PINS[i], HIGH); }
  SPI.begin();
  for (size_t i = 0; i < N_READERS; i++) {
    readers[i] = new MFRC522(RFID_SS_PINS[i], MFRC522::UNUSED_PIN);   // soft reset, RST tied to 3.3V
    readers[i]->PCD_Init();
    // Shorten the "no card" timeout from ~25 ms to ~5 ms so polling many readers stays fast
    readers[i]->PCD_WriteRegister(MFRC522::TReloadRegH, 0x00);
    readers[i]->PCD_WriteRegister(MFRC522::TReloadRegL, 0xC8);
    byte v = readers[i]->PCD_ReadRegister(MFRC522::VersionReg);
    Serial.printf("[rfid] reader %u (SS %u) version 0x%02X%s\n", (unsigned)i, RFID_SS_PINS[i], v,
                  (v == 0x00 || v == 0xFF) ? "  <-- NOT DETECTED, check wiring" : "");
  }
}

// ------------------------------------------------------------------ actuators
int laneIndex(const char* laneId) {
  for (size_t i = 0; i < N_LANES; i++) if (strcmp(LANES[i].laneId, laneId) == 0) return i;
  return -1;
}
int spaceIndex(const char* code) {
  for (size_t i = 0; i < N_SPACES; i++) if (strcmp(SPACES[i].code, code) == 0) return i;
  return -1;
}

void openBarrier(size_t i) {
  barriers[i].write(BARRIER_OPEN_ANGLE);
  lanes[i].barrierOpen = true; lanes[i].barrierOpenedAt = millis();
  Serial.printf("[barrier] %s OPEN\n", LANES[i].laneId);
}
void closeBarrier(size_t i) {
  barriers[i].write(BARRIER_CLOSED_ANGLE);
  lanes[i].barrierOpen = false;
  Serial.printf("[barrier] %s CLOSED\n", LANES[i].laneId);
}

void dispenseCard(size_t i) {
  const LaneConfig& c = LANES[i];
  if (c.dispenserPin < 0) { queueEvent("DISPENSE_FAILED", c.laneId, nullptr, "", -1, NAN, "no dispenser configured"); return; }
  if (c.dispenserEmptyPin >= 0 && digitalRead(c.dispenserEmptyPin) == LOW) {
    queueEvent("DISPENSE_FAILED", c.laneId, nullptr, "", -1, NAN, "dispenser empty");
    return;
  }
  digitalWrite(c.dispenserPin, HIGH);
  lanes[i].dispenseUntil = millis() + DISPENSER_PULSE_MS;
  Serial.printf("[dispenser] %s card issued\n", c.laneId);
}

void setLed(size_t i, const char* mode) {
  SpaceState& s = spaces[i];
  if (!strcmp(mode, "ASSIGNED")) s.led = LED_ASSIGNED;
  else if (!strcmp(mode, "WRONG")) s.led = LED_WRONG;
  else if (!strcmp(mode, "OK")) { s.led = LED_OK; s.ledUntil = millis() + 3000; }
  else s.led = LED_OFF;
}

void updateLed(size_t i, uint32_t t) {
  int8_t pin = SPACES[i].ledPin;
  if (pin < 0) return;
  SpaceState& s = spaces[i];
  if (s.led == LED_OK && t > s.ledUntil) s.led = LED_OFF;
  bool on = s.led == LED_ASSIGNED || s.led == LED_OK || (s.led == LED_WRONG && (t / 200) % 2);
  digitalWrite(pin, on ? HIGH : LOW);
}

void pollCommands() {
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.begin(String(BACKEND_URL) + "/api/gateways/" + GATEWAY_ID + "/commands");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.GET();
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      for (JsonObject cmd : doc["commands"].as<JsonArray>()) {
        const char* type = cmd["type"] | "";
        Serial.printf("[command] %s\n", type);
        if (!strcmp(type, "OPEN_BARRIER")) {
          int i = laneIndex(cmd["lane_id"] | ""); if (i >= 0) openBarrier(i);
        } else if (!strcmp(type, "DISPENSE_CARD")) {
          int i = laneIndex(cmd["lane_id"] | ""); if (i >= 0) dispenseCard(i);
        } else if (!strcmp(type, "SPACE_INDICATOR")) {
          int i = spaceIndex(cmd["space_code"] | ""); if (i >= 0) setLed(i, cmd["mode"] | "OFF");
        }
      }
    }
  }
  http.end();
}

// ------------------------------------------------------------------ lanes
void updateLane(size_t i, uint32_t t) {
  const LaneConfig& c = LANES[i];
  LaneState& s = lanes[i];

  bool raw = digitalRead(c.detectPin) == (c.detectActiveLow ? LOW : HIGH);
  if (raw != s.rawLast) { s.rawLast = raw; s.rawChangedAt = t; }
  uint32_t stable = t - s.rawChangedAt;

  if (!s.present && raw && stable >= DETECT_DEBOUNCE_MS) {          // vehicle arrives
    s.present = true; s.scanning = true; s.scanStart = t; s.lastUid = "";
    queueEvent("LANE_VEHICLE_DETECTED", c.laneId, nullptr);
  } else if (s.present && !raw && stable >= CLEAR_DEBOUNCE_MS) {     // vehicle gone (passed or reversed)
    s.present = false; s.scanning = false;
    queueEvent("LANE_VEHICLE_CLEARED", c.laneId, nullptr);
    if (s.barrierOpen) closeBarrier(i);
  }

  // While a vehicle waits, post every new identifier (vehicle tag or freshly dispensed card)
  if (s.present) {
    String uid;
    if (readUid(c.readerIndex, uid) && (uid != s.lastUid || t - s.lastUidAt > UID_REPEAT_MS)) {
      s.lastUid = uid; s.lastUidAt = t; s.scanning = false;
      queueEvent("LANE_RFID_READ", c.laneId, nullptr, uid);
    }
    if (c.cardReturnReaderIndex >= 0 && readUid(c.cardReturnReaderIndex, uid) &&
        (uid != s.lastReturnUid || t - s.lastReturnAt > UID_REPEAT_MS)) {
      s.lastReturnUid = uid; s.lastReturnAt = t;
      queueEvent("CARD_RETURNED", c.laneId, nullptr, uid);
    }
  }
  if (s.scanning && t - s.scanStart >= RFID_LANE_WINDOW_MS) {        // configured detection period elapsed
    s.scanning = false;
    queueEvent("LANE_RFID_TIMEOUT", c.laneId, nullptr);
  }

  if (s.dispenseUntil && t > s.dispenseUntil) { digitalWrite(c.dispenserPin, LOW); s.dispenseUntil = 0; }
  // never close on a vehicle: only time out when the lane is empty
  if (s.barrierOpen && !s.present && t - s.barrierOpenedAt > BARRIER_MAX_OPEN_MS) closeBarrier(i);
}

// ------------------------------------------------------------------ spaces
void updateSpace(size_t i, uint32_t t) {
  const SpaceConfig& c = SPACES[i];
  SpaceState& s = spaces[i];

  if (t - s.lastSample >= MAG_SAMPLE_MS) {
    s.lastSample = t;
    MagneticSpaceSensor::Result r = s.sensor.update();
    if (r == MagneticSpaceSensor::SENSOR_ERROR && !s.faultReported) {
      s.faultReported = true;
      queueEvent("SPACE_SENSOR_FAULT", nullptr, c.code, "", -1, NAN, "magnetometer not responding");
    } else if (r == MagneticSpaceSensor::SAMPLE_OK && s.faultReported) {
      s.faultReported = false;
      queueEvent("SPACE_SENSOR_OK", nullptr, c.code);
    }
    if (r == MagneticSpaceSensor::SAMPLE_OK && printMagnetic)
      Serial.printf("[mag] %s deviation=%.0f %s\n", c.code, s.sensor.deviation(), s.sensor.occupied() ? "CAR" : "empty");

    if (r == MagneticSpaceSensor::SAMPLE_OK && s.sensor.hasBaseline() && s.sensor.occupied() != s.occupied) {
      s.occupied = s.sensor.occupied();
      s.occupiedAt = t; s.lastPresenceCheck = t; s.lastUid = "";
      queueEvent("SPACE_OCCUPANCY", nullptr, c.code, "", s.occupied ? 1 : 0, s.sensor.deviation());
    }
  }

  // Local RFID: scan actively right after arrival, then a periodic presence check
  bool arrivalScan = s.occupied && t - s.occupiedAt < SPACE_RFID_WINDOW_MS;
  bool presenceDue = s.occupied && t - s.lastPresenceCheck >= PRESENCE_CHECK_MS;
  if (c.readerIndex >= 0 && (arrivalScan || presenceDue)) {
    String uid;
    bool got = readUid(c.readerIndex, uid);
    if (got && (uid != s.lastUid || presenceDue)) queueEvent("SPACE_RFID_READ", nullptr, c.code, uid);
    if (got) s.lastUid = uid;
    if (presenceDue) s.lastPresenceCheck = t;   // not seen: the backend watchdog decides what it means
  }
  updateLed(i, t);
}

void calibrateAll() {
  Serial.println("[mag] calibrating - all spaces must be EMPTY");
  for (size_t i = 0; i < N_SPACES; i++) {
    bool ok = spaces[i].sensor.begin() && spaces[i].sensor.calibrate();
    if (ok) spaces[i].sensor.saveBaseline(prefs);
    spaces[i].occupied = false;
    Serial.printf("[mag] %s %s\n", SPACES[i].code, ok ? "calibrated" : "FAILED");
  }
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nSmart parking gateway %s\n", GATEWAY_ID);

  initReaders();

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  prefs.begin("parking", false);
  for (size_t i = 0; i < N_SPACES; i++) {
    spaces[i].sensor.configure(TCA9548A_ADDR, SPACES[i].muxChannel, SPACES[i].code, MAG_ON_THRESHOLD,
                               MAG_OFF_THRESHOLD, MAG_DEBOUNCE_SAMPLES, MAG_BASELINE_ALPHA, MAG_FAULT_ERRORS);
    bool ok = spaces[i].sensor.begin();
    if (ok && !spaces[i].sensor.loadBaseline(prefs)) {
      // First boot: assume the space is empty. Recalibrate later with 'c' if needed.
      if (spaces[i].sensor.calibrate()) spaces[i].sensor.saveBaseline(prefs);
    }
    Serial.printf("[mag] %s sensor %s, baseline %s\n", SPACES[i].code, ok ? "OK" : "NOT FOUND",
                  spaces[i].sensor.hasBaseline() ? "loaded" : "missing");
    if (SPACES[i].ledPin >= 0) { pinMode(SPACES[i].ledPin, OUTPUT); digitalWrite(SPACES[i].ledPin, LOW); }
  }

  for (size_t i = 0; i < N_LANES; i++) {
    pinMode(LANES[i].detectPin, INPUT);   // GPIO 34/35 have no internal pull-up: use the module's output driver
    barriers[i].setPeriodHertz(50);
    barriers[i].attach(LANES[i].barrierServoPin, 500, 2400);
    barriers[i].write(BARRIER_CLOSED_ANGLE);
    if (LANES[i].dispenserPin >= 0) { pinMode(LANES[i].dispenserPin, OUTPUT); digitalWrite(LANES[i].dispenserPin, LOW); }
    if (LANES[i].dispenserEmptyPin >= 0) pinMode(LANES[i].dispenserEmptyPin, INPUT_PULLUP);
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  uint32_t t = millis();

  if (Serial.available()) {
    char ch = Serial.read();
    if (ch == 'c') calibrateAll();
    if (ch == 'm') printMagnetic = !printMagnetic;
  }

  if (WiFi.status() != WL_CONNECTED && t - lastWifiTry > 5000) {
    lastWifiTry = t;
    Serial.println("[wifi] reconnecting...");
    WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }

  for (size_t i = 0; i < N_LANES; i++) updateLane(i, t);
  for (size_t i = 0; i < N_SPACES; i++) updateSpace(i, t);

  if (WiFi.status() == WL_CONNECTED) {
    flushEvents();
    if (t - lastPoll >= COMMAND_POLL_MS) { lastPoll = t; pollCommands(); }
  }
}
