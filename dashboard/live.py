"""Incremental reader for append-only Contract 3 logs from one run."""
import json
import math
import uuid
from pathlib import Path


class RunLog:
    def __init__(self, root: Path):
        self.root = root
        self.reset()

    def reset(self):
        self.revision = uuid.uuid4().hex
        self.files = {}
        self.rows = []
        self.invalid = 0

    def refresh(self):
        paths = {}
        for path in self.root.rglob("events-*.jsonl"):
            try:
                paths[path] = path.stat()
            except FileNotFoundError:
                continue
        if any(p not in paths or paths[p].st_ino != inode or paths[p].st_size < offset
               for p, (inode, offset) in self.files.items()):
            self.reset()
        fresh = []
        for path, stat in sorted(paths.items()):
            _, offset = self.files.get(path, (stat.st_ino, 0))
            if stat.st_size == offset:
                continue
            with path.open("rb") as stream:
                stream.seek(offset)
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    offset += len(line)
                    try:
                        row = json.loads(line)
                        if (not isinstance(row, dict) or not isinstance(row.get("node"), str)
                                or not isinstance(row.get("type"), str)
                                or not isinstance(row.get("t"), (float, int))
                                or not math.isfinite(row["t"])):
                            raise ValueError("invalid event header")
                        json.dumps(row, allow_nan=False)
                    except (ValueError, UnicodeDecodeError, OverflowError):
                        self.invalid += 1
                        continue
                    row["source"] = path.relative_to(self.root).as_posix()
                    fresh.append(row)
            self.files[path] = (stat.st_ino, offset)
        self.rows.extend(sorted(fresh, key=lambda row: row["t"]))

    def page(self, since, revision):
        self.refresh()
        if (revision and revision != self.revision) or since > len(self.rows):
            since = 0
        end = min(since + 2048, len(self.rows))
        return dict(revision=self.revision, events=self.rows[since:end], cursor=end,
                    more=end < len(self.rows), invalid=self.invalid)
