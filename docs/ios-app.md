# Argon iOS app spec

Target: a fork of [`awaseem/foqos`](https://github.com/awaseem/foqos) with three
added tabs. Foqos already gives you Screen Time blocking, profiles, NFC/QR
strategies, widgets and Live Activities — none of that is respecified here.
This document covers **only** what Argon adds:

| Tab | What it is |
|---|---|
| **Focus** | foqos as-is (existing root view) |
| **Argon** | connection, mode↔profile bindings, nudge log |
| **Chat** | full agent conversation, replaces WhatsApp |
| **Dashboard** | today's tasks as a todo list |

Assumed done: foqos builds, `FamilyControls` entitlement is approved, blocking
works when *you* start it. Everything below is about letting **Argon** start it.

---

## 1. The core idea: desired state, not commands

The naive design is "server sends a `lock` command over push". It breaks the
first time a push is dropped, delivered twice, or delivered late — and iOS
drops silent pushes routinely.

Instead: **the server publishes a desired mode; the phone reconciles toward
it.** Every reconciliation is idempotent, ordering doesn't matter, and a lost
push costs you latency instead of correctness.

```
Argon decides "lock_in"          ┌──────────────┐
        │                        │  GET /v1/ios/mode
        ▼                        │  → {mode:"lock_in", version:41, expires_at:...}
  desired_mode.json ◄────────────┤
        │                        │
        │  APNs silent push ────►│  phone: version 41 > my 40 → apply
        │  ("doorbell", no data) │  StrategyManager.start(profile for lock_in)
        │                        │
        ▼                        └──────────────┐
  POST /v1/ios/state  ◄─────────────────────────┘  phone reports what it did
```

The push carries **no instructions**. It only says "check in". The phone also
reconciles on: app foreground, `BGAppRefreshTask`, a `DeviceActivityMonitor`
callback, and a 15-minute timer while a session is active. If APNs is having a
bad day the worst case is a delayed lock, never a wrong one.

### Safety: never build a brick

Every server-driven session carries a hard `expires_at` **applied on-device**.
If the server dies, the phone unblocks itself on schedule. Two rules:

- `expires_at` is absolute and stored locally the moment a mode is applied.
- The app clears any expired shield on launch, on foreground, and on a
  `UNTimeIntervalNotificationTrigger` scheduled for the expiry instant.

There is no server-side "you may not unblock" state that survives the phone
losing network. `allow_early_end: false` is a *nag*, not a jail — the app shows
a confirmation sheet and reports the override to Argon, then unblocks anyway.

---

## 2. Modes

A **mode** is a name Argon knows. A **profile** is a foqos `BlockedProfiles`
row. The binding between them lives **on the phone**, set once in the Argon tab.
The server never sees a profile UUID it didn't learn from the phone.

| Mode | Meaning | Typical binding |
|---|---|---|
| `off` | nothing blocked | — |
| `school` | bell-schedule hours | social + games |
| `homework` | active work session | social + games + YouTube |
| `lock_in` | deep work, hard | everything but Notes/Calculator |
| `sleep` | wind-down | everything |

Unbound modes are a no-op with a reported reason (`"unbound"`), not an error.
Adding a mode later is a string on both sides — no migration.

---

## 3. Server API additions

All under the existing bearer auth in `argon/api/server.py`
(`Authorization: Bearer <api.token>`, `hmac.compare_digest`). Existing
endpoints — `/v1/chat`, `/v1/status`, `/v1/screentime`, `/v1/lockdown` — are
unchanged; see [`ios-api.md`](ios-api.md).

### `GET /v1/ios/mode` — desired state

```json
{
  "mode": "lock_in",
  "version": 41,
  "since": "2026-07-30T19:04:11-07:00",
  "expires_at": "2026-07-30T21:00:00-07:00",
  "allow_early_end": false,
  "reason": "Chem pset due 11pm and you haven't started"
}
```

`version` is a monotonic counter, bumped on every change. The phone stores the
last version it applied and no-ops when they match. `expires_at` may be null
(open-ended); `reason` is shown in the app so a lock is never mysterious.

### `POST /v1/ios/state` — actual state + heartbeat

```json
{"mode": "lock_in", "version": 41, "applied_at": "...",
 "shielded": true, "override": false, "battery": 0.62}
```

Stored at `~/.argon/ios/state.json`. `override: true` means you ended a
`allow_early_end: false` session early — Argon should know, and can say
something about it. Doubles as a liveness signal: no report in 30 min and
`argon doctor` flags the phone as offline.

### `POST /v1/ios/profiles` — catalog upload

```json
{"profiles": [{"id": "UUID", "name": "Deep work", "app_count": 34}],
 "bindings": {"lock_in": "UUID", "homework": "UUID"}}
```

Sent whenever bindings change and on every cold launch. Lets Argon say "your
lock_in profile blocks 34 apps" and lets `argon doctor` report unbound modes.

### `GET /v1/tasks` — dashboard feed

Wraps the existing `GoogleTasksStore.get_all()` — **no new storage**, the
dashboard shows exactly what `list_tasks` shows the model.

```json
{"tasks": [{"id": "...", "title": "Chem pset", "priority": "high",
            "due": "2026-07-30T23:00:00-07:00", "subject": "AP Chemistry",
            "source": "classroom", "time_estimate_min": 90,
            "started_at": null, "completed": false}],
 "state": {"mode": "working", "current_task": "Chem pset",
           "work_session_minutes": 42}}
```

### `POST /v1/tasks`, `PATCH /v1/tasks/<id>`

`POST` body is `AddTaskTool`'s parameter object. `PATCH` accepts
`{"action": "start"|"complete"}` or `{"priority": ..., "due": ...}` and routes
to the same `StartTaskTool` / `CompleteTaskTool` / `UpdateTaskTool` paths, so
habit tracking and the daily log stay correct whether a task is completed by
you or by Argon.

### `POST /v1/ios/register`

```json
{"device_token": "<hex APNs token>", "environment": "sandbox"|"production",
 "app_version": "1.0.0"}
```

Persisted to `~/.argon/ios/device.json`. Re-sent on every launch — APNs tokens
rotate.

---

## 4. Server-side implementation

### `argon/ios/mode.py` — the state file

Two functions over `~/.argon/ios/desired_mode.json`, mirroring how
`argon/tools/lockdown.py` exposes `send_trigger` as a shared seam:

```python
def set_mode(mode, *, duration_min=None, allow_early_end=True, reason="") -> dict
def get_mode() -> dict
```

`set_mode` bumps `version`, computes `expires_at` from `duration_min`, writes
atomically, then fires the doorbell push. Both the model's tool and any future
automation go through it.

### `argon/ios/push.py` — APNs

Token-based auth (`.p8` key), HTTP/2 via `httpx`, ES256 JWT via `PyJWT` —
roughly 40 lines. No `apns2` dependency; it's unmaintained and this is one
request.

```python
def notify(reason: str = "mode") -> bool:
    """Doorbell push: content-available:1, no payload. False if it didn't send."""
```

JWT is cached for 50 minutes (Apple's limit is 60). On a `410 Unregistered`,
delete `device.json` and log — the phone will re-register on next launch.

Config (`~/.argon/config.json`):

```json
"ios": {"enabled": true, "key_id": "ABC123DEFG", "team_id": "XYZ9876543",
        "bundle_id": "com.niranjan.argon", "key_path": "~/.argon/ios/AuthKey.p8",
        "environment": "production"}
```

`key_path` file is chmod 600, same as `google/`. Never logged, never in git.

### `argon/tools/focus.py` — the model's tool

```
set_focus_mode(mode, duration_min=None, allow_early_end=true, reason)
```

Description tells the model this is a real intervention: use it when there's a
concrete reason (a deadline it can see, a work session it started), not as a
general nudge. `allow_early_end: false` is reserved for cases you asked for in
advance. Returns the applied mode and whether the push actually delivered, so
the model can say "I locked you down, but your phone is offline."

`get_status` gains `"ios": {"mode": ..., "shielded": ..., "last_seen": ...}` so
the model can see whether the phone is actually complying.

---

## 5. App implementation

New files, all under `Foqos/Argon/`:

```
ArgonClient.swift        async/await HTTP, bearer from Keychain
ArgonReconciler.swift    desired → actual, the only thing that calls StrategyManager
ArgonPush.swift          APNs registration, silent-push handling, BGAppRefresh
ArgonKeychain.swift      token storage
Views/ArgonTabView.swift
Views/ChatView.swift
Views/DashboardView.swift
Models/ArgonModels.swift Codable mirrors of the JSON above
Stores/ChatStore.swift   SwiftData @Model ChatMessage
```

Foqos's existing `StrategyManager` stays the single entry point for starting and
stopping sessions — the reconciler drives it exactly like the NFC path does, so
Live Activities, widgets and session history keep working with no special-casing.
Server-started sessions get `ManualBlockingStrategy` plus an `argonManaged: Bool`
flag on the session model, which is what the UI uses to show "started by Argon".

### `ArgonReconciler`

```swift
func reconcile() async {
    guard let desired = try? await client.mode() else { return }   // offline: keep current
    if desired.version <= lastAppliedVersion, !isExpired() { return }
    if let expiry = desired.expiresAt, expiry <= .now { await stop(); return }
    switch bindings[desired.mode] {
    case .some(let profileID): await strategyManager.start(profileID)
    case .none where desired.mode == "off": await strategyManager.stopAll()
    case .none: report(applied: false, reason: "unbound")
    }
    scheduleExpiryAlarm(desired.expiresAt)
    lastAppliedVersion = desired.version
    await client.reportState(...)
}
```

Called from: `didReceiveRemoteNotification`, `scenePhase == .active`,
`BGAppRefreshTask`, expiry timer, and pull-to-refresh in the Argon tab.

### Push entitlements

- `aps-environment` (`development` → `production` at ship)
- Background Modes → Remote notifications, Background fetch
- `UIBackgroundModes` includes `remote-notification`

Silent push is throttled by iOS when the app is force-quit — this is exactly
why the desired-state design exists. If you want a guaranteed wake, upgrade the
doorbell to a visible alert push with `mutable-content: 1` and reconcile from a
Notification Service Extension carrying the `com.apple.developer.family-controls`
entitlement. Start with silent; add the NSE only if you measure real misses.

### Argon tab

- **Connection**: server URL + paste token → `GET /health` then `GET /v1/status`
  to prove the token works. Green/red dot, last-sync timestamp.
- **Mode bindings**: one row per mode, each a picker over your foqos profiles.
  Changing one uploads the catalog.
- **Current mode**: what Argon wants, why (`reason`), time remaining, and an
  **End early** button — confirmation sheet when `allow_early_end` is false.
- **Nudge log**: last 20 mode changes with reasons. This is the trust surface;
  if you can't see why you got locked, you'll delete the app.

### Chat tab

`POST /v1/chat` — synchronous, up to 120 s. Show a typing indicator and disable
send while in flight; the server serializes on the `ios` session anyway.
Messages persist locally in SwiftData (`ChatMessage: id, role, text, sentAt,
delivered`). Failed sends stay in the list marked undelivered with a retry
button rather than vanishing.

No streaming in v1 — the endpoint is request/response. Add SSE later if 120 s of
silence feels bad in practice; it will need a new endpoint, not a new app.

### Dashboard tab

`GET /v1/tasks` on appear and pull-to-refresh. Sections: **Overdue**, **Today**,
**Later**. Row shows title, subject, due, estimate. Swipe-right completes
(`PATCH action:complete`), tap starts (`PATCH action:start`) which also sets
Argon's `current_task`. Header shows `state.mode` and work-session minutes.

Optimistic UI on complete, revert on failure. Cache the last payload so the tab
isn't blank offline.

---

## 6. Ship order

1. `ArgonClient` + Connection screen. Nothing else works until the token round-trips.
2. Dashboard tab — read-only `GET /v1/tasks`. Proves auth end-to-end, zero risk.
3. Chat tab. Now the app is useful and WhatsApp can go.
4. `GET /v1/ios/mode` + reconciler, **polling only, no push**. Flip modes by
   hand with `curl` and watch the phone follow on foreground.
5. APNs doorbell. Latency drops from "next foreground" to seconds.
6. `set_focus_mode` tool. Argon gets the keys last, after you've watched steps
   4–5 behave for a week.

Steps 1–4 need no Apple push infrastructure at all.

---

## 7. Deliberately not in v1

- **Streaming chat** — 120 s sync is fine at first; SSE when it isn't.
- **Screen Time reporting from the app.** `POST /v1/screentime` already exists
  and takes any shape. Wiring `DeviceActivityReport` into it is a separate,
  self-contained job — the endpoint isn't blocking the app.
- **Push-to-phone notifications from Argon** (as opposed to mode changes). The
  SMS-gateway path in `argon/tools/lockdown.py` still works; replace it once
  APNs is proven.
- **Multi-device.** One `device.json`, one phone. Make it a list when there's a
  second device.
- **Server-enforced unblock prevention.** By design — see §1.
