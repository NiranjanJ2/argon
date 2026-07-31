# iOS app API

Argon's HTTP surface, served by `argon gateway` on `api.host:api.port`
(default `0.0.0.0:3995`). Intended for use on your own network — there is no TLS
here, so put it behind Tailscale/WireGuard rather than a port forward.

## Auth

Every `/v1/*` request needs the bearer token from `~/.argon/config.json`:

```
Authorization: Bearer <api.token>
```

Compared with `hmac.compare_digest`. An empty `api.token` fails closed — every
`/v1` request is rejected. Bodies must be JSON objects, max 1 MB.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `{"status":"ok","service":"argon"}` (no auth) |
| `POST` | `/v1/chat` | `{"message": "..."}` | `{"reply": "...", "session": "ios"}` |
| `GET` | `/v1/status` | — | widget payload, below |
| `POST` | `/v1/screentime` | any object | `{"ok": true, "date": "YYYY-MM-DD"}` |
| `GET` | `/v1/screentime?date=` | — | `{"date", "count", "records"}` |
| `POST` | `/v1/lockdown` | `{"state": "lock"\|"unlock", "trigger": bool}` | `{"ok","state","since","source","triggered"}` |
| `POST` | `/v1/webhook/<name>` | `{"message": "..."}` | runs a turn and also delivers the reply to Discord |

Errors are always JSON: `401` bad/missing token, `400` bad body, `413` oversize,
`502` agent failure, `503` not ready, `504` turn timed out (120 s cap on chat).

## `/v1/status`

Calls the same `get_status` tool the model uses, so the widget and Argon can
never disagree:

```json
{
  "mode": "working",
  "current_task": "calc pset",
  "home_arrival": "2026-07-30T15:12:04-07:00",
  "work_session_minutes": 42,
  "lock_in_minutes": null,
  "school_period": {"status":"in_period","period":"Period 4",
                    "ends_at":"12:49","minutes_remaining":12},
  "lockdown": {"state":"lock","since":"...","source":"ios"}
}
```

`mode` is one of `idle | working | napping | lock_in | done`.

## Screen Time

`POST /v1/screentime` accepts whatever shape you send and appends
`{"received_at": "<local ISO8601>", "payload": <your object>}` to
`~/.argon/screentime/YYYY-MM-DD.jsonl`, bucketed by **local** date so a day
doesn't split at 5pm UTC. The schema is deliberately loose until the app exists.

Suggested starting shape:

```json
{"apps": [{"bundleId": "com.burbn.instagram", "seconds": 1840}], "day": "2026-07-30"}
```

Once you settle on a shape, the natural next step is a `get_screentime` tool so
Argon can reason over usage ("you've had 3h of Instagram and the pset isn't
started") — `read_screentime(day, limit)` in `argon/api/server.py` is the
in-process read path for it.

## Lockdown

Today lockdown is a mail to a carrier SMS gateway that trips an iOS Shortcut.
`POST /v1/lockdown` records state and, with `"trigger": true`, also fires that
mail. When the app can enforce Screen Time directly, point it at
`argon.tools.lockdown.send_trigger` — that function is the single seam both the
model's `send_phone_notification` tool and this endpoint go through.

## Replacing WhatsApp

`/v1/chat` is a full agent turn on its own `ios` session, so the app can be a
complete chat client. When you're ready to drop WhatsApp, set
`channels.whatsapp.enabled` to `false` and delete the `whatsapp_bridge/`
directory — nothing else depends on it.
