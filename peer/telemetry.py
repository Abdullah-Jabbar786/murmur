"""Contract 3 event hooks and sampling for a SwarmNode."""

import asyncio
import time


class PeerEvents:
    """The node owns this sampler; the caller owns the supplied EventLog."""

    def __init__(self, node, event_log, *, interval=1.0, clock=time.monotonic):
        self.node, self.log = node, event_log
        self.interval, self.clock = interval, clock
        self.error = None
        self._started = self._finished = self._complete = False
        self._task = None
        self._connections = {}
        self._next_id = 0
        self._closed_retx = 0
        self._unknown_retx = False
        self._last_time = clock()
        self._last_down = self._last_up = 0

    def emit(self, kind, **fields):
        if self.error is not None or self._finished:
            return
        try:
            self.log.emit(kind, **fields)
        except Exception as exc:
            # Logging failures must be visible without interrupting socket cleanup.
            self.error = exc
            self.node._complete.set()

    def raise_if_failed(self):
        if self.error is not None:
            raise self.error

    def start(self):
        if self._started:
            return
        self._started = True
        self._last_time = self.clock()
        self.emit("join", mode=self.node.mode, origin_seed=self.node.is_seed,
                  initial_complete=self.node.store.complete)
        for piece in sorted(self.node.store.owned_pieces):
            self.emit("have", piece=piece, initial=True)
        if self.node.store.complete:
            self.complete(initial=True)
        self.sample()
        if self.error is None:
            self._task = asyncio.create_task(self._run())

    def complete(self, *, initial=False):
        if not self._complete:
            self._complete = True
            self.emit("complete", initial=initial)

    def opened(self, connection):
        self.start()
        if connection not in self._connections:
            self._next_id += 1
            self._connections[connection] = self._next_id
            self._transport(connection)

    def _transport(self, connection):
        stats = connection.stats()
        self.emit("transport", peer=str(connection.peer_addr[0]),
                  peer_port=connection.peer_addr[1], connection_id=self._connections[connection],
                  proto=stats.get("proto"), cwnd_bytes=stats.get("cwnd_bytes"),
                  rtt_ms=stats.get("rtt_ms"), retx=stats.get("retx"),
                  bytes_in_flight=stats.get("bytes_in_flight"))
        return stats.get("retx")

    def closed(self, connection):
        if connection not in self._connections:
            return
        retx = self._transport(connection)
        del self._connections[connection]
        if retx is None:
            self._unknown_retx = True
        else:
            self._closed_retx += retx

    def sample(self):
        now = self.clock()
        elapsed = now - self._last_time
        down, up = self.node.bytes_received, self.node.bytes_sent
        self.emit("rate", down_bps=round(8 * (down - self._last_down) / elapsed) if elapsed > 0 else 0,
                  up_bps=round(8 * (up - self._last_up) / elapsed) if elapsed > 0 else 0)
        self._last_time, self._last_down, self._last_up = now, down, up
        for connection in list(self._connections):
            self._transport(connection)

    async def _run(self):
        try:
            while self.error is None:
                await asyncio.sleep(self.interval)
                self.sample()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
            self.node._complete.set()

    async def finish(self):
        if self._finished:
            return
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._started:
            self.sample()
            retx = self._closed_retx
            unknown = self._unknown_retx
            for connection in self._connections:
                value = connection.stats().get("retx")
                if value is None:
                    unknown = True
                else:
                    retx += value
            self.emit("summary", bytes_down=self.node.bytes_received, bytes_up=self.node.bytes_sent,
                      retx=None if unknown else retx, hash_fails=self.node.hash_failures,
                      complete=self.node.store.complete)
        self._finished = True
        self.raise_if_failed()
