// 44-byte PCM WAV header (little-endian, as the ESP32 is).
#pragma once

#include <stdint.h>
#include <string.h>

constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kBitsPerSample = 16;
constexpr uint32_t kBytesPerSecond = kSampleRate * kBitsPerSample / 8;  // mono

inline void wavHeader(uint8_t h[44], uint32_t dataBytes) {
  auto u32 = [&](int at, uint32_t v) { memcpy(h + at, &v, 4); };
  auto u16 = [&](int at, uint16_t v) { memcpy(h + at, &v, 2); };
  memcpy(h, "RIFF", 4);
  u32(4, 36 + dataBytes);
  memcpy(h + 8, "WAVEfmt ", 8);
  u32(16, 16);                   // fmt chunk size
  u16(20, 1);                    // PCM
  u16(22, 1);                    // mono
  u32(24, kSampleRate);
  u32(28, kBytesPerSecond);
  u16(32, kBitsPerSample / 8);   // block align
  u16(34, kBitsPerSample);
  memcpy(h + 36, "data", 4);
  u32(40, dataBytes);
}
