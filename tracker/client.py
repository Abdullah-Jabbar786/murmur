"""Async announce calls; the peer loop owns the 30-second refresh schedule."""

import asyncio
from dataclasses import dataclass
from http.client import HTTPException
import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from tracker.protocol import ANNOUNCE_INTERVAL, Peer, nonnegative, parse_peer_id, validate_event

MAX_RESPONSE_BYTES = 1024 * 1024


class TrackerError(Exception):
    """An HTTP/network failure or invalid tracker response."""


@dataclass(frozen=True)
class AnnounceResult:
    peers: tuple[Peer, ...]
    seeds: int
    leechers: int


def _decode_response(raw: bytes) -> AnnounceResult:
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or set(value) != {"peers", "seeds", "leechers"}:
            raise ValueError("expected peers, seeds, leechers")
        nonnegative(value["seeds"], "seeds")
        nonnegative(value["leechers"], "leechers")
        if not isinstance(value["peers"], list):
            raise ValueError("peers must be a list")
        peers = []
        seen = set()
        for entry in value["peers"]:
            if not isinstance(entry, dict) or set(entry) != {"peer_id", "ip", "port"}:
                raise ValueError("invalid peer entry")
            peer = Peer(parse_peer_id(entry["peer_id"]), entry["ip"], entry["port"])
            if peer.peer_id in seen:
                raise ValueError("duplicate peer_id")
            seen.add(peer.peer_id)
            peers.append(peer)
        if len(peers) > value["seeds"] + value["leechers"]:
            raise ValueError("peer count exceeds swarm counts")
        return AnnounceResult(tuple(peers), value["seeds"], value["leechers"])
    except (ValueError, TypeError, RecursionError) as exc:
        raise TrackerError(f"invalid tracker response: {exc}") from exc


class TrackerClient:
    announce_interval = ANNOUNCE_INTERVAL

    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.query or parts.fragment or parts.username or parts.password):
            raise ValueError("tracker URL must be HTTP(S), without credentials, query or fragment")
        if parts.port == 0:  # Access also validates malformed/out-of-range URL ports.
            raise ValueError("tracker port must be in [1, 65535]")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be positive and finite")
        self.url, self.timeout = url, timeout

    async def announce(self, peer: Peer, *, left: int, event: str = "") -> AnnounceResult:
        validate_event(event, left)
        if left >= 10**20:
            raise ValueError("left must fit 20 decimal digits")
        query = {**peer.to_dict(), "left": left, "event": event}
        return await asyncio.to_thread(self._request, self.url + "?" + urlencode(query))

    def _request(self, url: str) -> AnnounceResult:
        # Namespace/lab addresses must be contacted directly, without environment proxies.
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(Request(url, headers={"Accept": "application/json"}),
                             timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise TrackerError(f"tracker returned HTTP {status}") from exc
        except (OSError, URLError, ValueError, HTTPException) as exc:
            raise TrackerError(f"tracker request failed: {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TrackerError("tracker response exceeds size limit")
        return _decode_response(raw)
