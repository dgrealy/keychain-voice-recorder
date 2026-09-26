// Records the onboard PDM microphone to a WAV on the SD card.
//
// A FreeRTOS task reads the mic into a 1 MB PSRAM ring buffer from the moment the
// button is pressed, so no audio is lost while the SD card mounts; the main loop
// then drains the buffer to /recording/<name>.wav.
#pragma once

#include <Arduino.h>

enum class StopReason { ButtonOff, MaxLength, LowBattery, SdFull };

struct Recording {
  bool saved;       // false if discarded as too short, or if the file could not be created
  StopReason reason;
  float seconds;
};

namespace recorder {

bool startCapture();                   // start the mic now; returns false if PSRAM/I2S fail
void abortCapture();                   // stop the mic and drop what was buffered
Recording record(float batteryVolts);  // write audio to SD until a stop condition (needs mounted SD)

}  // namespace recorder
