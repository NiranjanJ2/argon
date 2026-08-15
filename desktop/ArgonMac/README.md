# Argon for Mac

A menu bar readout. Replaces the SwiftBar plugin; the Übersicht widgets stay
until the widget extension lands (phase two).

    ./build.sh            # builds and installs to ~/Applications/Argon.app
    open ~/Applications/Argon.app

Reads `~/.config/argon/desktop.json` — the same file the Python readout used, so
there is nothing to configure. `url` is tried first and `remoteUrl` second, so
it is fast on the LAN and still works away from the house.

## Why not the Python + Übersicht + SwiftBar stack

That stack worked, but it cost two third-party apps and a Python process
spawned every five seconds. macOS has supported widgets on the desktop since
14, so none of it is load-bearing any more.

## What it shows

One list, not three. The old readout rendered the same five tasks under "Start
working on", "Due" and "Later" in a single menu, which is how a readout stops
being read. Order is: Argon's open questions, what is running, what is due
today, then the next event.
