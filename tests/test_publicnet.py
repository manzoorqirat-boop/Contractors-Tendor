"""Webhooks may only ever reach the public internet, checked at the moment of connecting and on the address actually used (so a name that answers "public" to a check and
"private" a moment later gets nowhere)."""
import socket

import pytest
import requests

from tender_digest import publicnet


def answer(*addresses):
    return lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]


@pytest.mark.parametrize("ip,ok", [("93.184.216.34", True), ("2606:2800:220:1:248:1893:25c8:1946", True), ("10.0.0.5", False), ("192.168.1.1", False), ("172.16.0.9", False),
                                   ("127.0.0.1", False), ("169.254.169.254", False), ("0.0.0.0", False), ("::1", False), ("fe80::1%eth0", False), ("::ffff:10.0.0.5", False), ("224.0.0.1", False)])
def test_which_addresses_count_as_public(ip, ok):
    assert publicnet.is_public_address(ip) is ok


def connect_to(monkeypatch, *addresses):
    """Tries an https call to a name that resolves to `addresses`. Returns the address that was connected to (None if refused) and what was raised."""
    used, boom = [], None
    monkeypatch.setattr(publicnet.socket, "getaddrinfo", answer(*addresses))
    monkeypatch.setattr(publicnet, "create_connection", lambda address, *a, **k: (used.append(address), (_ for _ in ()).throw(OSError("stop here: nothing to talk to")))[1])
    try:
        publicnet.public_session().post("https://hooks.example.com/x", json={}, timeout=2)
    except requests.RequestException as e:
        boom = e
    return (used[0] if used else None), boom


def test_a_name_that_resolves_to_a_private_address_is_never_connected_to(monkeypatch):
    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.0.10"):
        used, boom = connect_to(monkeypatch, private)
        assert used is None and "private network" in str(boom)


def test_a_name_with_even_one_private_address_among_public_ones_is_refused(monkeypatch):
    used, boom = connect_to(monkeypatch, "93.184.216.34", "10.0.0.5")
    assert used is None and "private network" in str(boom)


def test_a_public_name_is_connected_to_by_the_address_that_was_checked(monkeypatch):
    used, _ = connect_to(monkeypatch, "93.184.216.34")
    assert used == ("93.184.216.34", 443)                            # the checked address itself, not a second lookup of the name


def test_plain_http_is_not_contacted_at_all(monkeypatch):
    monkeypatch.setattr(publicnet.socket, "getaddrinfo", answer("93.184.216.34"))
    with pytest.raises(requests.ConnectionError, match="https"):
        publicnet.public_session().post("http://hooks.example.com/x", json={}, timeout=2)
