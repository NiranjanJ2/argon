"""Which units exist, and the keys needed to talk to them.

Binding is cheap but chatty, and the key a unit hands back is stable, so it is
kept. The Action Button should be one UDP round trip, not a rediscovery.

Addresses are DHCP leases and do move. The MAC is the identity; the host is a
cache, and a unit that stops answering is re-found by scanning rather than
declared broken.
"""

from __future__ import annotations

import socket
from typing import Any

from loguru import logger

from argon.ac.gree import GENERIC_KEY, GreeError, Unit, decrypt
from argon.core import store

_DOC = "ac_units"

#: The LAN is a /22. Broadcast alone missed two of three units, so discovery
#: also probes the addresses already in the neighbour table.
BROADCASTS = ("255.255.255.255", "192.168.71.255")


def _saved() -> dict[str, dict[str, Any]]:
    return store.get_doc(_DOC, {"units": {}}).get("units", {})


def remember(unit: Unit) -> None:
    with store.edit_doc(_DOC, {"units": {}}) as doc:
        doc.setdefault("units", {})[unit.mac] = {
            "mac": unit.mac,
            "host": unit.host,
            "key": unit.key.decode(),
            "name": unit.name,
            "gcm": unit.gcm,
        }


def forget(mac: str) -> bool:
    with store.edit_doc(_DOC, {"units": {}}) as doc:
        return doc.get("units", {}).pop(mac, None) is not None


def known() -> list[dict[str, Any]]:
    """Every unit we have bound to, without touching the network."""
    return list(_saved().values())


def load(mac: str) -> Unit | None:
    """A ready-to-use unit, already bound, or None if we do not know it."""
    record = _saved().get(mac)
    if not record:
        return None
    return Unit(
        mac=record["mac"],
        host=record["host"],
        key=record["key"].encode(),
        name=record.get("name") or "",
        bound=True,
        gcm=bool(record.get("gcm", True)),
    )


def discover(timeout_s: float = 5.0, hosts: list[str] | None = None) -> list[dict[str, Any]]:
    """Scan the LAN. Returns descriptors; does not bind."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout_s)

    targets = list(BROADCASTS) + list(hosts or [])
    targets += [u["host"] for u in known()]
    for target in targets:
        try:
            sock.sendto(b'{"t":"scan"}', (target, 7000))
        except OSError:
            continue

    seen: dict[str, dict[str, Any]] = {}
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        try:
            import json

            pack = decrypt(json.loads(data)["pack"], GENERIC_KEY)
        except Exception:  # noqa: BLE001 - something else on port 7000
            continue
        mac = pack.get("mac") or pack.get("cid")
        if not mac or mac in seen:
            continue
        seen[mac] = {
            "mac": mac,
            "host": addr[0],
            "name": pack.get("name") or "",
            "version": pack.get("ver"),
            "known": mac in _saved(),
        }
    sock.close()
    return list(seen.values())


def adopt(mac: str, host: str, name: str = "") -> Unit:
    """Bind to a unit and remember its key."""
    unit = Unit(mac=mac, host=host, name=name)
    unit.bind()
    remember(unit)
    return unit


def refresh_host(mac: str) -> Unit | None:
    """A remembered unit moved. Find it again and update the cached address."""
    for found in discover():
        if found["mac"] == mac:
            unit = load(mac)
            if unit is None:
                return None
            unit.host = found["host"]
            remember(unit)
            logger.info("AC {} moved to {}", mac, found["host"])
            return unit
    return None


def with_retry(mac: str):
    """Load a unit, re-finding it once if its cached address has gone stale."""
    unit = load(mac)
    if unit is None:
        return None
    try:
        unit.status()
        return unit
    except GreeError:
        return refresh_host(mac)
