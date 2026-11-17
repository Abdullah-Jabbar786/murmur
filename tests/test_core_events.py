"""Unit tests for Contract 3 EventLog schema and configuration loader."""

import json
import os
import time
import pytest

from core.events import EventLog
from core.config import load_config


def test_event_log_emit(tmp_path):
    run_dir = str(tmp_path / "run_01")
    node = "10.0.1.11"

    with EventLog(node, run_dir) as log:
        event = log.emit("have", piece=42)
        assert event["node"] == node
        assert event["type"] == "have"
        assert event["piece"] == 42
        assert "t" in event

        # Emit transport stat
        t_event = log.emit(
            "transport",
            peer="10.0.2.12",
            proto="murmurtp",
            cwnd_bytes=14400,
            rtt_ms=104.2,
            retx=3,
        )
        assert t_event["cwnd_bytes"] == 14400
        assert t_event["rtt_ms"] == 104.2

    # Verify JSONL file on disk
    file_path = os.path.join(run_dir, f"events-{node}.jsonl")
    assert os.path.exists(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) == 2
    assert lines[0]["type"] == "have"
    assert lines[0]["piece"] == 42
    assert lines[1]["type"] == "transport"
    assert lines[1]["peer"] == "10.0.2.12"


def test_event_log_with_murmur_t0(tmp_path, monkeypatch):
    run_dir = str(tmp_path / "run_t0")
    node = "10.0.1.12"
    fixed_epoch = time.time() - 15.5  # 15.5 seconds ago

    monkeypatch.setenv("MURMUR_T0", str(fixed_epoch))

    with EventLog(node, run_dir) as log:
        event = log.emit("join")
        # Elapsed time should be approx 15.5 seconds
        assert 15.0 <= event["t"] <= 17.0


def test_config_loader(tmp_path):
    # Test loading default config
    cfg = load_config()
    assert cfg["transport"] == "tcp"
    assert cfg["piece_size"] == 262144
    assert cfg["block_size"] == 16384

    # Test overrides
    custom_cfg = load_config(overrides={"transport": "murmurtp", "unchoke_slots": 8})
    assert custom_cfg["transport"] == "murmurtp"
    assert custom_cfg["unchoke_slots"] == 8
    assert custom_cfg["piece_size"] == 262144  # unchanged default
