// SD card layout (docs/PROTOCOL.md section 1): /recording -> /pending -> /sent.
// A note is a <name>.wav + <name>.json pair; the JSON is always moved last, so a
// note counts as present in a folder once its JSON is there.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include <vector>

namespace storage {

constexpr const char* kRecording = "/recording";
constexpr const char* kPending = "/pending";
constexpr const char* kSent = "/sent";

bool mount();                       // idempotent; creates the folders
bool mounted();
String path(const char* dir, const String& name, const char* ext);
uint32_t nextSeq();                 // persistent counter in flash (NVS)

std::vector<String> pending();      // names waiting to be sent, oldest first
void finishRecording(const String& name);  // /recording -> /pending
void markSent(const String& name);         // /pending -> /sent
void recoverInterrupted();          // repair notes cut off by a power loss
void pruneSent(uint64_t minFreeBytes);     // delete the oldest sent notes if space is low

bool readJson(const String& path, JsonDocument& doc);
bool writeJson(const String& path, const JsonDocument& doc);

}  // namespace storage
