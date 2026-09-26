#include "storage.h"

#include <Preferences.h>
#include <SD.h>
#include <SPI.h>

#include <algorithm>

#include "clock.h"
#include "generated_config.h"
#include "wav.h"

namespace storage {
namespace {

bool isMounted = false;

// Note names in `dir` that have a file with extension `ext`, sorted.
// Skips macOS "._" metadata files created when the card is browsed in Finder.
std::vector<String> names(const char* dir, const char* ext) {
  std::vector<String> out;
  File d = SD.open(dir);
  if (!d) return out;
  for (File f = d.openNextFile(); f; f = d.openNextFile()) {
    String n = f.name();
    if (!f.isDirectory() && !n.startsWith(".") && n.endsWith(ext)) out.push_back(n.substring(0, n.length() - strlen(ext)));
  }
  std::sort(out.begin(), out.end());
  return out;
}

void move(const String& from, const String& to) {
  if (!SD.exists(from)) return;
  SD.remove(to);
  SD.rename(from, to);
}

void movePair(const char* from, const char* to, const String& name) {
  move(path(from, name, ".wav"), path(to, name, ".wav"));
  move(path(from, name, ".json"), path(to, name, ".json"));  // last: marks the move complete
}

}  // namespace

bool mount() {
  if (isMounted) return true;
  isMounted = SD.begin(cfg::PIN_SD_CS);  // default SPI pins are the Sense SD slot
  if (isMounted) {
    for (const char* dir : {kRecording, kPending, kSent}) {
      if (!SD.exists(dir)) SD.mkdir(dir);
    }
  }
  return isMounted;
}

bool mounted() { return isMounted; }

String path(const char* dir, const String& name, const char* ext) { return String(dir) + "/" + name + ext; }

uint32_t nextSeq() {
  Preferences prefs;
  prefs.begin("voicenote");
  const uint32_t seq = prefs.getUInt("seq", 0) + 1;
  prefs.putUInt("seq", seq);
  prefs.end();
  return seq;
}

std::vector<String> pending() { return names(kPending, ".json"); }

void finishRecording(const String& name) { movePair(kRecording, kPending, name); }

void markSent(const String& name) {
  // JSON first here: if power fails in between, the note is no longer pending
  // (it has already been delivered) and the stray WAV is simply ignored.
  move(path(kPending, name, ".json"), path(kSent, name, ".json"));
  move(path(kPending, name, ".wav"), path(kSent, name, ".wav"));
}

void recoverInterrupted() {
  for (const String& name : names(kRecording, ".json")) {
    const String wavPath = path(kRecording, name, ".wav"), jsonPath = path(kRecording, name, ".json");
    File wav = SD.open(wavPath, "r+");
    if (!wav) {  // power failed before any audio was written
      SD.remove(jsonPath);
      continue;
    }
    // The header was written with a zero length; fix it from the file size.
    const uint32_t dataBytes = wav.size() > 44 ? (wav.size() - 44) & ~1u : 0;
    uint8_t header[44];
    wavHeader(header, dataBytes);
    wav.seek(0);
    wav.write(header, sizeof header);
    wav.close();

    JsonDocument meta;
    readJson(jsonPath, meta);  // written at start with stop_reason "power_loss"
    meta["duration_s"] = roundf(dataBytes * 100.0f / kBytesPerSecond) / 100;
    writeJson(jsonPath, meta);
    finishRecording(name);
    Serial.printf("Recovered interrupted recording %s\n", name.c_str());
  }
}

void pruneSent(uint64_t minFreeBytes) {
  uint64_t freeBytes = SD.totalBytes() - SD.usedBytes();  // slow on big cards: compute once
  for (const String& name : names(kSent, ".json")) {
    if (freeBytes >= minFreeBytes) return;
    File wav = SD.open(path(kSent, name, ".wav"));
    if (wav) {
      freeBytes += wav.size();
      wav.close();
    }
    SD.remove(path(kSent, name, ".wav"));
    SD.remove(path(kSent, name, ".json"));
  }
}

bool readJson(const String& path, JsonDocument& doc) {
  File f = SD.open(path);
  if (!f) return false;
  const bool ok = !deserializeJson(doc, f);
  f.close();
  return ok;
}

bool writeJson(const String& path, const JsonDocument& doc) {
  File f = SD.open(path, FILE_WRITE);
  if (!f) return false;
  const bool ok = serializeJson(doc, f) > 0;
  f.close();
  return ok;
}

}  // namespace storage
