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
| `GET` | `/v1/ios/mode` | — | the desired focus mode alone |
| `POST` | `/v1/ios/state` | `{"mode","version","shielded","applied_at","battery"}` | records what the phone applied |
| `POST` | `/v1/ios/register` | `{"device_token","environment","app_version"}` | stores the APNs token |

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
  "lockdown": {"state":"lock","since":"...","source":"ios"},
  "ios": {
    "desired": {"mode":"lock_in","version":41,"since":"2026-07-31T19:04:11-07:00",
                "expires_at":"2026-07-31T21:00:00-07:00","allow_early_end":false,
                "reason":"Chem pset due 11pm and you haven't started"},
    "actual":  {"mode":"lock_in","version":41,"shielded":true,
                "last_seen":"2026-07-31T19:04:31-07:00"}
  }
}
```

`mode` is one of `idle | working | napping | lock_in | done`.

## Screen Time (`ios`)

Argon publishes a **desired** mode; the app reconciles toward it and reports
back. `version` is the whole protocol — the phone stores the last version it
applied and ignores what it has already seen, so a dropped push or an hour
offline converges instead of replaying a stale command. `argon/ios/mode.py` is
the store; `set_focus_mode` is the model's tool over it.

Two shape rules, both load-bearing for the app:

- **Every field is always present.** The app's Swift structs are non-optional
  apart from `since` and `expires_at`. One missing key fails the whole
  `/v1/status` decode and the app quietly shows "Offline".
- **Timestamps carry no fractional seconds.** `ISO8601DateFormatter` parses zero
  or exactly three fractional digits; Python's `isoformat()` emits six. A
  six-digit `expires_at` decodes to nil, the app treats a timed lock as
  open-ended, and the shield never releases itself.

An elapsed window collapses to `off` (with a version bump) on read, so the
server never advertises a lock the phone already dropped. Modes are
`off | school | homework | lock_in | sleep`; the phone maps the name to a local
Screen Time profile, so the server never sees a profile UUID.

APNs push is **not** wired — `/v1/ios/register` stores the token for later. The
app reconciles on launch, foreground, and a 20-second timer while open, so a
lock lands when the app is next opened rather than instantly.

### Did it actually land?

`convergence()` compares the version the phone last applied against the version
Argon published, so a lock that never took effect cannot pass for success:

| state | meaning |
|---|---|
| `converged` | versions match, and a requested lock is actually shielded |
| `pending` | published; the phone has not answered since |
| `diverged` | answered *after* the request, still on the old version |
| `failed` | the phone reported an `error`, or acknowledged a lock without shielding |
| `stale` | no report for 5 minutes |
| `never_seen` | no report ever |

`POST /v1/ios/state` accepts an optional `error` string, which the app sends
only when it could not apply the mode. This matters more than it looks: the
app's reconciler used to return nil on failure and its caller only reported on
success, so a failed lock was byte-identical to a phone that was switched off —
Argon would have believed it had locked a device that was wide open. Two rules
follow from that:

- **On failure the phone reports the version still in force**, not the one it
  failed to apply, so a plain version comparison shows it has not converged.
- **A matching version is not enough.** The app refuses focus states it deems
  unsafe (no hard expiry) and still reports the version, so a requested lock
  with `shielded: false` counts as `failed`.

`set_focus_mode` says "Do not assume it is locked" on any of these, and
`get_status` grows a `phone_focus` block whenever the answer is not the boring
one — so the model can see a block did not land.

With no APNs key, `stale` is also the ordinary state whenever the app is
backgrounded; that ambiguity disappears once push is wired.

## Emergency override

Getting out must not depend on the thing you are trying to get out of. There
are three independent releases, and **any one of them alone is enough**:

| | Needs | Use when |
|---|---|---|
| `argon unlock` | SSH to the box | anything at all is broken |
| `POST /v1/ios/override` | the HTTP API | from the app, a laptop, a Shortcut |
| Emergency Access in the app | nothing — no network | the server is unreachable |

Releasing on its own is not enough, and this is the part that is easy to get
wrong: the app re-applies the server's desired mode on **every poll**, roughly
every 20 seconds, with no version guard. A plain unlock is therefore undone
almost immediately, and Argon could publish a fresh lock a minute later anyway.
So an override does two things — it releases, *and* it refuses to let any block
be imposed until it expires (`ios.overrideMinutes`, default 120).

```sh
argon unlock                  # release, hold for 2 hours
argon unlock --minutes 30     # shorter window
argon unlock --clear          # end the override now
```

`argon unlock` edits the desired-state files directly, so it works with the
gateway stopped — verified with `argon.service` killed, and the override
survives a restart. `set_mode("off")` is always permitted during an override:
an escape hatch must never be able to jam shut. `set_focus_mode` refuses
outright and tells the model not to work around it.

On the phone, foqos's existing Emergency Access now also engages a **local**
override (`ArgonOverride`, a plain UserDefaults date). While it is set the
reconciler refuses any non-off mode without asking anyone, so it works in
airplane mode; it tells the server too, best-effort, so the two do not fight.
An unreadable or unparseable override file reads as *inactive* — this fails
open on purpose, so a corrupt file can never become a permanent lock.

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
