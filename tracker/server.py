"""In-memory, single-swarm Flask tracker for the routed lab."""

import argparse
import re
import threading
import time
from typing import Callable

from flask import Flask, jsonify, request

from tracker.protocol import PEER_TIMEOUT, Peer, parse_peer_id, validate_event


class TrackerState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._peers: dict[bytes, tuple[Peer, int, float]] = {}
        self._lock = threading.Lock()

    def announce(self, peer: Peer, *, left: int, event: str = "") -> dict:
        validate_event(event, left)
        with self._lock:
            now = self._clock()
            expired = [key for key, (_, _, seen) in self._peers.items()
                       if now - seen >= PEER_TIMEOUT]
            for key in expired:
                del self._peers[key]
            if event == "stopped":
                self._peers.pop(peer.peer_id, None)
            else:
                self._peers[peer.peer_id] = (peer, left, now)
            peers = [entry.to_dict() for key, (entry, _, _) in sorted(self._peers.items())
                     if key != peer.peer_id]
            seeds = sum(left == 0 for _, left, _ in self._peers.values())
            return {"peers": peers, "seeds": seeds, "leechers": len(self._peers) - seeds}


def _decimal(value: str, name: str) -> int:
    if re.fullmatch(r"[0-9]{1,20}", value) is None:
        raise ValueError(f"{name} must contain 1..20 decimal digits")
    return int(value)


def create_app(*, clock: Callable[[], float] = time.monotonic) -> Flask:
    app = Flask(__name__)
    state = TrackerState(clock)
    app.extensions["murmur_tracker"] = state

    @app.get("/announce")
    def announce():
        try:
            required = {"peer_id", "ip", "port", "left"}
            if not required <= request.args.keys() or set(request.args) - required - {"event"}:
                raise ValueError("expected peer_id, ip, port, left and optional event")
            if any(len(request.args.getlist(key)) != 1 for key in request.args):
                raise ValueError("duplicate query parameters are not allowed")
            peer = Peer(parse_peer_id(request.args["peer_id"]), request.args["ip"],
                        _decimal(request.args["port"], "port"))
            result = state.announce(peer, left=_decimal(request.args["left"], "left"),
                                    event=request.args.get("event", ""))
            response = jsonify(result)
        except ValueError as exc:
            response = jsonify({"error": str(exc)})
            response.status_code = 400
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in [1, 65535]")
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False,
                     threaded=True)


if __name__ == "__main__":
    main()
