# VoiceNote keychain

A keychain voice recorder. Press the latching button and it records. Press it again and the
note is saved to the SD card. When it's on home Wi-Fi, it sends the notes to your Mac, which
transcribes them with Whisper, tidies them up with a local Ollama model, and appends them to
`notes.md`.

```
 Keychain (XIAO ESP32S3 Sense)                    MacBook
 ─────────────────────────────                    ────────────────────────────────────────────
 button ─► mic ─► /pending/*.wav  ──Wi-Fi/HTTP──► receiver ─► ~/VoiceNotes/inbox/
 deep sleep between wakes                          processor: Whisper ─► Ollama ─► notes.md
                                                   archive/audio, archive/transcripts, voicenotes.log
```

| Component | Where | What |
|-----------|-------|------|
| Recorder  | [`firmware/`](firmware) | PlatformIO / Arduino-ESP32 firmware |
| Receiver  | [`mac/voicenotes/receiver.py`](mac/voicenotes/receiver.py) | HTTP server that drops uploads into the inbox |
| Processor | [`mac/voicenotes/processor.py`](mac/voicenotes/processor.py) | inbox → Whisper → Ollama → `notes.md` |
| iPhone importer | [`mac/voicenotes/iphone.py`](mac/voicenotes/iphone.py) | optional: iCloud-synced iPhone recordings → inbox ([below](#using-an-iphone-instead-until-the-keychain-is-built)) |

- [`docs/PROTOCOL.md`](docs/PROTOCOL.md): the contract between the three components
- [`docs/HARDWARE.md`](docs/HARDWARE.md): parts, wiring and LED patterns

## 1. Configure

```sh
cp config.example.yaml config.yaml    # git-ignored
```

Fill in every `<-- FILL IN` value: your Wi-Fi, your Mac's local hostname, and a random token.
The firmware and the Mac scripts both read this one file.

## 2. Mac: receiver and processor

Needs Python 3.10+ on Apple Silicon, [Ollama](https://ollama.com) with your model pulled, and ffmpeg.

```sh
brew install ffmpeg
cd mac
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Try it by hand first:

```sh
.venv/bin/python -m voicenotes.receiver                   # terminal 1
.venv/bin/python -m voicenotes.fake_device                # terminal 2: sends a test tone
.venv/bin/python -m voicenotes.processor --fake-models    # no Whisper/Ollama, checks the plumbing
.venv/bin/python -m voicenotes.processor                  # the real thing
```

Then install both as launchd agents, so they start at login:

```sh
launchd/install.sh              # uninstall: launchd/install.sh uninstall
```

- The receiver runs all the time.
- The processor runs whenever something lands in the inbox, and every 15 minutes to retry failures.
- Everything is logged to `~/VoiceNotes/voicenotes.log`.

If the macOS firewall is on, allow incoming connections for the venv's Python when prompted,
or add it under System Settings → Network → Firewall → Options.

Where things end up (`mac.base_dir`, default `~/VoiceNotes`):

```
notes.md                      one entry per note, same layout as docs/example_notes.md
inbox/                        received, not yet processed
archive/audio/YYYY-MM/        processed audio + metadata
archive/transcripts/          raw Whisper transcripts
failed/                       notes that failed 3 times, with an .error.txt
voicenotes.log
```

The model names, prompt files and Ollama options are in the `whisper` and `ollama` sections of
`config.yaml`. The prompt and few-shot examples live in [`mac/prompts/`](mac/prompts).

## Using an iPhone instead (until the keychain is built)

The iPhone can stand in for the keychain. You record on the phone, iCloud syncs the file to
the Mac, and [`voicenotes.iphone`](mac/voicenotes/iphone.py) converts it to the keychain's WAV
format and drops it in the inbox. From there the processor handles it exactly as it would a
keychain note. The receiver isn't involved, so the phone doesn't need to be on home Wi-Fi.

```
 iPhone                                 MacBook
 ──────────────────────────             ────────────────────────────────────────────────
 Voice Memos ──iCloud sync──►  Recordings/*.m4a ─► iphone importer ─► ~/VoiceNotes/inbox/
                                                   (ffmpeg → 16 kHz WAV)   processor as before
```

The Notes app's audio recordings can't be exported automatically, so record with **Voice Memos**
instead.

**On the iPhone**

1. Settings → [your name] → iCloud → turn on **Voice Memos**. Do the same on the Mac under
   System Settings → [your name] → iCloud.
2. Make recording a single press, like the keychain:
   - iPhone 15 Pro or later: Settings → Action Button → **Voice Memo**. Press to start, press again to stop.
   - Other iPhones: add the **Voice Memos** control to Control Center or the Lock Screen.
3. Open Voice Memos on the Mac once, so it starts syncing. New memos should then appear on
   the Mac within a minute or so of stopping.

**On the Mac**

1. Add the `iphone` section from `config.example.yaml` to your `config.yaml`.
2. Give the venv's Python **Full Disk Access**, because Voice Memos keeps its files in a
   protected folder: System Settings → Privacy & Security → Full Disk Access → **+**, press
   ⌘⇧G and enter the path printed by `mac/.venv/bin/python -c "import sys, os; print(os.path.realpath(sys.executable))"`.
   Without it the importer logs "does not exist (or no permission to read it)".
3. Try it by hand, then re-run the installer to add the launchd agent:

```sh
mac/bin/import-iphone                       # first run: only memos from now on
mac/bin/import-iphone --since 2026-09-01    # or backfill older memos
mac/launchd/install.sh                      # adds com.voicenotes.iphone
```

The importer runs in three ways:

| When | How |
|------|-----|
| A memo lands in the Voice Memos folder | launchd starts it. It waits (up to `max_wait_seconds`) for the memo to finish syncing, so the note appears about 30 s after syncing |
| Once an hour | launchd, as a backstop, e.g. for a change missed while the Mac was asleep |
| Whenever you like | `mac/bin/import-iphone` (add `--no-wait` to skip memos still syncing). If an automatic run is already going, it says so and leaves it to that run |

Imported notes land in the inbox, which starts the processor as usual.

It only reads your recordings and never moves or deletes them. It remembers what it has already imported, so
renaming or re-syncing a memo doesn't create a second entry. The recording time comes from the
timestamp in the file, and falls back to the file date, which the heading marks "approx.".

**Alternative: a Shortcut, without Full Disk Access.** Make a Shortcut with three actions:
**Record Audio**, **Set Name** (to the Current Date, custom format `yyyy-MM-dd_HH-mm-ss`), and
**Save File** (turn off *Ask Where to Save*, subpath `VoiceNotes/`). Put it on the Action Button. It saves to iCloud Drive → Shortcuts → VoiceNotes; use the
commented-out `watch_dirs` line in `config.example.yaml` for that folder. macOS may ask you once
to allow Python to access iCloud Drive.

## 3. Keychain firmware

Needs [PlatformIO](https://platformio.org) (`pip install platformio`, or the VS Code extension).

```sh
cd firmware
pio run -t upload       # builds with the values from ../config.yaml and flashes over USB-C
pio device monitor      # optional: watch the log (the chip sleeps most of the time)
```

Re-flash after changing anything in the `wifi`, `receiver` or `device` sections of `config.yaml`.

## Tests

```sh
pip install -r mac/requirements-dev.txt
pytest                  # from the repo root
```

The tests cover the receiver (auth, checksums, duplicates, naming), time resolution, the
`notes.md` format, the processor (retries, idempotency, failures) and the iPhone importer
(timestamps, de-duplication, retries) using fake models, so
they run anywhere without Whisper or Ollama.

## Future ideas

- Vibration motor for start/stop feedback
- Battery level shown in `notes.md`
- Over-the-air firmware updates
- Splitting output into `tasks.md` or daily files
