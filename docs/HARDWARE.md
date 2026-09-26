# Hardware

## Parts

| Part | Notes |
|------|-------|
| Seeed XIAO ESP32S3 **Sense** | The Sense expansion board has the PDM microphone and the microSD slot |
| SanDisk 32 GB microSD | SDHC, comes formatted FAT32 (keep it that way; in Disk Utility choose "MS-DOS (FAT)") |
| Small 3.7 V LiPo | Soldered to the BAT+ / BAT− pads under the XIAO; charges over USB-C |
| Self-locking (latching) push button | Press = locks down = recording; press again = released = stop |
| LED + 470 Ω–1 kΩ resistor | Recording indicator. The onboard LED can't be used: it shares GPIO21 with the SD card |
| 2 × 220 kΩ resistors | Battery voltage divider (optional; set `pins.battery_adc: -1` without it) |

## Wiring

| Function | XIAO pin | GPIO | Connection |
|----------|----------|------|------------|
| Button   | D1 | 2 | one button contact to D1, the other to GND (internal pull-up) |
| LED      | D3 | 4 | D3 → resistor → LED anode; LED cathode → GND |
| Battery  | D0 | 1 | BAT+ → 220 kΩ → **D0** → 220 kΩ → GND |
| Mic      | —  | 41/42 | on the Sense board |
| SD card  | D8–D10 | 7/8/9, CS 21 | on the Sense board (D8–D10 can't be used for anything else) |

All three of D0, D1 and D3 are RTC-capable pins, which the button needs so it can wake the chip
from deep sleep. Pins can be changed in `config.yaml` → `device.pins`.

The divider halves the battery voltage (4.2 V max → 2.1 V) so the ADC can read it.
At 440 kΩ total it draws about 10 µA, all the time.

## What the LED means

| Pattern | Meaning |
|---------|---------|
| Solid | Recording. Wait for it to light before speaking (≈0.3 s after pressing, while the chip wakes) |
| 3 quick blinks, then solid | Recording, but the battery is low: charge soon |
| 2 blinks after release | Note saved |
| No blinks after release | Too short (under `min_recording_seconds`); discarded |
| 10 fast blinks | Can't record: no SD card, SD card full, or battery critical |

## Power

The chip spends almost all its time in deep sleep:

- **Nothing waiting to send:** it sleeps until the button is pressed. No timer runs.
- **Notes waiting:** it also wakes every `sync_interval_minutes` to try Wi-Fi. Each attempt is a
  few seconds at roughly 100 mA. While home Wi-Fi keeps being unreachable, the interval doubles
  up to 8× (30 min → 4 h), and it resets after a successful sync.
- **Recording** draws tens of mA. The SD card and mic stay powered by the Sense board even in
  deep sleep, so measure the real sleep current with a USB power meter or multimeter once built.

## Clock

There's no battery-backed clock. The ESP32's internal clock keeps counting through deep sleep
(drifting up to roughly ±20 s per hour), but it restarts from zero if the battery is disconnected
or runs flat. The keychain sets it over NTP whenever it connects to Wi-Fi. If that hasn't happened
yet, the Mac works the time out from how long ago the note was recorded
(see [PROTOCOL.md](PROTOCOL.md) section 3).
