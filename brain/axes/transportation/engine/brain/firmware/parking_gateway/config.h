#pragma once
// =====================================================================
//  Smart parking ESP32 gateway - configuration for ONE parking lot node
// =====================================================================
#include <Arduino.h>

// ---------------- Network / backend
#define WIFI_SSID       "YOUR_WIFI"
#define WIFI_PASSWORD   "YOUR_PASSWORD"
#define BACKEND_URL     "http://192.168.1.50:8000"   // PC running uvicorn (use its LAN IP, not localhost)
#define GATEWAY_ID      "GW-P01"
#define DEVICE_KEY      "dev-device-key"              // must match PARKING_DEVICE_KEY on the backend

// ---------------- Buses
// SPI shared by every RC522 reader : SCK 18, MISO 19, MOSI 23 (RST of all readers tied to 3.3V)
// I2C : SDA 21, SCL 22  ->  TCA9548A multiplexer (0x70)  ->  one QMC5883L magnetometer per channel
#define I2C_SDA 21
#define I2C_SCL 22
#define TCA9548A_ADDR 0x70

// ---------------- RFID readers (RC522, 13.56 MHz) - chip-select pin per reader
// index:                        0=entrance 1=exit 2=card-return 3=E1 4=E2 5=E3
static const uint8_t RFID_SS_PINS[] = { 5,        17,    16,           4,   13,  14 };

// ---------------- Entrance / exit lanes
struct LaneConfig {
  const char* laneId;        // must match the backend lane id
  bool        isEntrance;
  uint8_t     detectPin;     // inductive-loop detector output or IR beam sensor
  bool        detectActiveLow;
  uint8_t     barrierServoPin;
  int8_t      readerIndex;   // lane RFID reader
  int8_t      cardReturnReaderIndex;  // exit only: reader inside the card-return unit (-1 = none)
  int8_t      dispenserPin;  // entrance only: relay / motor driver of the card dispenser (-1 = none)
  int8_t      dispenserEmptyPin; // entrance only: LOW = dispenser empty (-1 = no sensor)
};

static const LaneConfig LANES[] = {
  { "P01-ENT1",  true,  34, true, 25, 0, -1, 27, -1 },
  { "P01-EXIT1", false, 35, true, 26, 1,  2, -1, -1 },
};

// ---------------- Parking spaces: magnetic sensor + local RFID reader + LED
struct SpaceConfig {
  const char* code;          // must match the backend space code
  uint8_t     muxChannel;    // TCA9548A channel of its QMC5883L
  int8_t      readerIndex;   // local RFID reader
  int8_t      ledPin;        // guidance LED (-1 = none)
};

static const SpaceConfig SPACES[] = {
  { "E1", 0, 3, 32 },
  { "E2", 1, 4, 33 },
  { "E3", 2, 5, 15 },
};

// ---------------- Magnetic detection tuning (QMC5883L at +-2 G: 12000 counts per gauss)
// Print the live deviation with the serial command 'm' and tune these for your site.
#define MAG_SAMPLE_MS          100     // sampling period per sensor
#define MAG_ON_THRESHOLD       1200    // |B - baseline| above this -> vehicle (~100 mG)
#define MAG_OFF_THRESHOLD      600     // below this -> empty (hysteresis, ~50 mG)
#define MAG_DEBOUNCE_SAMPLES   5       // consecutive samples needed to change state
#define MAG_BASELINE_ALPHA     0.002f  // slow drift compensation while empty
#define MAG_FAULT_ERRORS       10      // consecutive I2C errors -> sensor fault

// ---------------- Timings
#define DETECT_DEBOUNCE_MS     150     // lane sensor must be active this long
#define CLEAR_DEBOUNCE_MS      800     // lane sensor must be inactive this long
#define RFID_LANE_WINDOW_MS    3000    // configured detection period for the lane tag
#define UID_REPEAT_MS          10000   // re-post the same UID after this delay only
#define SPACE_RFID_WINDOW_MS   20000   // scan the space reader this long after a vehicle arrives
#define PRESENCE_CHECK_MS      60000   // periodic tag presence check of a parked vehicle
#define COMMAND_POLL_MS        300
#define BARRIER_MAX_OPEN_MS    60000   // close an open barrier if no vehicle is present
#define DISPENSER_PULSE_MS     400
#define BARRIER_OPEN_ANGLE     90
#define BARRIER_CLOSED_ANGLE   0
#define HTTP_TIMEOUT_MS        2000
