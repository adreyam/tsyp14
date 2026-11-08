#pragma once
// Vehicle detection with a QMC5883L 3-axis magnetometer placed on / in the ground
// at the centre of the parking space. A car's steel body distorts the Earth's
// magnetic field; we measure |B - B_empty| and apply hysteresis + debounce.
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>

class MagneticSpaceSensor {
 public:
  enum Result { SAMPLE_OK, NOT_READY, SENSOR_ERROR };

  static constexpr uint8_t QMC_ADDR = 0x0D;

  void configure(uint8_t muxAddr, uint8_t channel, const char* code,
                 int onThreshold, int offThreshold, uint8_t debounce, float alpha, uint8_t faultErrors) {
    _mux = muxAddr; _ch = channel; _code = code;
    _on = onThreshold; _off = offThreshold; _debounce = debounce; _alpha = alpha; _faultErrors = faultErrors;
  }

  // Configure the chip: continuous mode, 50 Hz, +-2 G, OSR 512
  bool begin() {
    if (!selectChannel()) return false;
    if (!writeReg(0x0A, 0x80)) return false;   // soft reset
    delay(10);
    if (!writeReg(0x0B, 0x01)) return false;   // SET/RESET period (datasheet recommendation)
    if (!writeReg(0x09, 0x05)) return false;   // OSR=512 | RNG=2G | ODR=50Hz | MODE=continuous
    _ready = true;
    return true;
  }

  // Average N samples as the empty-space reference. The space MUST be empty.
  bool calibrate(uint16_t samples = 64) {
    double sx = 0, sy = 0, sz = 0; uint16_t got = 0; uint32_t start = millis();
    while (got < samples && millis() - start < 5000) {
      int16_t x, y, z;
      if (readRaw(x, y, z) == SAMPLE_OK) { sx += x; sy += y; sz += z; got++; }
      delay(20);
    }
    if (got < samples / 2) return false;
    _b[0] = sx / got; _b[1] = sy / got; _b[2] = sz / got;
    _hasBaseline = true; _occupied = false; _count = 0;
    return true;
  }

  bool loadBaseline(Preferences& prefs) {
    if (prefs.getBytesLength(key()) != sizeof(_b)) return false;
    prefs.getBytes(key(), _b, sizeof(_b));
    _hasBaseline = true;
    return true;
  }
  void saveBaseline(Preferences& prefs) { prefs.putBytes(key(), _b, sizeof(_b)); }

  // Call every MAG_SAMPLE_MS. Returns SENSOR_ERROR only after several consecutive failures.
  Result update() {
    if (!_ready && !begin()) return fail();
    int16_t x, y, z;
    Result r = readRaw(x, y, z);
    if (r == NOT_READY) return _errors >= _faultErrors ? SENSOR_ERROR : NOT_READY;
    if (r == SENSOR_ERROR) { _ready = false; return fail(); }
    _errors = 0;
    if (!_hasBaseline) return SAMPLE_OK;

    float dx = x - _b[0], dy = y - _b[1], dz = z - _b[2];
    _delta = _overflow ? 32767.0f : sqrtf(dx * dx + dy * dy + dz * dz);

    if (!_occupied) {
      _count = (_delta > _on) ? _count + 1 : 0;
      if (_count >= _debounce) { _occupied = true; _count = 0; }
      else if (_delta < _off / 2) {            // empty and quiet: follow slow drift (temperature...)
        _b[0] += (x - _b[0]) * _alpha; _b[1] += (y - _b[1]) * _alpha; _b[2] += (z - _b[2]) * _alpha;
      }
    } else {
      _count = (_delta < _off) ? _count + 1 : 0;
      if (_count >= _debounce) { _occupied = false; _count = 0; }
    }
    return SAMPLE_OK;
  }

  bool occupied() const { return _occupied; }
  float deviation() const { return _delta; }
  bool hasBaseline() const { return _hasBaseline; }
  const char* code() const { return _code; }

 private:
  uint8_t _mux = 0x70, _ch = 0, _debounce = 5, _faultErrors = 10;
  int _on = 1200, _off = 600;
  float _alpha = 0.002f, _b[3] = {0, 0, 0}, _delta = 0;
  const char* _code = "";
  bool _occupied = false, _hasBaseline = false, _ready = false, _overflow = false;
  uint8_t _count = 0, _errors = 0;
  char _key[12];

  const char* key() { snprintf(_key, sizeof(_key), "mag_%s", _code); return _key; }

  Result fail() { if (_errors < 255) _errors++; return _errors >= _faultErrors ? SENSOR_ERROR : NOT_READY; }

  bool selectChannel() {
    Wire.beginTransmission(_mux);
    Wire.write(1 << _ch);
    return Wire.endTransmission() == 0;
  }

  bool writeReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(QMC_ADDR);
    Wire.write(reg); Wire.write(val);
    return Wire.endTransmission() == 0;
  }

  Result readRaw(int16_t& x, int16_t& y, int16_t& z) {
    if (!selectChannel()) return SENSOR_ERROR;
    Wire.beginTransmission(QMC_ADDR);
    Wire.write(0x06);                                   // status register
    if (Wire.endTransmission(false) != 0) return SENSOR_ERROR;
    if (Wire.requestFrom((int)QMC_ADDR, 1) != 1) return SENSOR_ERROR;
    uint8_t status = Wire.read();
    if (!(status & 0x01)) return NOT_READY;            // DRDY
    _overflow = status & 0x02;                          // OVL: field beyond range -> treat as vehicle

    Wire.beginTransmission(QMC_ADDR);
    Wire.write(0x00);
    if (Wire.endTransmission(false) != 0) return SENSOR_ERROR;
    if (Wire.requestFrom((int)QMC_ADDR, 6) != 6) return SENSOR_ERROR;
    uint8_t b[6];
    for (int i = 0; i < 6; i++) b[i] = Wire.read();
    x = (int16_t)(b[0] | (b[1] << 8));
    y = (int16_t)(b[2] | (b[3] << 8));
    z = (int16_t)(b[4] | (b[5] << 8));
    return SAMPLE_OK;
  }
};
