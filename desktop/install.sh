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
install -m 644 "$SRC/argon.jsx" "$UBERSICHT_DIR/argon.jsx"
rm -f "$SWIFTBAR_DIR"/argon.*.py
ln -sf "$CONFIG_DIR/argon-widget.py" "$SWIFTBAR_DIR/argon.$INTERVAL.py"

if [ ! -f "$CONFIG_DIR/desktop.json" ]; then
  cat > "$CONFIG_DIR/desktop.json" <<'JSON'
{
  "url": "http://192.168.68.72:3995",
  "token": ""
}
JSON
  # The token is a bearer credential for an API with no TLS. Owner-only.
  chmod 600 "$CONFIG_DIR/desktop.json"
  echo "Created $CONFIG_DIR/desktop.json — put the server's api.token in it."
fi

"$SRC/argon-widget.py" --selftest
echo "Installed. Open SwiftBar and Übersicht (they pick up plugins on launch)."
