#!/bin/bash
# Install the Argon desktop readouts. Idempotent — re-run after editing either file.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.config/argon"
SWIFTBAR_DIR="$HOME/Library/Application Support/SwiftBar"
UBERSICHT_DIR="$HOME/Library/Application Support/Übersicht/widgets"
INTERVAL="${1:-10s}"   # SwiftBar refresh, encoded in the plugin filename

mkdir -p "$CONFIG_DIR" "$SWIFTBAR_DIR" "$UBERSICHT_DIR"

# ~/.config/argon is the install; this directory is the versioned source. Copy
# rather than symlink, so the readouts keep working if the checkout moves or is
# deleted — re-run this script to pick up edits.
install -m 755 "$SRC/argon-widget.py" "$CONFIG_DIR/argon-widget.py"
# The readout imports this from its own directory, so it has to land next to it.
install -m 644 "$SRC/argon_activity.py" "$CONFIG_DIR/argon_activity.py"
install -m 644 "$SRC/argon.jsx" "$UBERSICHT_DIR/argon.jsx"
# The Now panel is a second widget, not a replacement: both read the same
# `argon-widget.py --json`, so they cannot disagree about what is running.
install -m 644 "$SRC/argon-now.jsx" "$UBERSICHT_DIR/argon-now.jsx"
# SwiftBar is off by default: Argon.app is a native menu bar item that shows the
# same thing from one long-lived process, where the plugin costs a fresh Python
# interpreter every ten seconds — 8,640 process starts a day for a duplicate.
# Pass --swiftbar to install it anyway.
rm -f "$SWIFTBAR_DIR"/argon.*.py
if [ "${2:-}" = "--swiftbar" ] || [ "${1:-}" = "--swiftbar" ]; then
  ln -sf "$CONFIG_DIR/argon-widget.py" "$SWIFTBAR_DIR/argon.$INTERVAL.py"
  echo "SwiftBar plugin installed at $INTERVAL."
fi

if [ ! -f "$CONFIG_DIR/desktop.json" ]; then
  cat > "$CONFIG_DIR/desktop.json" <<'JSON'
{
  "url": "http://192.168.68.72:3995",
  "remoteUrl": "https://argon.agentneon.dev",
  "token": ""
}
JSON
  # The token is a bearer credential for an API with no TLS. Owner-only.
  chmod 600 "$CONFIG_DIR/desktop.json"
  echo "Created $CONFIG_DIR/desktop.json — put the server's api.token in it."
fi

"$SRC/argon-widget.py" --selftest
echo "Installed. Open SwiftBar and Übersicht (they pick up plugins on launch)."
