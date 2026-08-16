"""Local control for Gree/Tosot air conditioners.

Nothing to do with the assistant. It is here because the server is already on
the same LAN as the units, already authenticated, and already reachable from
the phone through the tunnel — so the Action Button works from anywhere rather
than only on home WiFi, and the protocol gets written once instead of twice.

The protocol is UDP on port 7000, AES-128-ECB, JSON inside. Every unit accepts
a published factory key until you *bind* to it, which hands back a key of its
own; from then on that key encrypts everything. Binding is additive — the Tosot
app keeps working alongside it.

Stdlib plus ``cryptography``, which the Google auth stack already pulls in.
"""

from __future__ import annotations

import base64
import json
import socket
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger

#: Published in every Gree client; the unit accepts it until you bind.
GENERIC_KEY = b"a3K8Bx%2r8Y7#xDh"

#: V2 firmware speaks AES-GCM for everything after discovery, with its own
#: factory key and a fixed nonce. Discovery stays ECB on both, which is why a
#: unit can answer a scan perfectly and then ignore an ECB bind completely —
#: it is not refusing, it cannot read the packet.
GENERIC_KEY_GCM = b"{yxAHAY_Lm6pbC/<"
GCM_NONCE = b"\x54\x40\x78\x44\x49\x67\x5a\x51\x6c\x5e\x63\x13"
GCM_AAD = b"qualcomm-test"

PORT = 7000
TIMEOUT_S = 4.0

#: The fields worth reading. The unit accepts more, but each one costs bytes in
#: a UDP datagram and most describe hardware this model does not have.
STATUS_COLS = [
    "Pow",       # 0 off, 1 on
    "Mod",       # 0 auto, 1 cool, 2 dry, 3 fan, 4 heat
    "SetTem",    # target, in TemUn units
    "WdSpd",     # 0 auto, 1 low … 5 high
    "TemUn",     # 0 celsius, 1 fahrenheit
    "TemSen",    # what the unit thinks the room is, offset by 40
    "SwUpDn",    # vertical swing
    "Lig",       # display light
    "Quiet",
    "Tur",       # turbo
    "SvSt",      # eco / power saving
]

MODES = {0: "auto", 1: "cool", 2: "dry", 3: "fan", 4: "heat"}
MODE_VALUES = {name: value for value, name in MODES.items()}


def _pad(data: bytes) -> bytes:
    """PKCS#7 to the AES block size."""
    padding = 16 - (len(data) % 16)
    return data + bytes([padding]) * padding


def encrypt(payload: dict[str, Any], key: bytes = GENERIC_KEY) -> str:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    raw = _pad(json.dumps(payload, separators=(",", ":")).encode())
    return base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode()


def decrypt(pack: str, key: bytes = GENERIC_KEY) -> dict[str, Any]:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    raw = base64.b64decode(pack)
    out = decryptor.update(raw) + decryptor.finalize()
    return json.loads(out[: -out[-1]].decode(errors="replace"))


def encrypt_gcm(payload: dict[str, Any], key: bytes) -> tuple[str, str]:
    """``(pack, tag)`` — V2 firmware wants the auth tag as its own field."""
    encryptor = Cipher(algorithms.AES(key), modes.GCM(GCM_NONCE)).encryptor()
    encryptor.authenticate_additional_data(GCM_AAD)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = encryptor.update(raw) + encryptor.finalize()
    return base64.b64encode(body).decode(), base64.b64encode(encryptor.tag).decode()


def decrypt_gcm(pack: str, tag: str, key: bytes) -> dict[str, Any]:
    decryptor = Cipher(
        algorithms.AES(key), modes.GCM(GCM_NONCE, base64.b64decode(tag))
    ).decryptor()
    decryptor.authenticate_additional_data(GCM_AAD)
    raw = base64.b64decode(pack)
    out = decryptor.update(raw) + decryptor.finalize()
    return json.loads(out.decode(errors="replace"))


class GreeError(RuntimeError):
    """The unit did not answer, or answered something unreadable."""


@dataclass
class Unit:
    """One air conditioner: where it is, and how to talk to it."""

    mac: str
    host: str
    key: bytes = GENERIC_KEY
    name: str = ""

    #: Set once bound. Until then every message uses the factory key.
    bound: bool = field(default=False)

    #: V2 firmware speaks AES-GCM. Discovery reports the version, so this is
    #: usually known before the first real message; ``bind`` proves it.
    gcm: bool = field(default=True)

    def _send(self, payload: dict[str, Any], *, key: bytes | None = None) -> dict[str, Any]:
        key = key or self.key
        request: dict[str, Any] = {
            "cid": "app",
            "i": 0 if self.bound else 1,
            "t": "pack",
            "uid": 0,
            "tcid": self.mac,
        }
        if self.gcm:
            request["pack"], request["tag"] = encrypt_gcm(payload, key)
        else:
            request["pack"] = encrypt(payload, key)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(TIMEOUT_S)
        try:
            sock.sendto(json.dumps(request).encode(), (self.host, PORT))
            data, _ = sock.recvfrom(65535)
        except socket.timeout as exc:
            raise GreeError(
                f"{self.mac} at {self.host} did not answer "
                f"({'GCM' if self.gcm else 'ECB'})"
            ) from exc
        finally:
            sock.close()

        try:
            reply = json.loads(data)
            if self.gcm:
                return decrypt_gcm(reply["pack"], reply["tag"], key)
            return decrypt(reply["pack"], key)
        except Exception as exc:  # noqa: BLE001 - a garbled reply is a failure
            raise GreeError(f"{self.mac} sent an unreadable reply: {exc}") from exc

    def bind(self) -> bytes:
        """Ask the unit for a key of its own.

        Tries GCM first when the descriptor said V2, then falls back — the
        firmware string is a strong hint rather than a guarantee, and a unit
        that cannot read the packet stays silent rather than complaining, so
        guessing wrong looks exactly like the unit being offline.
        """
        attempts = [True, False] if self.gcm else [False, True]
        last: Exception | None = None
        for use_gcm in attempts:
            self.gcm = use_gcm
            self.bound = False
            factory = GENERIC_KEY_GCM if use_gcm else GENERIC_KEY
            try:
                reply = self._send({"mac": self.mac, "t": "bind", "uid": 0}, key=factory)
            except GreeError as exc:
                last = exc
                continue
            key = reply.get("key")
            if not key:
                last = GreeError(f"{self.mac} refused to bind: {reply}")
                continue
            self.key = key.encode()
            self.bound = True
            logger.info(
                "Bound to AC {} at {} using {}", self.mac, self.host,
                "GCM" if use_gcm else "ECB",
            )
            return self.key
        raise last or GreeError(f"{self.mac} would not bind")

    def status(self) -> dict[str, Any]:
        """Everything in STATUS_COLS, decoded into names rather than indexes."""
        reply = self._send({"cols": STATUS_COLS, "mac": self.mac, "t": "status"})
        values = dict(zip(reply.get("cols", []), reply.get("dat", [])))
        return {
            "mac": self.mac,
            "host": self.host,
            "name": self.name,
            "on": bool(values.get("Pow")),
            "mode": MODES.get(values.get("Mod"), "unknown"),
            "target_c": values.get("SetTem"),
            "fan": values.get("WdSpd"),
            # The unit reports room temperature offset by 40, in celsius, and
            # reports 0 when the sensor is absent rather than admitting it.
            "room_c": (values["TemSen"] - 40) if values.get("TemSen") else None,
            "raw": values,
        }

    def set(self, **options: int) -> dict[str, Any]:
        """Set one or more raw columns, e.g. ``Pow=1`` or ``SetTem=22``."""
        if not options:
            raise ValueError("nothing to set")
        keys = list(options)
        return self._send(
            {"opt": keys, "p": [options[k] for k in keys], "t": "cmd"}
        )

    def power(self, on: bool) -> dict[str, Any]:
        return self.set(Pow=1 if on else 0)
