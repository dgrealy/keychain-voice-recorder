#include "recorder.h"

#include <ArduinoJson.h>
#include <ESP_I2S.h>
#include <SD.h>

#include <atomic>

#include "board.h"
#include "clock.h"
#include "generated_config.h"
#include "storage.h"
#include "wav.h"

namespace {

constexpr const char* kFwVersion = "0.1.0";
constexpr int kMicClk = 42, kMicData = 41;  // fixed on the Sense board
constexpr size_t kRingBytes = 1 << 20;      // ~32 s of audio; must be a power of two
constexpr size_t kChunk = 4096;             // bytes per mic read and per SD write
constexpr uint32_t kButtonMs = 50, kBatteryMs = 30000, kFlushMs = 10000;
constexpr const char* kReasons[] = {"button_off", "max_length", "low_battery", "sd_full"};

I2SClass i2s;
uint8_t* ring = nullptr;
// Single producer (capture task) and single consumer (drain). Positions only ever
// grow; the ring index is position & (kRingBytes - 1).
std::atomic<size_t> head{0}, tail{0};
std::atomic<bool> capturing{false}, taskDone{true};
std::atomic<uint32_t> droppedBytes{0};
time_t startedAt = 0;
int64_t startedMono = 0;

void ringWrite(size_t pos, const uint8_t* src, size_t n) {
  const size_t at = pos & (kRingBytes - 1), first = std::min(n, kRingBytes - at);
  memcpy(ring + at, src, first);
  memcpy(ring, src + first, n - first);
}

void ringRead(size_t pos, uint8_t* dst, size_t n) {
  const size_t at = pos & (kRingBytes - 1), first = std::min(n, kRingBytes - at);
  memcpy(dst, ring + at, first);
  memcpy(dst + first, ring, n - first);
}

// Mic -> DC-blocking high-pass filter -> gain -> ring buffer.
void captureTask(void*) {
  int16_t buf[kChunk / 2];
  float prevIn = 0, prevOut = 0;
  while (capturing) {
    const size_t samples = i2s.readBytes(reinterpret_cast<char*>(buf), sizeof buf) / 2;
    for (size_t i = 0; i < samples; ++i) {
      const float out = buf[i] - prevIn + 0.995f * prevOut;
      prevIn = buf[i];
      prevOut = out;
      buf[i] = static_cast<int16_t>(constrain(lroundf(out * cfg::MIC_GAIN), -32768L, 32767L));
    }
    const size_t bytes = samples * 2, h = head.load();
    if (kRingBytes - (h - tail.load()) < bytes) {
      droppedBytes += bytes;  // SD card stalled for >30 s; should never happen
      continue;
    }
    ringWrite(h, reinterpret_cast<uint8_t*>(buf), bytes);
    head.store(h + bytes);
  }
  taskDone = true;
  vTaskDelete(nullptr);
}

void stopCapture() {
  capturing = false;
  while (!taskDone) delay(5);
  i2s.end();
}

// Write everything buffered so far to `f`. Returns false if the card is full.
bool drain(File& f, uint32_t& written) {
  static uint8_t out[kChunk];
  while (const size_t avail = head.load() - tail.load()) {
    const size_t n = std::min(avail, kChunk);
    ringRead(tail.load(), out, n);
    if (f.write(out, n) != n) return false;
    tail.store(tail.load() + n);
    written += n;
  }
  return true;
}

String makeName(uint32_t seq) {
  char name[24];
  if (rtc.timeSynced) {
    strftime(name, sizeof name, "%Y%m%d-%H%M%SZ", gmtime(&startedAt));
  } else {
    snprintf(name, sizeof name, "rec_%06lu", static_cast<unsigned long>(seq));
  }
  return name;
}

void initialMeta(JsonDocument& meta, uint32_t seq, float batteryVolts) {
  meta["schema"] = 1;
  meta["device_id"] = cfg::DEVICE_ID;
  meta["fw_version"] = kFwVersion;
  meta["seq"] = seq;
  meta["boot_id"] = clk::bootIdHex();
  meta["time_synced"] = rtc.timeSynced;
  meta["started_at"] = static_cast<int64_t>(startedAt);
  meta["started_mono"] = startedMono;
  meta["duration_s"] = 0;
  meta["sample_rate"] = kSampleRate;
  meta["bits_per_sample"] = kBitsPerSample;
  meta["channels"] = 1;
  meta["stop_reason"] = "power_loss";  // replaced on a clean stop
  if (isnan(batteryVolts)) {
    meta["battery_v"] = nullptr;
  } else {
    meta["battery_v"] = roundf(batteryVolts * 100) / 100;
  }
  meta["dropped_ms"] = 0;
}

}  // namespace

namespace recorder {

bool startCapture() {
  if (!ring) ring = static_cast<uint8_t*>(ps_malloc(kRingBytes));
  if (!ring) return false;
  head = 0;
  tail = 0;
  droppedBytes = 0;
  startedAt = time(nullptr);
  startedMono = clk::mono();
  i2s.setPinsPdmRx(kMicClk, kMicData);
  if (!i2s.begin(I2S_MODE_PDM_RX, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) return false;
  capturing = true;
  taskDone = false;
  xTaskCreatePinnedToCore(captureTask, "capture", 4096 + kChunk, nullptr, 5, nullptr, 0);
  return true;
}

void abortCapture() { stopCapture(); }

Recording record(float batteryVolts) {
  const uint32_t seq = storage::nextSeq();
  const String name = makeName(seq);
  const String wavPath = storage::path(storage::kRecording, name, ".wav");
  const String jsonPath = storage::path(storage::kRecording, name, ".json");

  // The JSON goes down first, so a power loss mid-recording can be repaired on the next boot.
  JsonDocument meta;
  initialMeta(meta, seq, batteryVolts);
  File f;
  if (storage::writeJson(jsonPath, meta)) f = SD.open(wavPath, FILE_WRITE);
  if (!f) {
    stopCapture();
    SD.remove(jsonPath);
    return {false, StopReason::SdFull, 0};
  }
  uint8_t header[44];
  wavHeader(header, 0);
  f.write(header, sizeof header);

  const uint32_t maxBytes = cfg::MAX_RECORDING_MIN * 60 * kBytesPerSecond;
  uint32_t written = 0, lastButton = 0, lastBattery = millis(), lastFlush = millis();
  StopReason reason = StopReason::ButtonOff;
  for (;;) {
    if (!drain(f, written)) { reason = StopReason::SdFull; break; }
    if (written >= maxBytes) { reason = StopReason::MaxLength; break; }
    const uint32_t now = millis();
    if (now - lastButton >= kButtonMs) {
      lastButton = now;
      if (!board::buttonOn()) break;
    }
    if (now - lastBattery >= kBatteryMs) {
      lastBattery = now;
      if (board::batteryVolts() < cfg::CRITICAL_BATTERY_V) { reason = StopReason::LowBattery; break; }
    }
    if (now - lastFlush >= kFlushMs) {
      lastFlush = now;
      f.flush();
    }
    delay(20);
  }

  stopCapture();
  if (reason != StopReason::SdFull) drain(f, written);  // audio captured since the last pass
  wavHeader(header, written);
  f.seek(0);
  f.write(header, sizeof header);
  f.close();

  const float seconds = static_cast<float>(written) / kBytesPerSecond;
  if (seconds < cfg::MIN_RECORDING_S) {  // accidental press
    SD.remove(wavPath);
    SD.remove(jsonPath);
    return {false, reason, seconds};
  }
  meta["duration_s"] = roundf(seconds * 100) / 100;
  meta["stop_reason"] = kReasons[static_cast<int>(reason)];
  meta["dropped_ms"] = droppedBytes.load() * 1000 / kBytesPerSecond;
  storage::writeJson(jsonPath, meta);
  storage::finishRecording(name);
  return {true, reason, seconds};
}

}  // namespace recorder
