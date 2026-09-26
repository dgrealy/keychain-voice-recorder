#include "uploader.h"

#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <SD.h>
#include <WiFi.h>
#include <mbedtls/sha256.h>

#include "clock.h"
#include "generated_config.h"
#include "storage.h"

namespace {

constexpr uint32_t kWifiTimeoutMs = 12000, kNtpTimeoutMs = 5000;

bool connectWifi(bool (*shouldAbort)()) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(cfg::WIFI_SSID, cfg::WIFI_PASSWORD);
  for (uint32_t t0 = millis(); millis() - t0 < kWifiTimeoutMs; delay(100)) {
    if (WiFi.status() == WL_CONNECTED) return true;
    if (shouldAbort()) return false;
  }
  return false;
}

bool healthy(const IPAddress& ip) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(2000);
  if (!http.begin(client, ip.toString(), cfg::RECEIVER_PORT, "/health")) return false;
  const bool ok = http.GET() == 200;
  http.end();
  return ok;
}

// Last address that worked, then the Mac's Bonjour name, then the fixed fallback IP.
IPAddress findReceiver() {
  IPAddress ip(rtc.receiverIp);
  if (rtc.receiverIp && healthy(ip)) return ip;
  ip = IPAddress();
  if (strlen(cfg::MAC_HOSTNAME) && MDNS.begin("voicenote-keychain")) {
    ip = MDNS.queryHost(cfg::MAC_HOSTNAME, 3000);
    MDNS.end();
  }
  if (!static_cast<uint32_t>(ip) || !healthy(ip)) {
    if (!ip.fromString(cfg::FALLBACK_IP) || !healthy(ip)) return IPAddress();
  }
  rtc.receiverIp = static_cast<uint32_t>(ip);
  return ip;
}

String sha256Hex(File& f) {
  static uint8_t buf[4096];
  uint8_t digest[32];
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);
  while (const size_t n = f.read(buf, sizeof buf)) mbedtls_sha256_update(&ctx, buf, n);
  mbedtls_sha256_finish(&ctx, digest);
  mbedtls_sha256_free(&ctx);
  f.seek(0);
  char hex[65];
  for (int i = 0; i < 32; ++i) snprintf(hex + 2 * i, 3, "%02x", digest[i]);
  return hex;
}

bool upload(const IPAddress& ip, const String& name) {
  File json = SD.open(storage::path(storage::kPending, name, ".json"));
  File wav = SD.open(storage::path(storage::kPending, name, ".wav"));
  if (!json || !wav) return false;
  String meta = json.readString();
  json.close();
  meta.trim();

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(20000);
  if (!http.begin(client, ip.toString(), cfg::RECEIVER_PORT, "/upload")) return false;
  http.addHeader("Authorization", String("Bearer ") + cfg::TOKEN);
  http.addHeader("Content-Type", "audio/wav");
  http.addHeader("X-VN-Protocol", "1");
  http.addHeader("X-VN-Filename", name + ".wav");
  http.addHeader("X-VN-SHA256", sha256Hex(wav));
  http.addHeader("X-VN-Meta", meta);
  http.addHeader("X-VN-Boot-Id", clk::bootIdHex());
  http.addHeader("X-VN-Device-Mono", String(static_cast<long long>(clk::mono())));
  http.addHeader("X-VN-Device-Time", String(static_cast<long long>(time(nullptr))));
  const int code = http.sendRequest("POST", &wav, wav.size());
  http.end();
  wav.close();
  Serial.printf("Upload %s -> %d\n", name.c_str(), code);
  return code == 200;
}

}  // namespace

namespace uploader {

Result syncPending(bool (*shouldAbort)()) {
  const std::vector<String> names = storage::pending();
  if (names.empty()) return Result::Done;

  Result result = Result::Done;
  if (!connectWifi(shouldAbort)) {
    result = shouldAbort() ? Result::Aborted : Result::NoWifi;
  } else {
    clk::syncNtp(kNtpTimeoutMs);
    const IPAddress ip = findReceiver();
    if (!static_cast<uint32_t>(ip)) result = Result::NoReceiver;
    for (size_t i = 0; result == Result::Done && i < names.size(); ++i) {
      if (shouldAbort()) {
        result = Result::Aborted;
      } else if (upload(ip, names[i])) {
        storage::markSent(names[i]);
      } else {
        result = Result::Failed;
        rtc.receiverIp = 0;  // look it up again next time
      }
    }
  }
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  return result;
}

const char* describe(Result r) {
  static const char* names[] = {"done", "no Wi-Fi", "receiver not found", "upload failed", "aborted"};
  return names[static_cast<int>(r)];
}

}  // namespace uploader
