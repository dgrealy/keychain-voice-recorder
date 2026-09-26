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
`notes.md` format and the processor (retries, idempotency, failures) using fake models, so
they run anywhere without Whisper or Ollama.

## Future ideas

- Vibration motor for start/stop feedback
- Battery level shown in `notes.md`
- Over-the-air firmware updates
- Splitting output into `tasks.md` or daily files
