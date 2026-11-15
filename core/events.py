"""Contract 3: Event log schema and EventLog logger.

Every node (seed, leechers, tracker, and test harness) appends JSON objects
to results/<run-id>/events-<node>.jsonl.

Rules:
    - Every event has 't' (float seconds since MURMUR_T0), 'node', and 'type'.
    - Event types are only added, never renamed.
    - Each line is flushed immediately so the dashboard can tail the file.
"""

import json
import os
import time
from typing import Any, Dict, Optional


class EventLog:
    """Appends standardized JSONL events to a node's event file."""

    def __init__(self, node: str, run_dir: str) -> None:
        self.node = node
        self.run_dir = run_dir
        self.file_path = os.path.join(run_dir, f"events-{node}.jsonl")
        os.makedirs(run_dir, exist_ok=True)
        self._file = open(self.file_path, "a", encoding="utf-8")
        self._start_time = time.time()

    def _get_t(self) -> float:
        """Calculate elapsed seconds since MURMUR_T0 or instance start."""
        env_t0 = os.environ.get("MURMUR_T0")
        if env_t0 is not None:
            try:
                return round(time.time() - float(env_t0), 3)
            except ValueError:
                pass
        return round(time.time() - self._start_time, 3)

    def emit(self, event_type: str, **kwargs: Any) -> Dict[str, Any]:
        """Emit a structured event to the JSONL log file.

        Standard event types include:
            - join: {"type": "join"}
            - have: {"type": "have", "piece": int}
            - rate: {"type": "rate", "down_bps": int, "up_bps": int}
            - transport: {"type": "transport", "peer": str, "proto": str,
                          "cwnd_bytes": int, "rtt_ms": float, "retx": int}
            - unchoke: {"type": "unchoke", "peer": str}
            - choke: {"type": "choke", "peer": str}
            - hash_fail: {"type": "hash_fail", "piece": int}
            - complete: {"type": "complete"}
            - summary: {"type": "summary", "bytes_down": int, "bytes_up": int,
                        "retx": int, "hash_fails": int}
            - marker: {"type": "marker", "label": str}
        """
        event: Dict[str, Any] = {
            "t": self._get_t(),
            "node": self.node,
            "type": event_type,
        }
        event.update(kwargs)

        line = json.dumps(event) + "\n"
        self._file.write(line)
        self._file.flush()
        return event

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
