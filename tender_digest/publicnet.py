"""Outgoing calls to an address an administrator typed (webhooks) that can only ever reach the public internet.

Checking the name first and connecting later leaves a gap: whoever controls a name's DNS can answer "public" to the check and "10.0.0.5" a moment later when the
connection is made. So the check is made at the moment of connecting, on the very address that is then used: every address the name gives must be public, and the
connection goes to one of those addresses, never to a second lookup of the name. The web address stays the name, so the certificate is still checked against it."""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.util.connection import create_connection


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])        # an IPv6 address may carry a zone ("fe80::1%eth0")
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


class _PublicHTTPSConnection(HTTPSConnection):
    def _new_conn(self) -> socket.socket:
        host = self._dns_host
        infos = socket.getaddrinfo(host, self.port, type=socket.SOCK_STREAM)
        addresses = [str(info[4][0]) for info in infos]
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise OSError("that address points to a private network, so it was not contacted")
        return create_connection((addresses[0], self.port), self.timeout, source_address=self.source_address, socket_options=self.socket_options)


class _PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {**self.poolmanager.pool_classes_by_scheme, "https": _PublicHTTPSPool}


def public_session() -> requests.Session:
    """A session that refuses, at connection time, to reach anything but public addresses. Only https is allowed: a plain http address is never contacted.
    It ignores HTTP(S)_PROXY from the environment: a proxy would do the name lookup itself, out of this check's sight."""
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", _PublicAdapter())
    session.mount("http://", _Refuse())
    return session


class _Refuse(HTTPAdapter):
    def send(self, *args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("Only https addresses are contacted")
