"""PlatformIO pre-build step: turn ``../config.yaml`` into ``src/generated_config.h``.

The header is only rewritten when its contents change, so unrelated builds stay incremental.
"""
import json
from pathlib import Path

Import("env")  # noqa: F821 (injected by PlatformIO)

try:
    import yaml
except ImportError:
    env.Execute("$PYTHONEXE -m pip install -q pyyaml")  # noqa: F821
    import yaml

project = Path(env["PROJECT_DIR"])  # noqa: F821
source = project.parent / "config.yaml"
if not source.exists():
    print("WARNING: config.yaml not found, building with config.example.yaml (the keychain will not connect)")
    source = project.parent / "config.example.yaml"

cfg = yaml.safe_load(source.read_text())
wifi, rx, dev = cfg["wifi"], cfg["receiver"], cfg["device"]
pins = dev["pins"]


def text(value):
    """C string literal (JSON escaping is valid C for these values)."""
    return json.dumps(str(value or ""), ensure_ascii=False)


constants = [
    ("const char*", "WIFI_SSID", text(wifi["ssid"])),
    ("const char*", "WIFI_PASSWORD", text(wifi["password"])),
    ("const char*", "MAC_HOSTNAME", text(str(rx.get("mac_hostname") or "").removesuffix(".local"))),
    ("const char*", "FALLBACK_IP", text(rx.get("fallback_ip"))),
    ("uint16_t", "RECEIVER_PORT", int(rx.get("port", 8765))),
    ("const char*", "TOKEN", text(rx["token"])),
    ("const char*", "DEVICE_ID", text(dev.get("id", "keychain-01"))),
    ("uint32_t", "SYNC_INTERVAL_MIN", int(dev.get("sync_interval_minutes", 30))),
    ("uint32_t", "MAX_RECORDING_MIN", int(dev.get("max_recording_minutes", 30))),
    ("float", "MIN_RECORDING_S", f"{float(dev.get('min_recording_seconds', 1.0))}f"),
    ("float", "MIC_GAIN", f"{float(dev.get('mic_gain', 8))}f"),
    ("float", "LOW_BATTERY_V", f"{float(dev.get('low_battery_volts', 3.5))}f"),
    ("float", "CRITICAL_BATTERY_V", f"{float(dev.get('critical_battery_volts', 3.3))}f"),
    ("float", "BATTERY_DIVIDER", f"{float(dev.get('battery_divider_ratio', 2.0))}f"),
    ("int", "PIN_BUTTON", int(pins.get("button", 2))),
    ("int", "PIN_LED", int(pins.get("led", 4))),
    ("int", "PIN_BATTERY", int(pins.get("battery_adc", 1))),
    ("int", "PIN_SD_CS", int(pins.get("sd_cs", 21))),
]
header = ("// Generated from config.yaml by scripts/gen_config.py. Do not edit.\n"
          "#pragma once\n#include <stdint.h>\n\nnamespace cfg {\n"
          + "".join(f"constexpr {t} {name} = {value};\n" for t, name, value in constants)
          + "}  // namespace cfg\n")

out = project / "src" / "generated_config.h"
if not out.exists() or out.read_text() != header:
    out.write_text(header)
