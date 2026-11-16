"""Tracker values shared by the HTTP server and async client."""

from dataclasses import dataclass
from ipaddress import IPv4Address
import re

ANNOUNCE_INTERVAL = 30
PEER_TIMEOUT = 90


def nonnegative(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def parse_peer_id(value: str) -> bytes:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{40}", value) is None:
        raise ValueError("peer_id must be 40 hex characters (20 bytes)")
    return bytes.fromhex(value)


def validate_event(event: str, left: int) -> None:
    nonnegative(left, "left")
    if event not in ("", "started", "completed", "stopped"):
        raise ValueError("event must be started, completed, stopped, or empty")
    if event == "completed" and left != 0:
        raise ValueError("completed requires left=0")


@dataclass(frozen=True)
class Peer:
    peer_id: bytes
    ip: str
    port: int

    def __post_init__(self) -> None:
        if not isinstance(self.peer_id, bytes) or len(self.peer_id) != 20:
            raise ValueError("peer_id must contain 20 immutable bytes")
        if not isinstance(self.ip, str):
            raise ValueError("ip must be a numeric IPv4 string")
        address = IPv4Address(self.ip)
        if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
            raise ValueError("ip must be a unicast IPv4 address")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer in [1, 65535]")

    def to_dict(self) -> dict:
        return {"peer_id": self.peer_id.hex(), "ip": self.ip, "port": self.port}
