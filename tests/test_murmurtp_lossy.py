"""Virtual-time impairment checks and real localhost UDP integration."""

import asyncio
import heapq
from unittest.mock import Mock

import pytest
from hypothesis import given, settings, strategies as st

from murmurtp.header import DATA, Header, decode_packet, encode_packet
from murmurtp.lossy import LossConfig, LossyDatagramTransport


class Clock:
    def __init__(self):
        self.now, self.serial, self.queue = 0.0, 0, []

    def call_later(self, delay, callback):
        handle = asyncio.Handle(callback, (), self)
        self.serial += 1
        heapq.heappush(self.queue, (self.now + delay, self.serial, handle))
        return handle

    def get_debug(self):
        return False

    def run(self):
        while self.queue:
            self.now, _, handle = heapq.heappop(self.queue)
            if not handle.cancelled():
                handle._callback(*handle._args)


def rig(config=LossConfig(), seed=0):
    clock, delivered = Clock(), []
    raw = Mock()
    raw.is_closing.return_value = False
    raw.sendto.side_effect = lambda data, addr: delivered.append((clock.now, data, addr))
    shim = LossyDatagramTransport(raw, config, seed=seed, loop=clock)
    return shim, raw, clock, delivered


def test_no_impairments_and_complete_loss():
    for loss in (0, 1):
        shim, raw, clock, delivered = rig(LossConfig(loss=loss))
        shim.sendto(b"hello", ("127.0.0.1", 6882))
        clock.run()
        assert len(delivered) == 1 - loss
        if delivered:
            assert delivered[0] == (0, b"hello", ("127.0.0.1", 6882))
        assert shim.stats()["dropped"] == loss
        shim.close()
        shim.close()
        raw.close.assert_called_once()
        with pytest.raises(RuntimeError, match="closed"):
            shim.sendto(b"after close")


def test_seeded_drop_rate_and_repeatability():
    traces = []
    for seed in (42, 42, 43):
        shim, _, clock, delivered = rig(LossConfig(loss=0.2), seed)
        for index in range(10000):
            shim.sendto(index.to_bytes(2, "big"))
        clock.run()
        assert abs(shim.stats()["dropped"] / 10000 - 0.2) < 0.02
        assert shim.stats()["attempted"] == 10000
        assert shim.stats()["sent"] + shim.stats()["dropped"] == 10000
        traces.append(delivered)
    assert traces[0] == traces[1]
    assert traces[0] != traces[2]


def test_delay_jitter_duplicates_and_reordering():
    config = LossConfig(delay=0.2, jitter=0.05, reorder=0.5,
                        reorder_delay=0.5, duplicate=1)
    traces = []
    for _ in range(2):
        shim, _, clock, delivered = rig(config, seed=123)
        for index in range(30):
            shim.sendto(bytes([index]), ("127.0.0.1", 6882))
        assert not delivered
        clock.run()
        assert len(delivered) == 60
        assert shim.stats()["duplicated"] == 30
        assert 0 < shim.stats()["reordered"] < 30
        order = [data[0] for _, data, _ in delivered]
        assert order != sorted(order)
        assert all(order.count(index) == 2 for index in range(30))
        assert all(0.15 <= t <= 0.25 or 0.65 <= t <= 0.75 for t, _, _ in delivered)
        traces.append(delivered)
    assert traces[0] == traces[1]


def test_reordering_alone_and_final_packet_are_delivered():
    shim, _, clock, delivered = rig(LossConfig(reorder=0.5, reorder_delay=0.1), seed=123)
    for index in range(30):
        shim.sendto(bytes([index]))
    clock.run()
    order = [data[0] for _, data, _ in delivered]
    assert sorted(order) == list(range(30))
    assert order != sorted(order)
    assert shim.stats()["pending"] == 0


def test_close_cancels_pending_copies():
    shim, raw, clock, delivered = rig(LossConfig(delay=1, duplicate=1))
    shim.sendto(b"pending")
    assert shim.stats()["pending"] == 2
    shim.close()
    clock.run()
    assert not delivered
    assert shim.stats()["cancelled"] == 2
    assert shim.stats()["pending"] == 0
    raw.close.assert_called_once()


def test_send_errors_are_visible():
    shim, raw, _, _ = rig()
    raw.sendto.side_effect = OSError("socket error")
    with pytest.raises(OSError, match="socket error"):
        shim.sendto(b"hello")
    assert shim.stats()["send_errors"] == 1
    assert shim.stats()["sent"] == 0


def test_invalid_configuration():
    for name in ("loss", "delay", "jitter", "reorder", "duplicate", "reorder_delay"):
        for value in (-1, float("inf"), float("nan"), True, "0.1"):
            with pytest.raises(ValueError, match=name):
                LossConfig(**{name: value})
    for name in ("loss", "reorder", "duplicate"):
        with pytest.raises(ValueError, match=name):
            LossConfig(**{name: 1.1})
    with pytest.raises(ValueError, match="reorder_delay"):
        LossConfig(reorder=1, reorder_delay=0)


class Receiver(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()
        self.closed = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr):
        self.messages.put_nowait((data, addr))

    def connection_lost(self, exc):
        if not self.closed.done():
            self.closed.set_result(None)


async def test_real_udp_round_trip_preserves_header_and_source_address():
    loop = asyncio.get_running_loop()
    endpoints = []
    try:
        for _ in range(2):
            raw, protocol = await loop.create_datagram_endpoint(
                Receiver, local_addr=("127.0.0.1", 0))
            endpoints.append((LossyDatagramTransport(raw, LossConfig(delay=0.01)), protocol))
        sender, sender_protocol = endpoints[0]
        receiver, receiver_protocol = endpoints[1]
        packet = encode_packet(Header(DATA, conn_id=3, seq_num=8), b"payload")
        sender.sendto(packet, receiver.get_extra_info("sockname"))
        data, addr = await asyncio.wait_for(receiver_protocol.messages.get(), 3)
        assert data == packet
        assert decode_packet(data) == (Header(DATA, conn_id=3, seq_num=8), b"payload")
        assert addr == sender.get_extra_info("sockname")
        receiver.sendto(b"reply", addr)
        reply, _ = await asyncio.wait_for(sender_protocol.messages.get(), 3)
        assert reply == b"reply"
    finally:
        for shim, _ in endpoints:
            shim.close()
        await asyncio.wait_for(asyncio.gather(*(p.closed for _, p in endpoints)), 3)


@settings(max_examples=60, derandomize=True, database=None, deadline=None)
@given(st.binary(min_size=1, max_size=1200), st.integers(0, 100000),
       st.floats(0, 1), st.floats(0, 1), st.floats(0, 1))
def test_random_impairments_never_modify_bytes(payload, seed, loss, duplicate, reorder):
    shim, _, clock, delivered = rig(
        LossConfig(loss=loss, duplicate=duplicate, reorder=reorder, jitter=0.01), seed)
    for _ in range(10):
        shim.sendto(payload, ("127.0.0.1", 6882))
    clock.run()
    assert all(data == payload and addr == ("127.0.0.1", 6882) and t >= 0
               for t, data, addr in delivered)
    stats = shim.stats()
    assert len(delivered) == 10 - stats["dropped"] + stats["duplicated"]
    assert stats["pending"] == 0
