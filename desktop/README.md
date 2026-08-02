# Desktop readouts

Two live views of Argon on the Mac: a **SwiftBar** menu bar item and an
**Übersicht** desktop widget. Both shell out to `argon-widget.py`, which is the
only thing that talks to the server — so they cannot disagree, and adding a
field means editing one file.

```
argon-widget.py            fetch + all clock-dependent formatting
  --json                   → Übersicht (argon.jsx renders it)
  (no args)                → SwiftBar plugin format
  --selftest               → asserts, no network
argon.jsx                  Übersicht layout
install.sh                 symlinks both into place
```

## Install

```sh
brew install --cask swiftbar ubersicht
./install.sh              # or ./install.sh 30s for a slower menu bar
```

Then put the server's `api.token` (from `~/.argon/config.json` on `agentneon`)
into `~/.config/argon/desktop.json`, and launch both apps once — they pick up
plugins on start. `install.sh` symlinks rather than copies, so editing this
directory updates both readouts on their next refresh.

## Why the split

Everything that needs a clock — countdowns, `overdue 2d`, `running 12m` — is
computed in Python and handed over as finished strings. Übersicht renders in
WebKit with no notion of the server's timezone, and SwiftBar would otherwise
compute the same strings a second time, which is exactly how two displays start
disagreeing. `enrich()` is that boundary.

`argon-widget.py` is stdlib-only and Python 3.9-compatible on purpose: SwiftBar
launches plugins from launchd, whose `PATH` finds `/usr/bin/python3` (3.9)
rather than Homebrew's.

## Refresh and cost

SwiftBar's interval is encoded in its plugin *filename* (`argon.10s.py`);
Übersicht's is `refreshFrequency` in `argon.jsx`. Neither costs a Google round
trip — `/v1/tasks` caches for 60 s server-side, so polling is one LAN request.

## Reachability

`/v1/*` is on the home LAN with no TLS (`http://192.168.68.72:3995`). Off the
network both readouts show unreachable until there's a tunnel — an SSH forward
(`ssh -N -L 3995:127.0.0.1:3995 agentneon`, then point `url` at
`http://127.0.0.1:3995`) or a VPN. `desktop.json` holds a bearer token for that
API, so `install.sh` creates it `0600`.
