# Argon for iPhone

This app is the native dashboard for the Argon v2 service running on `agentneon`. It chats with the same agent, shows the same productivity state, and reconciles Screen Time directly from Argon's desired focus mode. It does not use email, SMS, Pushcut, or a Shortcuts automation.

The Screen Time engine is based on [awaseem/foqos](https://github.com/awaseem/foqos) at commit [`39d85c7ba40cc0354a4ee9d2213dd273312c107d`](https://github.com/awaseem/foqos/commit/39d85c7ba40cc0354a4ee9d2213dd273312c107d). Its MIT license is preserved in `LICENSE`.

## Architecture

Argon publishes desired state rather than sending an imperative lock/unlock payload:

1. `set_mode(lock_in)` or `set_focus_mode(...)` writes a versioned desired mode on `agentneon`.
2. The app fetches `GET /v1/status`, compares the desired state, and applies it through Foqos's `StrategyManager`.
3. The app reports the actual state to `POST /v1/ios/state`.
4. It reconciles on launch, foreground, pull-to-refresh, and every 20 seconds while active. APNs is not wired yet, so background changes converge the next time the app is opened.

Every non-off mode has a server-issued `expires_at`. The app schedules Foqos's on-device `DeviceActivity` timer for that boundary, so a server outage cannot leave an Argon-managed shield active indefinitely.

## Live API

All `/v1/*` requests use:

```text
Authorization: Bearer <api.token from ~/.argon/config.json>
```

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/status` | Daily mode, current task, durations, desired iOS state, and last reported actual state |
| `POST /v1/chat` | Full agent turn on the dedicated `ios` session |
| `GET /v1/tasks` | Pending tasks from Argon's existing Google Tasks store plus current work state |
| `POST /v1/tasks` | Add a task through the same tool path Argon uses |
| `PATCH /v1/tasks/<id>` | Start, complete, reprioritize, or reschedule a shared task |
| `GET /v1/ios/mode` | Desired focus state only |
| `POST /v1/ios/state` | Applied-state heartbeat |
| `POST /v1/ios/register` | APNs token registration |

The **Dashboard** tab is a cached view of that shared task store, not a second local task system. The **Chat** tab keeps conversation history on-device and sends each turn directly to the running Argon agent. Failed messages remain visible and can be retried.

The Argon server currently stores APNs registrations for future use but does not send pushes. Foreground reconciliation and simulator testing work without an Apple APNs key.

## Simulator connection

The simulator reaches the live service through an SSH tunnel on the Mac:

```sh
ssh -N -L 3995:127.0.0.1:3995 agentneon
```

Use `http://127.0.0.1:3995` in **Settings → Argon Connection**, then paste the live server's `api.token`. The app's default URL is already set for this tunnel.

For a physical iPhone, use a private LAN/VPN/HTTPS address that routes to `agentneon`. Do not publicly expose the plain HTTP port.

## Build and test

```sh
cd docs/iosapp
make build
make test
```

The simulator validates the UI, API synchronization, desired-state decoding, and session logic. Apple does not enforce Family Controls or Managed Settings in the simulator, so actual shielding must also be checked on an iPhone.

For a device build:

1. Open `foqos.xcodeproj` in Xcode.
2. Select your development team for the app and all four extensions.
3. Replace the upstream app, extension, and app-group identifiers with identifiers owned by that team.
4. Enable Family Controls and Push Notifications for the app identifier and provisioning profile.
5. Match `ios.bundleId` on `agentneon` to the signed app's bundle identifier.

The shield extensions currently require iOS 18.5 or later.

## Pair the app

1. Install Argon and grant Screen Time and notification access.
2. Create a profile named `Argon Lockdown` and choose the apps, categories, and sites to restrict.
3. Open **Settings → Argon Connection**.
4. Enter the server URL and the `api.token` from `~/.argon/config.json`.
5. Confirm the profile name and tap **Connect to Argon**.
6. Ask Argon to lock in, then leave lock-in, and verify both transitions.

The authenticated Argon reconciliation path is trusted and bypasses Foqos's public background-stop guard. Manual, NFC, QR, and Shortcut entry points retain their existing rules.
