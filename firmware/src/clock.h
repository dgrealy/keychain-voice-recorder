// Timekeeping without a battery-backed clock (docs/PROTOCOL.md section 3).
//
// The wall clock keeps running through deep sleep but starts from zero after a power loss,
// so it is only trusted once NTP has set it. mono() counts seconds since power-on and never
// jumps; together with the boot id, it lets the Mac date recordings made before a sync.
#pragma once

#include <Arduino.h>
#include <stdint.h>

// State kept in RTC memory: survives deep sleep, resets on power loss.
struct RtcState {
  uint32_t magic;          // set once this power-on's state is initialised
  uint32_t bootId;         // random per power-on
  bool timeSynced;         // wall clock set by NTP since power-on
  int64_t monoOffsetUs;    // wall clock minus monotonic clock
  bool waitForRelease;     // a safety stop ended recording while the button was still latched
  uint32_t receiverIp;     // last receiver address that worked (0 = none)
  uint8_t failedSyncs;     // consecutive failed syncs; stretches the retry interval
};
extern RtcState rtc;

namespace clk {

void begin();                      // call first after every wake
int64_t mono();                    // seconds since power-on
String bootIdHex();                // 8 lowercase hex characters
bool syncNtp(uint32_t timeoutMs);  // needs Wi-Fi; keeps mono() continuous

}  // namespace clk
