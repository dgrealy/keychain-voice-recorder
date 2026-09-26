# Setup and test guide

This guide tells you how to set up and test the system. Do the parts in order. Each part
tests one component before you add the next one. You run every command on your Mac.

---

## Part A: Get the code

1. Open Terminal.
2. Clone the repository:
   ```sh
   git clone https://github.com/dgrealy/cov-seminar-slides.git
   cd cov-seminar-slides
   ```

---

## Part B: Write the configuration

1. Copy the template:
   ```sh
   cp config.example.yaml config.yaml
   ```
2. Get the local hostname of your Mac:
   ```sh
   scutil --get LocalHostName
   ```
   Result: A name, for example `Davids-MacBook-Air`.
3. Make a token:
   ```sh
   python3 -c "import secrets; print(secrets.token_hex(16))"
   ```
4. Open `config.yaml` in a text editor. Fill in these values:
   - `wifi.ssid` and `wifi.password`: your home Wi-Fi.
   - `receiver.mac_hostname`: the name from step 2. Do not add `.local`.
   - `receiver.token`: the token from step 3.
5. Make sure the `whisper.model` and `ollama.model` values are correct.
6. Set `device.pins.battery_adc` to `-1`.

> **CAUTION:** The ESP32 connects only to 2.4 GHz Wi-Fi. Make sure that your router has a 2.4 GHz band.

> **CAUTION:** Keep `battery_adc` at `-1` until you install the battery and the two resistors.
> If you do not, the chip can read a false low voltage and refuse to record.

---

## Part C: Install the Mac software

1. Make sure that these items are installed:
   - Python 3.10 or later
   - [Ollama](https://ollama.com), with your model pulled
   - [Homebrew](https://brew.sh)
2. Install ffmpeg. Whisper needs it:
   ```sh
   brew install ffmpeg
   ```
3. Make a virtual environment and install the packages:
   ```sh
   cd mac
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
   cd ..
   ```

---

## Part D: Test the Mac software

### D1. Run the automatic tests

1. From the repository root, run:
   ```sh
   mac/.venv/bin/python -m pytest
   ```
   Result: All tests pass.

### D2. Test the receiver

1. Open Terminal window 1. Start the receiver:
   ```sh
   cd mac
   .venv/bin/python -m voicenotes.receiver
   ```
   Result: `Listening on port 8765`.
2. Open Terminal window 2. Send a request to the receiver:
   ```sh
   curl http://localhost:8765/health
   ```
   Result: `{"status": "ok", "protocol": 1}`.
3. Connect your phone to the same Wi-Fi. In the phone browser, open
   `http://<your-hostname>.local:8765/health`.
   Result: The same text as step 2. This shows that other devices on your network can reach the Mac.

   > **NOTE:** If you get no answer, the macOS firewall blocks the connection. Go to
   > System Settings > Network > Firewall > Options. Allow incoming connections for Python.

4. In window 2, send a test note:
   ```sh
   cd mac
   .venv/bin/python -m voicenotes.fake_device
   ```
   Result: `200 {'status': 'stored', 'name': '...'}`.
5. Look in the inbox:
   ```sh
   ls ~/VoiceNotes/inbox
   ```
   Result: One `.wav` file and one `.json` file.

### D3. Test the processor without models

1. In window 2, run:
   ```sh
   .venv/bin/python -m voicenotes.processor --fake-models
   ```
2. Look at the notes file:
   ```sh
   cat ~/VoiceNotes/notes.md
   ```
   Result: An entry with the title `fake title`.

### D4. Test the processor with Whisper and Ollama

1. Make sure that Ollama runs.
2. Record a short voice memo on the Mac, for example with Voice Memos.
3. Convert it to WAV:
   ```sh
   ffmpeg -i memo.m4a -ar 16000 -ac 1 memo.wav
   ```
4. Send it, then run the real processor:
   ```sh
   .venv/bin/python -m voicenotes.fake_device --wav memo.wav
   .venv/bin/python -m voicenotes.processor
   ```
   Result: `~/VoiceNotes/notes.md` has a new entry with a real title and cleaned-up text.

   > **NOTE:** The first Whisper run downloads the model. This can take several minutes.

### D5. Remove the test data

1. In window 1, push Ctrl+C to stop the receiver.
2. Delete the test data:
   ```sh
   rm -rf ~/VoiceNotes
   ```

> **CAUTION:** This deletes all notes in that folder. Do this only before you use the system for real notes.

---

## Part E: Start the Mac software automatically (launchd)

1. Make sure that no receiver runs in a Terminal window. If one runs, the launchd receiver cannot use the port.
2. From the repository root, run:
   ```sh
   mac/launchd/install.sh
   ```
   Result: `Loaded com.voicenotes.receiver` and `Loaded com.voicenotes.processor`.
3. Check that both agents run:
   ```sh
   launchctl list | grep voicenotes
   ```
4. Send a test note:
   ```sh
   cd mac && .venv/bin/python -m voicenotes.fake_device
   ```
5. Wait approximately 30 seconds. Then look at the log:
   ```sh
   tail ~/VoiceNotes/voicenotes.log
   ```
   Result: A `Received ...` line, then an `Added ...` line. The processor started by itself.
6. If you see no lines, look at the error files in `~/VoiceNotes/.state/`
   (`launchd-receiver.err` and `launchd-processor.err`).
7. Delete this test entry from `~/VoiceNotes/notes.md`.

> **NOTE:** To remove the agents, run `mac/launchd/install.sh uninstall`.

---

## Part F: Build and flash the firmware

1. Install PlatformIO:
   ```sh
   pip3 install platformio
   ```
2. Build the firmware:
   ```sh
   cd firmware
   pio run
   ```
   Result: `SUCCESS`. The first build downloads the ESP32 tools. This can take 5 to 10 minutes.
3. Put the SD card into the Sense board.
4. Connect the XIAO to the Mac with a USB-C cable. Use a cable that carries data, not only power.
5. Flash the firmware:
   ```sh
   pio run -t upload
   ```
6. If the upload fails, put the chip into download mode:
   1. Hold the **B** (BOOT) button.
   2. Push and release the **R** (RESET) button.
   3. Release **B**.
   4. Do step 5 again.

> **NOTE:** After the first flash, the chip is almost always in deep sleep. In deep sleep, the
> USB port is not available. Thus you must use download mode for each new flash.

---

## Part G: Connect the parts

Use [HARDWARE.md](HARDWARE.md) for the details.

1. Connect the button between D1 and GND.
2. Connect D3 to the resistor. Connect the resistor to the LED anode (long leg).
   Connect the LED cathode to GND.
3. Do not connect the battery yet. Supply power from USB.

---

## Part H: Test the keychain

Keep the Mac awake and on the same Wi-Fi during these tests.

### H1. Record a note

1. Push the button until it locks.
2. Wait until the LED comes on. Then speak for 5 to 10 seconds.
3. Push the button again to release it.

   Result: The LED blinks 2 times. The note is saved.

### H2. Make sure that a short push is discarded

1. Push the button. Release it in less than 1 second.

   Result: No blinks. The keychain does not save the note.

### H3. Check the upload

1. Wait approximately 20 seconds after H1.
2. Look at the log:
   ```sh
   tail ~/VoiceNotes/voicenotes.log
   ```
   Result: `Received ...`, then `Added ...`. The note is in `notes.md`.

### H4. Check the audio quality

1. Open the WAV file in `~/VoiceNotes/archive/audio/` and listen to it.
2. If the sound is too quiet, increase `device.mic_gain` in `config.yaml`, for example to `16`.
3. If the sound is distorted, decrease the value.
4. Flash again (Part F, step 5).

### H5. Test the retry when the Mac cannot be reached

1. Temporarily set `device.sync_interval_minutes` to `2`, then flash again.
2. Stop the receiver:
   ```sh
   launchctl bootout gui/$(id -u)/com.voicenotes.receiver
   ```
3. Record a note.

   Result: No upload. The note stays on the SD card.
4. Start the receiver again:
   ```sh
   mac/launchd/install.sh
   ```
5. Wait up to 2 minutes.

   Result: The note arrives. The chip woke up by itself and sent it.
6. Set `sync_interval_minutes` back to `30`, then flash again.

### H6. Add the battery

1. Remove USB power.
2. Solder the battery to the BAT+ and BAT− pads.
3. Install the two 220 kΩ resistors: BAT+ → D0 → GND.
4. Set `device.pins.battery_adc` to `1`, then flash again.
5. Record a note.
6. Open its `.json` file in `~/VoiceNotes/archive/audio/`.

   Result: `battery_v` shows a value from approximately 3.5 to 4.2.

### H7. Measure the sleep current (optional)

1. Connect a USB power meter between the Mac and the keychain.
2. Wait until the LED is off and the chip sleeps.
3. Read the current. This value sets the battery life.

---

> **NOTE:** If the MacBook is asleep or the lid is closed, the keychain cannot send notes.
> It keeps them on the SD card and tries again later. You do not lose notes.
