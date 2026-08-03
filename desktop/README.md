# Desktop readouts

Two live views of Argon on the Mac: a **SwiftBar** menu bar item and an
**Übersicht** desktop widget. Both shell out to `argon-widget.py`, which is the
only thing that talks to the server — so they cannot disagree, and adding a
field means editing one file.

```
argon-widget.py            fetch + build_view: everything the readouts SAY
  --json                   → Übersicht (argon.jsx renders it)
  (no args)                → SwiftBar plugin format
  --selftest               → asserts, no network
argon.jsx                  Übersicht layout
install.sh                 puts both into place
preview/build.mjs          render argon.jsx to static HTML, to look at it
```

`build_view` decides what the readout says — mode wording, task grouping, sort
order, every string needing a clock. The renderers only decide how it looks.

## Styling

Lifted from the iOS app, not invented. `PALETTE` in `argon-widget.py` mirrors
`Foqos/Utils/ArgonDesign.swift`; the card is that file's `argonGlassPanel`; the
serif face is `Font.argonDisplay`; the Overdue/Today/Later sections, their
tints, the priority pill and the row sort all come from
`Views/ArgonDashboardView.swift`. Change a colour there, change `PALETTE` here.

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

## Looking at it before shipping it

The desktop widget is invisible to a terminal, so styling changes are otherwise
unverifiable. `preview/build.mjs` renders `argon.jsx` to a static page against
sample states — including ones the live server is rarely in, like an overdue
task or a lock the phone refused:

```sh
cd preview && npm install esbuild
node build.mjs ../argon.jsx preview.html fixtures.json
python3 -m http.server 8731        # then open it
```

It is a stand-in, not Übersicht: a ~40-line `h` shim replaces React. That gap is
real — the first version silently swallowed any error message beginning with
`<`, because the shim was sniffing markup instead of tagging it. Trust it for
layout and colour, not for behaviour.

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
