"""One verified bulk flow through Contract 2; local foundation for E4/E5."""

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import struct
import time

from core.asyncio_utils import wait_for_cancel_safe
from core.events import EventLog
from core.transports import create_transport, transport_port


CHUNK = 64 * 1024
HEADER = struct.Struct("!8sQ32s")
MAGIC, RECEIPT = b"MRFLOW01", b"MRDONE01"
DEFAULT_LIMIT = 1024 * 1024 * 1024


class FlowError(ValueError):
    """Malformed benchmark framing, truncated data, or failed verification."""


class IntegrityError(FlowError):
    """The delivered bytes did not match the declared SHA-256."""


async def read_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = await connection.recv(size - len(data))
        if not chunk:
            raise FlowError("premature EOF")
        if len(chunk) > size - len(data):
            raise FlowError("transport exceeded requested read size")
        data.extend(chunk)
    return bytes(data)


def _result(role, size, digest, started):
    elapsed = time.monotonic() - started
    return dict(role=role, bytes=size, sha256=digest.hex(), seconds=elapsed,
                goodput_mbps=size * 8 / elapsed / 1_000_000, verified=True)


async def send_file(connection, source, progress):
    """Stream an existing file with bounded buffers; require receiver receipt."""
    with Path(source).open("rb") as stream:
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            await asyncio.sleep(0)
        if not size:
            raise FlowError("source file must be nonempty")
        expected = digest.digest()
        stream.seek(0)
        await connection.send(HEADER.pack(MAGIC, size, expected))
        if await read_exact(connection, 1) != b"\x01":
            raise FlowError("receiver did not acknowledge readiness")
        started, sent, actual = time.monotonic(), 0, hashlib.sha256()
        while sent < size:
            chunk = stream.read(min(CHUNK, size - sent))
            if not chunk:
                raise IntegrityError("source shortened during transfer")
            await connection.send(chunk)
            sent += len(chunk)
            actual.update(chunk)
            progress.up += len(chunk)
        if stream.read(1) or actual.digest() != expected:
            raise IntegrityError("source changed during transfer")
        magic, received, digest = HEADER.unpack(await read_exact(connection, HEADER.size))
        if magic != RECEIPT or received != size or digest != expected:
            raise IntegrityError("receiver receipt did not match source")
        return _result("sender", size, expected, started)


async def receive_stream(connection, max_bytes, progress):
    """Hash and discard one length-delimited flow; acknowledge only verified data."""
    magic, size, expected = HEADER.unpack(await read_exact(connection, HEADER.size))
    if magic != MAGIC or not 0 < size <= max_bytes:
        raise FlowError("invalid flow header or size exceeds receiver limit")
    digest, received = hashlib.sha256(), 0
    started = time.monotonic()
    await connection.send(b"\x01")
    while received < size:
        limit = min(CHUNK, size - received)
        chunk = await connection.recv(limit)
        if not chunk or len(chunk) > limit:
            raise FlowError("premature EOF or oversized transport read")
        received += len(chunk)
        digest.update(chunk)
        progress.down += len(chunk)
    if digest.digest() != expected:
        raise IntegrityError("received SHA-256 mismatch")
    result = _result("receiver", size, expected, started)
    await connection.send(HEADER.pack(RECEIPT, received, expected))
    return result


class _Progress:
    def __init__(self, log, role, interval):
        self.log, self.role, self.interval = log, role, interval
        self.connection = None
        self.up = self.down = self._previous_up = self._previous_down = 0
        self._sample_at = time.monotonic()

    def sample(self):
        if self.log is None or self.connection is None:
            return
        now = time.monotonic()
        elapsed = max(now - self._sample_at, 1e-9)
        self.log.emit("rate", up_bps=(self.up - self._previous_up) * 8 / elapsed,
                      down_bps=(self.down - self._previous_down) * 8 / elapsed, role=self.role)
        stats = self.connection.stats()
        self.log.emit("transport", peer=self.connection.peer_addr[0], role=self.role,
                      **{key: stats.get(key) for key in
                         ("proto", "cwnd_bytes", "rtt_ms", "retx", "bytes_in_flight")})
        self._previous_up, self._previous_down, self._sample_at = self.up, self.down, now

    async def sample_loop(self):
        while True:
            self.sample()
            await asyncio.sleep(self.interval)


async def run_once(transport, address, *, source=None, timeout=120.0,
                   max_bytes=DEFAULT_LIMIT, event_log=None, sample_interval=1.0,
                   on_listening=None):
    """Own and close one endpoint. source=None receives; otherwise send that file.

    timeout covers setup, transfer and graceful close. Samples and summaries use
    EventLog; payload counts exclude the benchmark header/readiness/receipt.
    """
    progress = _Progress(event_log, "receiver" if source is None else "sender", sample_interval)
    result, failure = None, None

    async def operation():
        if source is None:
            await transport.listen(address)
            if on_listening is not None:
                on_listening(getattr(transport, "local_addr", None) or address)
            connection = await transport.accept()
        else:
            connection = await transport.connect(address)
        progress.connection = connection
        if event_log is not None:
            event_log.emit("join", role=progress.role, proto=connection.stats().get("proto"))

        async def transfer():
            report = (await receive_stream(connection, max_bytes, progress) if source is None
                      else await send_file(connection, source, progress))
            if event_log is not None:
                event_log.emit("complete", **report)
            await connection.close()
            return report

        work = asyncio.create_task(transfer())
        sampler = asyncio.create_task(progress.sample_loop())
        try:
            done, _ = await asyncio.wait((work, sampler), return_when=asyncio.FIRST_COMPLETED)
            if sampler in done:
                sampler.result()  # Never silently discard telemetry failures.
                raise RuntimeError("metric sampler stopped unexpectedly")
            return work.result()
        finally:
            for task in (work, sampler):
                task.cancel()
            await asyncio.gather(work, sampler, return_exceptions=True)

    try:
        for value in (timeout, sample_interval):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("timeout and sample_interval must be finite and positive")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        result = await wait_for_cancel_safe(operation(), timeout)
        return result
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await transport.close()
        except BaseException as exc:
            result = None
            failure = failure or exc
            raise
        finally:
            if event_log is not None:
                progress.sample()
                stats = progress.connection.stats() if progress.connection is not None else {}
                event_log.emit("summary", role=progress.role, complete=result is not None,
                    bytes_up=progress.up, bytes_down=progress.down, retx=stats.get("retx"),
                    hash_fails=int(isinstance(failure, IntegrityError)), result=result,
                    error=None if failure is None else type(failure).__name__ + ": " + str(failure))


async def _cli(args):
    config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    if args.transport is not None:
        config["transport"] = args.transport
    port = transport_port(config, args.port)
    if args.command == "send" and port == 0:
        raise ValueError("sender requires a nonzero destination port")
    transport = create_transport(config)
    with EventLog(f"flow-{args.command}", args.run_dir) as log:
        result = await run_once(transport, (args.host, port), source=args.file,
            timeout=args.timeout, max_bytes=args.max_bytes, event_log=log,
            on_listening=lambda address: print(f"Listening on {address[0]}:{address[1]}", flush=True))
    print(json.dumps(result, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("receive", "send"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--host", default="127.0.0.1")
        sub.add_argument("--port", type=int)
        sub.add_argument("--config", type=Path)
        sub.add_argument("--transport", choices=("tcp", "murmurtp"))
        sub.add_argument("--run-dir", required=True)
        sub.add_argument("--timeout", type=float, default=120.0)
        sub.add_argument("--max-bytes", type=int, default=DEFAULT_LIMIT)
        if command == "send":
            sub.add_argument("--file", type=Path, required=True)
        else:
            sub.set_defaults(file=None)
    args = parser.parse_args()
    try:
        asyncio.run(_cli(args))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"flowbench: {exc}\n")


if __name__ == "__main__":
    main()
