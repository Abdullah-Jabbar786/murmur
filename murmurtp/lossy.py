"""Seeded outgoing UDP impairments for local MurmurTP reliability tests."""

import asyncio
from dataclasses import dataclass
import math
import random
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class LossConfig:
    """Rates are probabilities (0..1); delay, jitter and reorder_delay are seconds."""

    loss: float = 0.0
    delay: float = 0.0
    jitter: float = 0.0
    reorder: float = 0.0
    duplicate: float = 0.0
    reorder_delay: float = 0.05

    def __post_init__(self) -> None:
        for name in ("loss", "delay", "jitter", "reorder", "duplicate", "reorder_delay"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite nonnegative number")
            if name in ("loss", "reorder", "duplicate") and value > 1:
                raise ValueError(f"{name} must be a probability in [0, 1]")
        if self.reorder and not self.reorder_delay:
            raise ValueError("reorder_delay must be positive when reordering is enabled")


class LossyDatagramTransport:
    """Wrap an asyncio UDP transport's sendto; receive via its existing protocol.

    Use one wrapper per endpoint, on the same event loop as the raw transport.
    A seed repeats impairment choices, not OS scheduling or network delivery.
    """

    def __init__(self, transport: asyncio.DatagramTransport,
                 config: Optional[LossConfig] = None, *, seed: int = 0,
                 loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("seed must be an integer")
        self.config = config if config is not None else LossConfig()
        self._transport = transport
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._rng = random.Random(seed)
        self._pending: set = set()
        self._closed = False
        self._counts = dict(attempted=0, dropped=0, duplicated=0, reordered=0,
                            sent=0, cancelled=0, send_errors=0)

    def sendto(self, data: bytes, addr: Optional[Tuple[str, int]] = None) -> None:
        """Drop, or schedule one/two identical copies; never block or alter bytes."""
        if self.is_closing():
            raise RuntimeError("Lossy transport is closed")
        if not isinstance(data, bytes):
            raise TypeError("data must be immutable bytes")
        self._counts["attempted"] += 1
        cfg = self.config
        if self._rng.random() < cfg.loss:
            self._counts["dropped"] += 1
            return
        duplicate = self._rng.random() < cfg.duplicate
        reorder = self._rng.random() < cfg.reorder
        delay = max(0.0, cfg.delay + self._rng.uniform(-cfg.jitter, cfg.jitter))
        if reorder:
            delay += cfg.reorder_delay
        self._counts["duplicated"] += int(duplicate)
        self._counts["reordered"] += int(reorder)
        for _ in range(1 + int(duplicate)):
            self._schedule(data, addr, delay)

    def _schedule(self, data: bytes, addr: Optional[Tuple[str, int]], delay: float) -> None:
        if delay == 0:
            self._emit(data, addr)
            return

        def deliver() -> None:
            self._pending.discard(handle)
            if self.is_closing():
                self._counts["cancelled"] += 1
            else:
                self._emit(data, addr)

        handle = self._loop.call_later(delay, deliver)
        self._pending.add(handle)

    def _emit(self, data: bytes, addr: Optional[Tuple[str, int]]) -> None:
        try:
            self._transport.sendto(data, addr)
        except OSError:
            self._counts["send_errors"] += 1
            raise  # Immediate errors reach the caller; timer errors reach asyncio.
        self._counts["sent"] += 1

    def stats(self) -> Dict[str, int]:
        """Snapshot: attempted/dropped count originals; sent/cancelled count copies."""
        return dict(self._counts, pending=len(self._pending))

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._transport.get_extra_info(name, default)

    def is_closing(self) -> bool:
        return self._closed or self._transport.is_closing()

    def close(self) -> None:
        """Cancel unsent copies and close the owned UDP transport; idempotent."""
        if not self._closed:
            self._closed = True
            for handle in self._pending:
                handle.cancel()
            self._counts["cancelled"] += len(self._pending)
            self._pending.clear()
            self._transport.close()
