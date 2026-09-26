#include "clock.h"

#include <esp_random.h>
#include <esp_sntp.h>
#include <esp_timer.h>
#include <sys/time.h>

RTC_DATA_ATTR RtcState rtc;

namespace {
constexpr uint32_t kMagic = 0x564E4B31;  // "VNK1"
volatile bool ntpDone = false;

int64_t wallUs() {
  timeval tv;
  gettimeofday(&tv, nullptr);
  return static_cast<int64_t>(tv.tv_sec) * 1000000 + tv.tv_usec;
}
}  // namespace

namespace clk {

void begin() {
  if (rtc.magic == kMagic) return;  // woke from deep sleep: state is still valid
  rtc = RtcState{};
  rtc.magic = kMagic;
  rtc.bootId = esp_random();
}

int64_t mono() { return (wallUs() - rtc.monoOffsetUs) / 1000000; }

String bootIdHex() {
  char hex[9];
  snprintf(hex, sizeof hex, "%08lx", static_cast<unsigned long>(rtc.bootId));
  return hex;
}

bool syncNtp(uint32_t timeoutMs) {
  ntpDone = false;
  sntp_set_time_sync_notification_cb([](timeval*) { ntpDone = true; });
  const int64_t wallBefore = wallUs(), t0 = esp_timer_get_time();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  while (!ntpDone && esp_timer_get_time() - t0 < timeoutMs * 1000LL) delay(50);
  esp_sntp_stop();
  if (!ntpDone) return false;
  // Whatever the wall clock moved beyond real elapsed time is the NTP correction;
  // add it to the offset so mono() does not jump.
  rtc.monoOffsetUs += (wallUs() - wallBefore) - (esp_timer_get_time() - t0);
  rtc.timeSynced = true;
  return true;
}

}  // namespace clk
