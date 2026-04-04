/**
 * Argon WhatsApp bridge — whatsapp-web.js ↔ nanobot HTTP glue.
 *
 * Env vars (all optional, shown with defaults):
 *   NANOBOT_WEBHOOK_URL  http://localhost:3995/whatsapp/incoming
 *   WA_BRIDGE_PORT       3996
 *   WA_DATA_PATH         ./wwebjs_auth  (relative to CWD when launched)
 *
 * Endpoints exposed:
 *   POST /send   { to: "16265551234", body: "text" }  → sends WhatsApp message
 *   GET  /health  → { status: "connected" | "disconnected" | "initializing" }
 */

'use strict';

const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode-terminal');

const WEBHOOK_URL   = process.env.NANOBOT_WEBHOOK_URL || 'http://localhost:3995/whatsapp/incoming';
const BRIDGE_PORT   = parseInt(process.env.WA_BRIDGE_PORT || '3996', 10);
const WA_DATA_PATH  = process.env.WA_DATA_PATH || './wwebjs_auth';

// ── State ─────────────────────────────────────────────────────────────────────
let clientState = 'initializing';  // initializing | connected | disconnected

// ── WhatsApp client ───────────────────────────────────────────────────────────
const client = new Client({
    authStrategy: new LocalAuth({ dataPath: WA_DATA_PATH }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
        ],
    },
});

client.on('qr', (qr) => {
    console.log('\n📱 Scan this QR code with WhatsApp on your phone:\n');
    qrcode.generate(qr, { small: true });
    console.log('\nWaiting for scan...');
});

client.on('authenticated', () => {
    console.log('✓ WhatsApp authenticated — session saved.');
});

client.on('ready', () => {
    clientState = 'connected';
    console.log('✓ WhatsApp connected and ready.');
});

client.on('auth_failure', (msg) => {
    console.error('WhatsApp auth failure:', msg);
    clientState = 'disconnected';
});

client.on('disconnected', (reason) => {
    console.error('WhatsApp disconnected:', reason);
    clientState = 'disconnected';
    // Exit so the Python channel can restart the process.
    process.exit(1);
});

// Forward incoming messages to nanobot
client.on('message', async (msg) => {
    if (msg.fromMe) return;

    const payload = {
        from:      msg.from,           // e.g. "16265551234@c.us"
        body:      msg.body,
        timestamp: msg.timestamp,
        type:      msg.type,           // "chat" | "image" | ...
    };

    try {
        const res = await fetch(WEBHOOK_URL, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        if (!res.ok) {
            console.error(`Webhook POST failed: HTTP ${res.status}`);
        }
    } catch (err) {
        console.error('Failed to forward message to nanobot:', err.message);
    }
});

// ── Express HTTP server ───────────────────────────────────────────────────────
const app = express();
app.use(express.json());

// POST /send — send a WhatsApp message
app.post('/send', async (req, res) => {
    if (clientState !== 'connected') {
        return res.status(503).json({ error: 'WhatsApp not connected yet' });
    }

    const { to, body } = req.body || {};
    if (!to || typeof body !== 'string') {
        return res.status(400).json({ error: '"to" and "body" required' });
    }

    // Accept bare phone numbers or @c.us format
    const chatId = to.includes('@') ? to : `${to}@c.us`;

    try {
        await client.sendMessage(chatId, body);
        res.json({ ok: true });
    } catch (err) {
        console.error('sendMessage failed:', err.message);
        res.status(500).json({ error: err.message });
    }
});

// GET /health
app.get('/health', (_req, res) => {
    res.json({ status: clientState });
});

app.listen(BRIDGE_PORT, '127.0.0.1', () => {
    console.log(`WhatsApp bridge HTTP server on port ${BRIDGE_PORT}`);
    console.log(`Forwarding incoming messages to: ${WEBHOOK_URL}`);
});

// ── Start ─────────────────────────────────────────────────────────────────────
console.log('Initializing WhatsApp client...');
client.initialize().catch((err) => {
    console.error('client.initialize() failed:', err.message);
    process.exit(1);
});
