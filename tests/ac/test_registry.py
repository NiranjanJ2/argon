"""The re-find path, which is what a moved DHCP lease depends on."""

from __future__ import annotations

from argon.ac import registry

# Real /proc/net/arp output: a resolved unit, a second one, and an incomplete
# entry for the address the unit used to hold. Discovery must probe the first
# two and must not waste a probe on the third.
ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.68.61    0x1         0x2         c0:39:37:10:28:bd     *        wlp0s20f3
192.168.68.60    0x1         0x2         c0:39:37:10:28:d0     *        wlp0s20f3
192.168.68.75    0x1         0x0         00:00:00:00:00:00     *        wlp0s20f3
"""


def test_neighbours_skips_incomplete_entries(tmp_path, monkeypatch):
    arp = tmp_path / "arp"
    arp.write_text(ARP)
    monkeypatch.setattr(registry, "Path", lambda _: arp)

    assert registry._neighbours() == ["192.168.68.61", "192.168.68.60"]


def test_neighbours_is_empty_off_linux(monkeypatch):
    """No /proc is not a failure — broadcast is still tried."""

    def missing(_):
        raise OSError("no /proc")

    monkeypatch.setattr(registry, "Path", missing)
    assert registry._neighbours() == []
