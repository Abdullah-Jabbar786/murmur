"""Execute the actual Lua dissector in TShark against deterministic pcapngs."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from captures.generate_sample import sample_packets, write_capture
from murmurtp.header import ACK, DATA, Header, encode_packet, internet_checksum


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("murmurtp.flags", "murmurtp.conn_id", "murmurtp.seq", "murmurtp.ack",
          "murmurtp.window", "murmurtp.sack", "murmurtp.sack_seq", "murmurtp.timestamp",
          "murmurtp.length", "murmurtp.checksum_good", "murmurtp.payload", "_ws.expert.message")


@pytest.fixture(scope="module")
def tshark():
    explicit = os.environ.get("MURMUR_TSHARK")
    executable = explicit or shutil.which("tshark")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark/tshark.exe"
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        if os.environ.get("MURMUR_REQUIRE_TSHARK") == "1":
            pytest.fail("TShark is required by this test environment")
        pytest.skip("Install Wireshark/TShark with Lua to run dissector acceptance")
    return executable


def decode(tshark, path, *extra):
    args = [tshark, "-n", "-r", str(path), "-X", f"lua_script:{ROOT / 'captures/murmurtp.lua'}",
            *extra, "-T", "fields"]
    for field in FIELDS:
        args.extend(("-e", field))
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Lua Error" not in result.stderr, result.stderr
    rows = [dict(zip(FIELDS, line.split("\t"))) for line in result.stdout.splitlines()]
    assert all("Lua Error" not in row["_ws.expert.message"] for row in rows)
    return rows


def test_sample_decodes_all_header_fields_and_sack_bits(tshark, tmp_path):
    rows = decode(tshark, write_capture(tmp_path / "sample.pcapng", sample_packets()))
    assert len(rows) == 9
    assert [row["murmurtp.flags"] for row in rows] == ["0x01", "0x03", "0x02", "0x08", "0x02", "0x08", "0x04", "0x02", "0x10"]
    assert all(row["murmurtp.checksum_good"] in ("True", "1") for row in rows)
    assert rows[0]["murmurtp.conn_id"] == "0" and rows[1]["murmurtp.conn_id"] == "7"
    assert rows[2]["murmurtp.seq"] == rows[2]["murmurtp.ack"] == "1"
    assert rows[3]["murmurtp.timestamp"] == "1234"
    assert bytes.fromhex(rows[3]["murmurtp.payload"]) == b"synthetic MurmurTP payload"
    assert rows[4]["murmurtp.sack"] == "0x80000005"
    assert rows[4]["murmurtp.sack_seq"] == "2,4,33"
    assert rows[4]["murmurtp.window"] == "63"
    assert rows[5]["murmurtp.length"] == "0"  # Valid persist probe.
    assert all(row["_ws.expert.message"] == "SYNTHETIC packet" for row in rows)


@pytest.mark.parametrize("payload", [b"", b"x", b"xy", bytes(range(256)) * 4 + b"z" * 176])
def test_checksum_matches_python_for_odd_even_and_max_payload(tshark, tmp_path, payload):
    packet = encode_packet(Header(DATA, 65535, 0xFFFFFFFF, 0xFFFFFFFF, 65535,
                                  timestamp=0xFFFFFFFF), payload)
    rows = decode(tshark, write_capture(tmp_path / "limits.pcapng", [packet]))
    assert rows[0]["murmurtp.checksum_good"] in ("True", "1")
    assert rows[0]["murmurtp.seq"] == rows[0]["murmurtp.ack"] == "4294967295"
    assert rows[0]["murmurtp.length"] == str(len(payload))


def test_corrupt_checksum_is_reported(tshark, tmp_path):
    packet = bytearray(encode_packet(Header(DATA), b"abc"))
    packet[-1] ^= 1
    row = decode(tshark, write_capture(tmp_path / "corrupt.pcapng", [bytes(packet)]))[0]
    assert row["murmurtp.checksum_good"] in ("False", "0")
    assert "checksum mismatch" in row["_ws.expert.message"]


@pytest.mark.parametrize("size", [1, 10, 24])
def test_short_header_does_not_throw_lua_range_error(tshark, tmp_path, size):
    row = decode(tshark, write_capture(tmp_path / "short.pcapng", [b"\0" * size]))[0]
    assert "Truncated MurmurTP" in row["_ws.expert.message"]
    assert not row["murmurtp.checksum_good"]


@pytest.mark.parametrize("change", ["missing", "extra", "oversize"])
def test_bad_payload_length_is_not_checksum_verified(tshark, tmp_path, change):
    packet = encode_packet(Header(DATA), b"abc")
    if change == "missing":
        packet = packet[:-1]
    elif change == "extra":
        packet += b"x"
    else:
        packet = packet[:21] + (1201).to_bytes(2, "big") + packet[23:25] + b"x" * 1201
    row = decode(tshark, write_capture(tmp_path / "length.pcapng", [packet]))[0]
    assert "Invalid MurmurTP payload length" in row["_ws.expert.message"]
    assert not row["murmurtp.checksum_good"]


def test_capture_truncation_is_distinct_from_corrupt_packet(tshark, tmp_path):
    packet = encode_packet(Header(DATA), b"abcdef")
    row = decode(tshark, write_capture(tmp_path / "snaplen.pcapng", [packet], truncate=2))[0]
    assert "Truncated MurmurTP" in row["_ws.expert.message"]
    assert "checksum mismatch" not in row["_ws.expert.message"]
    assert not row["murmurtp.checksum_good"]


def test_unknown_flags_and_control_payload_are_visible(tshark, tmp_path):
    packet = bytearray(encode_packet(Header(DATA), b"x"))
    packet[0] = 0x80
    packet[23:25] = b"\0\0"
    packet[23:25] = internet_checksum(bytes(packet)).to_bytes(2, "big")
    row = decode(tshark, write_capture(tmp_path / "flags.pcapng", [bytes(packet)]))[0]
    assert row["murmurtp.flags"] == "0x80" and row["murmurtp.checksum_good"] in ("True", "1")
    assert "flag combination" in row["_ws.expert.message"]
    assert "Payload on a control" in row["_ws.expert.message"]


def test_out_of_range_sack_is_reported_without_lua_exception(tshark, tmp_path):
    packet = encode_packet(Header(ACK, cum_ack=0xFFFFFFFF, sack_mask=1))
    row = decode(tshark, write_capture(tmp_path / "sack.pcapng", [packet]))[0]
    assert "SACK sequence exceeds" in row["_ws.expert.message"]
    assert not row["murmurtp.sack_seq"]


def test_decode_as_for_nondefault_port(tshark, tmp_path):
    path = write_capture(tmp_path / "other-port.pcapng", [encode_packet(Header(ACK, 23))], port=16882)
    assert not decode(tshark, path)[0]["murmurtp.conn_id"]
    assert decode(tshark, path, "-d", "udp.port==16882,murmurtp")[0]["murmurtp.conn_id"] == "23"
