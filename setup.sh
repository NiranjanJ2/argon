#!/usr/bin/env bash
# Argon setup script — run once on a fresh Linux server.
# Usage: bash setup.sh
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BOLD}Argon setup${RESET}"
echo "=================================="

# ── 0. Prerequisites ──────────────────────────────────────────────────────────
echo -e "\n${BOLD}Checking prerequisites...${RESET}"

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10) ]]; then
    echo -e "${RED}Python 3.10+ required (found $PY_VER). Install it and retry.${RESET}"
    exit 1
fi
echo -e "  Python $PY_VER ${GREEN}✓${RESET}"

if ! command -v systemctl &>/dev/null; then
    echo -e "${YELLOW}Warning: systemctl not found — services won't be created.${RESET}"
    NO_SYSTEMD=1
fi

# ── 1. Install Python package ─────────────────────────────────────────────────
echo -e "\n${BOLD}Installing Argon...${RESET}"
pip install -e "$SCRIPT_DIR/.[discord]" --quiet
export PATH="$HOME/.local/bin:$PATH"
NANOBOT_BIN=$(which nanobot 2>/dev/null || echo "$HOME/.local/bin/nanobot")
echo -e "${GREEN}✓ Installed${RESET} ${DIM}($NANOBOT_BIN)${RESET}"

# ── 2. Node.js + WhatsApp bridge ──────────────────────────────────────────────
echo -e "\n${BOLD}WhatsApp bridge setup${RESET}"
if ! command -v node &>/dev/null; then
    echo -e "${YELLOW}Node.js not found — installing via NodeSource (LTS)...${RESET}"
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - 2>/dev/null
    sudo apt-get install -y nodejs -qq
fi
NODE_BIN=$(which node)
echo -e "  Node.js $(node --version) ${GREEN}✓${RESET}"
(cd "$SCRIPT_DIR/whatsapp_bridge" && npm install --silent)
echo -e "${GREEN}✓ WhatsApp bridge ready${RESET}"

# ── 3. Collect secrets ────────────────────────────────────────────────────────
echo -e "\n${BOLD}Configuration${RESET}"
echo -e "${DIM}Press Enter to skip optional fields.${RESET}\n"

while [[ -z "$NIM_API_KEY" ]]; do
    read -rp "NIM API key: " NIM_API_KEY
done

while [[ -z "$DISCORD_TOKEN" ]]; do
    read -rp "Discord bot token: " DISCORD_TOKEN
done

while [[ -z "$DISCORD_USER_ID" ]]; do
    read -rp "Your Discord user ID: " DISCORD_USER_ID
done

while [[ -z "$WA_PHONE" ]]; do
    read -rp "Your WhatsApp phone number (digits + country code, e.g. 16265551234): " WA_PHONE
done

read -rp "Brave Search API key ${DIM}(optional — free tier at brave.com/search/api)${RESET}: " BRAVE_API_KEY

# ── 4. Write config ───────────────────────────────────────────────────────────
mkdir -p ~/.nanobot

if [[ -n "$BRAVE_API_KEY" ]]; then
    SEARCH_BLOCK='"provider": "brave", "apiKey": "'"$BRAVE_API_KEY"'"'
else
    SEARCH_BLOCK='"provider": "duckduckgo"'
fi

cat > ~/.nanobot/config.json <<EOF
{
  "agents": {
    "defaults": {
      "model": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
      "fallbackModel": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
      "provider": "custom"
    }
  },
  "providers": {
    "custom": {
      "apiKey": "${NIM_API_KEY}",
      "apiBase": "https://integrate.api.nvidia.com/v1"
    }
  },
  "channels": {
    "discord": {
      "enabled": true,
      "token": "${DISCORD_TOKEN}",
      "allowFrom": ["${DISCORD_USER_ID}"],
      "groupPolicy": "mention"
    },
    "whatsapp": {
      "enabled": true,
      "phoneNumber": "${WA_PHONE}",
      "bridgePort": 3996
    }
  },
  "tools": {
    "exec": { "enable": false },
    "web": {
      "enable": true,
      "search": { ${SEARCH_BLOCK} }
    }
  }
}
EOF
echo -e "${GREEN}✓ Config written to ~/.nanobot/config.json${RESET}"

# ── 5. Workspace directories ──────────────────────────────────────────────────
mkdir -p ~/.nanobot/workspace/google
mkdir -p ~/.nanobot/workspace/daily
mkdir -p ~/.nanobot/workspace/habits
echo -e "${GREEN}✓ Workspace directories created${RESET}"

# ── 6. Google OAuth ───────────────────────────────────────────────────────────
echo -e "\n${BOLD}Google API Setup${RESET}"
echo -e "To use Google Calendar, Classroom, and Tasks you need a ${BOLD}client_secrets.json${RESET}"
echo -e "from Google Cloud Console:\n"
echo -e "  1. console.cloud.google.com → New Project (or pick existing)"
echo -e "  2. Enable these APIs: Calendar, Tasks, Classroom, Drive, Gmail"
echo -e "  3. APIs & Services → Credentials → Create OAuth 2.0 Client ID"
echo -e "     Application type: ${BOLD}Desktop app${RESET}"
echo -e "  4. Download the JSON and copy it to:"
echo -e "     ${BOLD}~/.nanobot/workspace/google/client_secrets.json${RESET}\n"

read -rp "Have you placed client_secrets.json? (y/N): " HAS_SECRETS
if [[ "$HAS_SECRETS" =~ ^[Yy]$ ]]; then
    echo ""
    read -rp "Authenticate 'school' account (Classroom, Drive, Gmail)? (y/N): " DO_SCHOOL
    [[ "$DO_SCHOOL" =~ ^[Yy]$ ]] && "$NANOBOT_BIN" google-auth school

    read -rp "Authenticate 'personal' account (Calendar, Drive, Gmail)? (y/N): " DO_PERSONAL
    [[ "$DO_PERSONAL" =~ ^[Yy]$ ]] && "$NANOBOT_BIN" google-auth personal
else
    echo -e "${YELLOW}Skipping — run these later:${RESET}"
    echo -e "  nanobot google-auth school"
    echo -e "  nanobot google-auth personal"
fi

# ── 7. WhatsApp QR scan ───────────────────────────────────────────────────────
echo -e "\n${BOLD}WhatsApp QR scan${RESET}"
echo -e "The bridge will start and print a QR code. Scan it with your phone:"
echo -e "  ${BOLD}WhatsApp → Settings → Linked Devices → Link a Device${RESET}"
echo -e "${DIM}Press Ctrl+C once you see '✓ WhatsApp connected and ready.'${RESET}\n"
read -rp "Ready to scan? (y/N): " DO_QR
if [[ "$DO_QR" =~ ^[Yy]$ ]]; then
    (cd "$SCRIPT_DIR/whatsapp_bridge" && node index.js) || true
    echo -e "\n${GREEN}✓ WhatsApp session saved${RESET}"
else
    echo -e "${YELLOW}Skipping — the bridge will start with the service but you'll need to check logs for the QR.${RESET}"
fi

# ── 8. Systemd services ───────────────────────────────────────────────────────
if [[ -z "$NO_SYSTEMD" ]]; then
    echo -e "\n${BOLD}Creating systemd services...${RESET}"

    sudo tee /etc/systemd/system/argon-whatsapp.service > /dev/null <<EOF
[Unit]
Description=Argon WhatsApp Bridge
After=network.target

[Service]
User=$USER
WorkingDirectory=$SCRIPT_DIR/whatsapp_bridge
ExecStart=$NODE_BIN index.js
Restart=always
RestartSec=5
Environment=HOME=$HOME

[Install]
WantedBy=multi-user.target
EOF

    sudo tee /etc/systemd/system/argon.service > /dev/null <<EOF
[Unit]
Description=Argon AI Assistant
After=network.target argon-whatsapp.service

[Service]
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$NANOBOT_BIN gateway
Restart=always
RestartSec=5
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable argon-whatsapp argon
    echo -e "${GREEN}✓ Services created and enabled (start on boot)${RESET}"

    echo -e "\n${BOLD}Starting services...${RESET}"
    sudo systemctl start argon-whatsapp
    sleep 2
    sudo systemctl start argon
    sleep 3

    if systemctl is-active --quiet argon; then
        echo -e "${GREEN}✓ Argon is running${RESET}"
    else
        echo -e "${RED}Argon failed to start. Check logs: journalctl -u argon -n 30${RESET}"
    fi

    if systemctl is-active --quiet argon-whatsapp; then
        echo -e "${GREEN}✓ WhatsApp bridge is running${RESET}"
    else
        echo -e "${YELLOW}WhatsApp bridge not running. Check: journalctl -u argon-whatsapp -n 30${RESET}"
    fi
fi

# ── 9. Done ───────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}${BOLD}Setup complete.${RESET}\n"
echo -e "  Dashboard   http://localhost:3995"
echo -e "  Argon logs  journalctl -u argon -f"
echo -e "  WA logs     journalctl -u argon-whatsapp -f"
echo -e ""
echo -e "${BOLD}To update:${RESET}"
echo -e "  cd $SCRIPT_DIR && git pull && pip install -e '.[discord]' --quiet && sudo systemctl restart argon"
echo -e ""
echo -e "${BOLD}Google auth (run any time):${RESET}"
echo -e "  nanobot google-auth school"
echo -e "  nanobot google-auth personal"
echo -e ""
echo -e "${BOLD}iPhone Shortcuts (one-time setup):${RESET}"
echo -e "  1. Create a Focus mode called 'Lock Down' with distracting apps blocked"
echo -e "  2. Shortcuts → Automation → Message Received from [Argon's number]:"
echo -e "       Contains 'lockdown' → Set Focus: Lock Down On  (run immediately)"
echo -e "       Contains 'unlock'   → Set Focus: Lock Down Off (run immediately)"
echo -e "  3. Shortcuts → Automation → When I arrive home → send 'Neon is home' to Argon"
