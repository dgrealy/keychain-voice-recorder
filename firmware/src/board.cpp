#include "board.h"

#include <Arduino.h>
#include <driver/rtc_io.h>

#include "generated_config.h"

namespace board {

void init() {
  rtc_gpio_deinit(static_cast<gpio_num_t>(cfg::PIN_BUTTON));  // undo the deep-sleep wake setup
  pinMode(cfg::PIN_BUTTON, INPUT_PULLUP);                      // button shorts the pin to GND
  pinMode(cfg::PIN_LED, OUTPUT);
  led(false);
}

bool buttonOn() {
  // The level must hold for 30 ms to count, which filters contact bounce.
  bool level = digitalRead(cfg::PIN_BUTTON) == LOW;
  for (uint32_t since = millis(); millis() - since < 30;) {
    bool now = digitalRead(cfg::PIN_BUTTON) == LOW;
    if (now != level) {
      level = now;
      since = millis();
    }
  }
  return level;
}

void led(bool on) { digitalWrite(cfg::PIN_LED, on ? HIGH : LOW); }

void blink(int times, int ms) {
  for (int i = 0; i < times; ++i) {
    led(true);
    delay(ms);
    led(false);
    delay(ms);
  }
}

float batteryVolts() {
  if (cfg::PIN_BATTERY < 0) return NAN;
  uint32_t mv = 0;
  for (int i = 0; i < 16; ++i) mv += analogReadMilliVolts(cfg::PIN_BATTERY);
  return mv / 16.0f / 1000.0f * cfg::BATTERY_DIVIDER;
}

}  // namespace board
