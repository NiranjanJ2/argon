#!/usr/bin/env bash
# Argon setup script — run once on a fresh server.
# Usage: bash setup.sh
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RESET='\033[0m'

echo -e "${BOLD}Argon setup${RESET}"
echo "----------------------------"

# ── 1. Install Python package ────────────────────────────────────────────────
echo -e "\n${BOLD}Installing Argon...${RESET}"
pip install -e ".[discord]" --quiet
echo -e "${GREEN}✓ Installed${RESET}"

# ── 2. WhatsApp bridge (Node.js) ─────────────────────────────────────────────
echo -e "\n${BOLD}WhatsApp bridge setup${RESET}"
if ! command -v node &> /dev/null; then
    echo -e "${YELLOW}Node.js not found. Installing via NodeSource (LTS)...${RESET}"
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
NODE_VER=$(node --version 2>/dev/null)
echo -e "Node.js: ${NODE_VER}"

echo -e "Installing whatsapp-web.js dependencies..."
(cd "$(dirname "$0")/whatsapp_bridge" && npm install --silent)
echo -e "${GREEN}✓ WhatsApp bridge ready${RESET}"

# ── 2. Collect secrets ───────────────────────────────────────────────────────
echo -e "\n${BOLD}Configuration${RESET}"

read -rp "NIM API key: " NIM_API_KEY
read -rp "Discord bot token: " DISCORD_TOKEN
read -rp "Your Discord user ID (allowFrom): " DISCORD_USER_ID
read -rp "Your WhatsApp phone number (digits only, with country code, e.g. 16265551234): " WA_PHONE

# ── 3. Write config ──────────────────────────────────────────────────────────
mkdir -p ~/.nanobot

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
    "web": { "enable": true }
  }
}
EOF

echo -e "${GREEN}✓ Config written to ~/.nanobot/config.json${RESET}"

# ── 4. Google API setup ──────────────────────────────────────────────────────
echo -e "\n${BOLD}Google API Setup${RESET}"
echo -e "You need a ${BOLD}client_secrets.json${RESET} from Google Cloud Console."
echo -e "Steps:"
echo -e "  1. Go to console.cloud.google.com → New Project"
echo -e "  2. Enable: Calendar API, Tasks API, Classroom API, Drive API, Gmail API"
echo -e "  3. APIs & Services → Credentials → Create OAuth 2.0 Client ID (Desktop app)"
echo -e "  4. Download the JSON and place it at:"
echo -e "     ${BOLD}~/.nanobot/workspace/google/client_secrets.json${RESET}"
echo ""
read -rp "Have you placed client_secrets.json? (y/N): " HAS_SECRETS

if [[ "$HAS_SECRETS" =~ ^[Yy]$ ]]; then
    mkdir -p ~/.nanobot/workspace/google
    echo -e "\nAuthenticating Google accounts (browser will open for each)..."
    echo -e "${YELLOW}Authenticate in the order prompted. Each opens a browser tab.${RESET}\n"

    read -rp "Authenticate 'personal' account (Drive)? (y/N): " DO_PERSONAL
    [[ "$DO_PERSONAL" =~ ^[Yy]$ ]] && nanobot google-auth personal

    read -rp "Authenticate 'work' account (Calendar, Tasks, Drive, Gmail)? (y/N): " DO_WORK
    [[ "$DO_WORK" =~ ^[Yy]$ ]] && nanobot google-auth work

    read -rp "Authenticate 'school' account (Classroom, Drive, Gmail)? (y/N): " DO_SCHOOL
    [[ "$DO_SCHOOL" =~ ^[Yy]$ ]] && nanobot google-auth school
else
    echo -e "${YELLOW}Skipping Google auth. Run 'nanobot google-auth <account>' later.${RESET}"
fi

# ── 5. Done ──────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}${BOLD}Setup complete.${RESET}"
echo -e "Run ${BOLD}nanobot gateway${RESET} to start."
echo -e "Dashboard will be at ${BOLD}http://localhost:3995${RESET}"
echo -e "\n${BOLD}First-time WhatsApp setup:${RESET}"
echo -e "  1. Run ${BOLD}nanobot gateway${RESET}"
echo -e "  2. A QR code will appear in the terminal."
echo -e "  3. Open WhatsApp on your phone → Settings → Linked Devices → Link a Device."
echo -e "  4. Scan the QR code. Done — session is saved for future restarts."
echo -e "\n${BOLD}iPhone Shortcuts for lockdown/unlock:${RESET}"
echo -e "  1. Create a Focus mode called 'Lock Down' with your distracting apps restricted."
echo -e "  2. In Shortcuts → Automation → New → Message Received:"
echo -e "     - From: [your Argon WhatsApp contact]"
echo -e "     - Message Contains: lockdown"
echo -e "     - Action: Set Focus → Lock Down → On"
echo -e "     - Run immediately (disable 'Ask Before Running')"
echo -e "  3. Repeat for 'unlock' → Set Focus → Lock Down → Off"
echo -e "  4. In Shortcuts → Automation → New → Personal (iPhone shortcut):"
echo -e "     - When you arrive home → send 'Neon is home' to your Argon WhatsApp contact"
echo -e "\nGoogle auth can be run at any time:"
echo -e "  nanobot google-auth personal"
echo -e "  nanobot google-auth work"
echo -e "  nanobot google-auth school"
