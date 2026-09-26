# Protocol: keychain → receiver → inbox

This is the contract between the three components. The firmware produces it, the receiver
checks it, and the processor consumes the inbox. If you change something here, change it
in all three.

## 1. Recording files (on the SD card)

| Folder        | Contents                                                             |
|---------------|----------------------------------------------------------------------|
| `/recording/` | The note currently being recorded. Anything left here at boot was cut off by a power loss and is repaired. |
| `/pending/`   | Finished notes waiting to be sent: `<name>.wav` + `<name>.json`       |
| `/sent/`      | Notes the Mac confirmed. Kept; the oldest are pruned below 200 MB free |

`<name>` is `20260926-154210Z` (UTC start time) when the device clock is set, otherwise
`rec_000123` (the sequence number).

Audio is a standard 44-byte-header PCM WAV: 16 kHz, 16-bit, mono.

### Sidecar JSON (`<name>.json`, one line)

| Field             | Type          | Meaning                                                        |
|-------------------|---------------|----------------------------------------------------------------|
| `schema`          | int           | Always `1`                                                     |
| `device_id`       | string        | `device.id` from config                                        |
| `fw_version`      | string        | Firmware version                                               |
| `seq`             | int           | Per-device counter stored in flash, survives power loss        |
| `boot_id`         | string        | 8 hex chars, new random value on every power-on               |
| `time_synced`     | bool          | Whether the clock had been set by NTP when recording started   |
| `started_at`      | int           | Unix time at start (only meaningful when `time_synced`)        |
| `started_mono`    | int           | Seconds on the device's monotonic clock at start               |
| `duration_s`      | float         | Audio length                                                   |
| `sample_rate`, `bits_per_sample`, `channels` | int | `16000`, `16`, `1`                               |
| `stop_reason`     | string        | `button_off`, `max_length`, `low_battery`, `sd_full`, `power_loss` |
| `battery_v`       | float or null | Battery voltage at start, `null` if no divider is fitted       |
| `dropped_ms`      | int           | Audio lost to buffer overruns (normally 0)                     |

The **monotonic clock** counts seconds since power-on and never jumps when NTP corrects the
wall clock. Together with `boot_id`, it lets the Mac date a recording made before the clock
was ever set.

## 2. HTTP (device → Mac)

The receiver listens on `receiver.port` (default 8765). The device finds it by, in order:

1. the last IP that worked
2. an mDNS lookup of `receiver.mac_hostname`
3. `receiver.fallback_ip`

### `GET /health`
`200 {"status": "ok", "protocol": 1}`

### `POST /upload`
The body is the raw WAV bytes. Headers:

| Header              | Value                                               |
|---------------------|-----------------------------------------------------|
| `Authorization`     | `Bearer <receiver.token>`                           |
| `Content-Length`    | Size of the WAV in bytes                            |
| `X-VN-Protocol`     | `1`                                                 |
| `X-VN-Filename`     | `<name>.wav` as stored on the SD card               |
| `X-VN-SHA256`       | Lowercase hex SHA-256 of the body                   |
| `X-VN-Meta`         | The sidecar JSON (one line)                         |
| `X-VN-Boot-Id`      | The device's **current** `boot_id`                  |
| `X-VN-Device-Mono`  | The device's **current** monotonic clock, seconds   |
| `X-VN-Device-Time`  | The device's current Unix time (informational)      |

Responses (JSON body):

| Code | Body                                           | Device action          |
|------|------------------------------------------------|------------------------|
| 200  | `{"status": "stored", "name": "<stem>"}`       | move to `/sent/`       |
| 200  | `{"status": "duplicate", "name": "<stem>"}`    | move to `/sent/`       |
| 400 / 411 | `{"status": "error", "error": "..."}`     | keep, retry later      |
| 401  | bad or missing token                           | keep, retry later      |
| 413  | larger than 512 MB                             | keep                   |
| 415  | body is not a RIFF/WAVE file                   | keep                   |
| 422  | SHA-256 mismatch (corrupted in transit)        | keep, retry later      |
| 507  | the Mac's disk is full                         | keep, retry later      |

Duplicates are detected by (`device_id`, `seq`, `sha256`), so re-sending after a dropped
connection is always safe.

## 3. Recording time

The receiver works out `recorded_at` using the first rule that applies:

| `time_source`   | Rule                                                                         |
|-----------------|------------------------------------------------------------------------------|
| `device_clock`  | `time_synced` is true: `started_at`                                          |
| `relative`      | same `boot_id` as now: Mac time − (`X-VN-Device-Mono` − `started_mono`)      |
| `received`      | otherwise: when the Mac received it (the heading in notes.md shows "approx.") |

A `device_clock` value more than 5 minutes in the future is ignored, and the next rule is tried.

## 4. Inbox (receiver → processor)

For each note, the receiver writes two files into `<base_dir>/inbox/`, named by local recording time:

```
2026-09-26_15-42-10.wav
2026-09-26_15-42-10.json    <- written last; its presence means the pair is complete
```

If two notes share the same second, the second one gets a `_2` suffix, and so on.

The inbox JSON is the device sidecar plus:

| Field               | Meaning                                         |
|---------------------|-------------------------------------------------|
| `recorded_at`       | ISO 8601 with the Mac's UTC offset              |
| `time_source`       | `device_clock`, `relative` or `received`        |
| `received_at`       | ISO 8601 with the Mac's UTC offset              |
| `sha256`            | Of the WAV                                      |
| `original_filename` | `X-VN-Filename`                                 |

## 5. Archive (after processing)

```
<base_dir>/notes.md                           entries appended in the example's layout
<base_dir>/archive/audio/2026-09/<stem>.wav   and <stem>.json
<base_dir>/archive/transcripts/<stem>.txt     raw Whisper output
<base_dir>/failed/<stem>.{wav,json,error.txt} after max_attempts failures
<base_dir>/voicenotes.log                     one line per event from both services
```
