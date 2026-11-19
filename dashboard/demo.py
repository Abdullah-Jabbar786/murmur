"""A real local swarm for dashboard review. Files and ports are private to the run."""
import argparse
import asyncio
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path

from core.events import EventLog
from core.transports import create_transport
from peer.metadata import generate_dataset
from peer.storage import PieceStore
from peer.swarm import SwarmNode


async def transfer(root, protocol):
    os.environ["MURMUR_T0"] = str(time.time())
    payload = root / "seed" / "payload.bin"
    metadata = generate_dataset(payload, root / "metadata.json",
                                size=8 * 1024 * 1024, piece_length=32 * 1024)
    nodes = []
    async with AsyncExitStack() as resources:
        for index, name in enumerate(("seed", "leecher-1", "leecher-2")):
            folder = root / name
            folder.mkdir(exist_ok=True)
            store = resources.enter_context(PieceStore(folder / "payload.bin", metadata,
                                                        mode="seed" if index == 0 else "create"))
            log = resources.enter_context(EventLog("127.0.0.1", str(folder)))
            node = SwarmNode(store, transport=create_transport({"transport": protocol}),
                             event_log=log, is_seed=index == 0, sample_interval=0.25)
            resources.push_async_callback(node.close)
            await node.start(("127.0.0.1", 0))
            nodes.append(node)
        await nodes[2].connect(nodes[1].address)
        await nodes[1].connect(nodes[0].address)
        await asyncio.gather(*(n.wait_complete(150) for n in nodes[1:]))
        for node in nodes[1:]:
            if not node.store.complete or node.store.left:
                raise RuntimeError("Unverified data at completion")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("protocol", choices=["tcp", "murmurtp"])
    args = parser.parse_args()
    asyncio.run(transfer(args.directory, args.protocol))
