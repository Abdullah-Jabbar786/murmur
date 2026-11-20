"""Keep a listening swarm peer registered with the tracker while it serves."""
import asyncio
import sys

from tracker.client import TrackerError
from tracker.protocol import Peer


async def serve_discovered(node, client, timeout, event_log=None, *, single_session=False):
    peer = Peer(node.peer_id, *node.address)
    completion = asyncio.create_task(node.wait_complete(timeout))
    event = "started"
    announced_complete = node.store.complete
    request = None
    dialed = set()
    try:
        while True:
            if completion.done():
                completion.result()  # Propagate transfer failures and deadlines.
            try:
                request = asyncio.create_task(client.announce(peer, left=node.store.left, event=event))
                result = await asyncio.shield(request)
            except TrackerError as exc:
                print(f"Tracker retry: {exc}", file=sys.stderr, flush=True)
                await asyncio.sleep(1)
                continue
            if event and event_log is not None:
                event_log.emit("tracker", event=event, peers=len(result.peers))
            if event == "completed":
                announced_complete = True
            event = ""
            for remote in result.peers:
                if remote.peer_id == peer.peer_id or remote.peer_id in node.peer_ids:
                    continue
                address = (remote.ip, remote.port)
                if single_session:
                    # MurmurTP v1 supports one session per remote endpoint.
                    # Both nodes use the same endpoint ordering to elect one dialer.
                    if node.address >= address or address in dialed:
                        continue
                    dialed.add(address)
                try:
                    await node.connect(address)
                except (OSError, ValueError, EOFError, asyncio.TimeoutError) as exc:
                    print(f"Peer {remote.ip}:{remote.port}: {exc}", file=sys.stderr, flush=True)
            if announced_complete:
                await asyncio.sleep(client.announce_interval)
            else:
                await asyncio.wait((completion,), timeout=client.announce_interval)
                if completion.done():
                    completion.result()
                    event = "completed"
    finally:
        completion.cancel()
        await asyncio.gather(completion, return_exceptions=True)
        # The HTTP call runs in a thread. Finish it before deregistering so
        # a late registration cannot put a stopped peer back into the swarm.
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
            try:
                await client.announce(peer, left=node.store.left, event="stopped")
            except TrackerError as exc:
                print(f"Tracker stop: {exc}", file=sys.stderr, flush=True)
