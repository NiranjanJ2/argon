#!/usr/bin/env bash
# Argon setup. Safe to re-run: every step checks before it acts.
#
#   bash setup.sh              install, configure, and offer to run as a service
#   bash setup.sh --no-service just install and configure
#
# The previous version of this script wrote ~/.nanobot/config.json, installed a
# `nanobot` binary, configured a provider that is no longer used and demanded a
# WhatsApp phone number that is not. None of it would have worked. Everything
# that needs judgement now lives in `argon init`, which is Python and tested;
# this file only does the parts that need a shell.
set -euo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; RESET=$'\033[0m'

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WANT_SERVICE=1
[[ "${1:-}" == "--no-service" ]] && WANT_SERVICE=0

step() { printf '\n%s%s%s\n' "$BOLD" "$1" "$RESET"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '\n%s%s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

printf '%sArgon setup%s  %s%s%s\n' "$BOLD" "$RESET" "$DIM" "$HERE" "$RESET"

# ── Python ───────────────────────────────────────────────────────────────────
step "Python"
command -v python3 >/dev/null || die "python3 not found."
PY=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
# Matches requires-python in pyproject.toml.
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' \
  || die "Python 3.11+ required (found $PY)."
ok "python3 $PY"

if ! python3 -m venv --help >/dev/null 2>&1; then
  warn "python3-venv missing; trying to install it"
  sudo apt-get install -y python3-venv -qq || die "Install python3-venv and re-run."
fi

# ── Virtualenv ───────────────────────────────────────────────────────────────
step "Installing"
[[ -d "$HERE/.venv" ]] || python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install -q --upgrade pip
"$HERE/.venv/bin/pip" install -q -e "$HERE"
ARGON="$HERE/.venv/bin/argon"
[[ -x "$ARGON" ]] || die "Install finished but $ARGON is missing."
ok "argon installed"

# ── Config ───────────────────────────────────────────────────────────────────
# Interactive unless the caller already supplied the answers as env vars.
step "Configuration"
if [[ -n "${ARGON_PROVIDER_KEY:-}" ]]; then
  "$ARGON" init --non-interactive
else
  "$ARGON" init
fi

# ── Service ──────────────────────────────────────────────────────────────────
if [[ "$WANT_SERVICE" -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
  step "Service"
  read -rp "  Run Argon as a systemd service on boot? (y/N) " reply
  if [[ "$reply" =~ ^[Yy] ]]; then
    # Templated from the running user and checkout, because the committed unit
    # file used to hardcode one machine's paths.
    UNIT=/etc/systemd/system/argon.service
    sudo tee "$UNIT" >/dev/null <<UNITFILE
[Unit]
Description=Argon
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HERE
ExecStart=$ARGON gateway
Restart=always
RestartSec=5
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

# State lives in \${ARGON_HOME:-~/.argon}; the checkout is read-only to it.
ReadWritePaths=$HOME/.argon
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNITFILE
    sudo systemctl daemon-reload
    sudo systemctl enable --now argon
    sleep 3
    if systemctl is-active --quiet argon; then
      ok "argon.service running"
    else
      warn "argon.service did not start — journalctl -u argon -n 40"
    fi
  else
    ok "skipped; run it yourself with: $ARGON gateway"
  fi
fi

# ── What is actually working ─────────────────────────────────────────────────
step "Checking"
"$ARGON" doctor || true

cat <<NEXT

${BOLD}Next${RESET}
  $ARGON doctor          what is working
  $ARGON chat            talk to it from here
  $ARGON google-auth work    connect a Google account

  desktop/install.sh     menu-bar and desktop readouts (macOS)

${DIM}Re-run this script any time; it only fills in what is missing.${RESET}
NEXT
