// VoiceNote keychain: records while the latching button is down, sends notes to the
// Mac over Wi-Fi, and spends the rest of its life in deep sleep.
//
// Every wake runs setup() from the top:
//   button latched -> record until released (or a safety stop)
//   notes pending  -> try to send them
//   then sleep: wake on the button, plus a timer only while notes are still pending.
#include <Arduino.h>
#include <SD.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>

#include <algorithm>

#include "board.h"
#include "clock.h"
#include "generated_config.h"
#include "recorder.h"
#include "storage.h"
#include "uploader.h"

namespace {

constexpr uint64_t kMinFreeBytes = 200ULL * 1024 * 1024;  // keep sent notes until space drops below this
constexpr int kErrorBlinks = 10, kErrorBlinkMs = 50;
constexpr uint8_t kMaxBackoffShift = 3;  // retry interval grows up to 8x while away from home Wi-Fi

bool recordingWanted() { return !rtc.waitForRelease && board::buttonOn(); }

// Blink an error and ignore the button until it is released, so we don't wake-loop.
void refuseRecording(const char* why) {
  Serial.printf("Not recording: %s\n", why);
  board::blink(kErrorBlinks, kErrorBlinkMs);
  rtc.waitForRelease = true;
}

void runRecording() {
  board::led(true);
  const float volts = board::batteryVolts();
  if (volts < cfg::CRITICAL_BATTERY_V) return refuseRecording("battery critical");
  if (!recorder::startCapture()) return refuseRecording("microphone/PSRAM failed");
  if (!storage::mount()) {
    recorder::abortCapture();
    return refuseRecording("no SD card");
  }
  storage::recoverInterrupted();
  if (volts < cfg::LOW_BATTERY_V) {
    board::blink(3, 100);  // low-battery warning; the mic keeps capturing meanwhile
    board::led(true);
  }

  const Recording r = recorder::record(volts);
  board::led(false);
  Serial.printf("Recording %s: %.1f s\n", r.saved ? "saved" : "discarded", r.seconds);
  if (r.saved) {
    delay(200);
    board::blink(2, 150);
  } else if (r.reason == StopReason::SdFull) {
    board::blink(kErrorBlinks, kErrorBlinkMs);
  }
  if (r.reason != StopReason::ButtonOff) rtc.waitForRelease = board::buttonOn();
}

[[noreturn]] void sleepNow() {
  const bool pending = storage::mounted() && !storage::pending().empty();
  const auto button = static_cast<gpio_num_t>(cfg::PIN_BUTTON);
  board::led(false);
  SD.end();
  // Wake when the button is pressed (pin pulled low), or, after a safety stop, when it is released.
  esp_sleep_enable_ext0_wakeup(button, rtc.waitForRelease ? 1 : 0);
  rtc_gpio_pullup_en(button);
  rtc_gpio_pulldown_dis(button);
  if (pending) {
    const uint64_t minutes = uint64_t{cfg::SYNC_INTERVAL_MIN} << std::min(rtc.failedSyncs, kMaxBackoffShift);
    esp_sleep_enable_timer_wakeup(minutes * 60ULL * 1000000ULL);
  }
  Serial.printf("Sleeping until %s\n", pending ? "button or sync timer" : "button");
  Serial.flush();
  esp_deep_sleep_start();
}

}  // namespace

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT && ARDUINO_USB_MODE
  Serial.setTxTimeoutMs(0);  // never block on logging when no computer is attached
#endif
  clk::begin();
  board::init();
  if (!board::buttonOn()) rtc.waitForRelease = false;

  for (;;) {
    if (recordingWanted()) {
      runRecording();
      continue;  // the button may already be down again
    }
    if (!storage::mount()) break;
    storage::recoverInterrupted();
    if (storage::pending().empty()) break;
    if (board::batteryVolts() < cfg::CRITICAL_BATTERY_V) break;  // Wi-Fi could brown out

    const uploader::Result result = uploader::syncPending(recordingWanted);
    Serial.printf("Sync: %s\n", uploader::describe(result));
    if (result == uploader::Result::Aborted) continue;  // button pressed mid-sync: go record
    if (result == uploader::Result::Done) {
      rtc.failedSyncs = 0;
      storage::pruneSent(kMinFreeBytes);
    } else if (rtc.failedSyncs < 255) {
      ++rtc.failedSyncs;
    }
    break;
  }
  sleepNow();
}

void loop() {}  // never reached: setup() always ends in deep sleep
