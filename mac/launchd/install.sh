#!/usr/bin/env bash
# Install (or with "uninstall", remove) the receiver and processor as launchd agents.
#
#   mac/launchd/install.sh              # uses mac/.venv/bin/python
#   PYTHON=/path/to/python mac/launchd/install.sh
#   mac/launchd/install.sh uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MAC_DIR="$(dirname "$HERE")"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABELS=(com.voicenotes.receiver com.voicenotes.processor)

for label in "${LABELS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
done
[[ "${1:-}" == "uninstall" ]] && { echo "Uninstalled."; exit 0; }

PYTHON="${PYTHON:-$MAC_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || { echo "Python not found at $PYTHON (create mac/.venv or set PYTHON=)"; exit 1; }

# Ask the package itself where the folders are, so this always matches config.yaml.
{ read -r INBOX; read -r STATE_DIR; } < <(cd "$MAC_DIR" && "$PYTHON" -c \
  "from voicenotes.config import Paths, load_config; p = Paths.from_config(load_config()).ensure(); print(p.inbox); print(p.state)")

mkdir -p "$AGENTS"
for label in "${LABELS[@]}"; do
  sed -e "s|__PYTHON__|$PYTHON|" -e "s|__MAC_DIR__|$MAC_DIR|" \
      -e "s|__INBOX__|$INBOX|" -e "s|__STATE_DIR__|$STATE_DIR|" \
      "$HERE/$label.plist" > "$AGENTS/$label.plist"
  launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist"
  echo "Loaded $label"
done
echo "Logs: $(dirname "$STATE_DIR")/voicenotes.log (unless mac.log_file says otherwise)"
