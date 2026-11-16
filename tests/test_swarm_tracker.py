"""Tracker lifecycle, clock boundaries, validation, and real HTTP integration."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
from http.client import IncompleteRead
import json
import threading
from urllib.error import HTTPError, URLError

import pytest
from werkzeug.serving import make_server

from tracker import client as client_module
from tracker.client import MAX_RESPONSE_BYTES, TrackerClient, TrackerError, _decode_response
from tracker.protocol import ANNOUNCE_INTERVAL, PEER_TIMEOUT, Peer
from tracker.server import TrackerState, create_app

SEED = Peer(b"s" * 20, "127.0.0.1", 6881)
LEECHER = Peer(b"l" * 20, "127.0.0.2", 6881)


def query(peer=SEED, left=0, event="started"):
    return {**peer.to_dict(), "left": str(left), "event": event}


def test_registration_completion_stop_and_idempotency():
    client = create_app().test_client()
    response = client.get("/announce", query_string=query())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json == {"peers": [], "seeds": 1, "leechers": 0}
    response = client.get("/announce", query_string=query(LEECHER, 123))
    assert response.json == {"peers": [SEED.to_dict()], "seeds": 1, "leechers": 1}
    assert client.get("/announce", query_string=query(LEECHER, 123)).json == response.json
    response = client.get("/announce", query_string=query(LEECHER, 0, "completed"))
    assert response.json == {"peers": [SEED.to_dict()], "seeds": 2, "leechers": 0}
    for _ in range(2):
        response = client.get("/announce", query_string=query(SEED, 0, "stopped"))
        assert response.json == {"peers": [LEECHER.to_dict()], "seeds": 1, "leechers": 0}


def test_expiry_exact_boundary_and_refresh():
    now = [0.0]
    client = create_app(clock=lambda: now[0]).test_client()
    client.get("/announce", query_string=query())
    now[0] = 89.999
    assert client.get("/announce", query_string=query(LEECHER, 1)).json["seeds"] == 1
    now[0] = 90
    assert client.get("/announce", query_string=query(LEECHER, 1, "")).json["seeds"] == 0
    # Missing event is a regular refresh; refreshing extends the lease.
    client.get("/announce", query_string=query())
    now[0] = 120
    refresh = query()
    del refresh["event"]
    assert client.get("/announce", query_string=refresh).status_code == 200
    now[0] = 180
    assert client.get("/announce", query_string=query(LEECHER, 1)).json["seeds"] == 1
    now[0] = 210
    assert client.get("/announce", query_string=query(LEECHER, 1)).json["seeds"] == 0
    assert (ANNOUNCE_INTERVAL, PEER_TIMEOUT) == (30, 90)


def test_endpoint_updates_and_uppercase_id_is_same_peer():
    client = create_app().test_client()
    peer = Peer(bytes.fromhex("ab" * 20), "10.0.1.11", 6881)
    client.get("/announce", query_string=query(peer, 100))
    updated = {**query(peer, 10, ""), "peer_id": peer.peer_id.hex().upper(),
               "ip": "10.0.2.11", "port": "6882"}
    response = client.get("/announce", query_string=updated)
    assert response.json == {"peers": [], "seeds": 0, "leechers": 1}
    response = client.get("/announce", query_string=query())
    assert response.json["peers"] == [Peer(peer.peer_id, "10.0.2.11", 6882).to_dict()]


@pytest.mark.parametrize("field,value", [
    ("peer_id", "short"), ("peer_id", "z" * 40), ("peer_id", "a" * 42),
    ("ip", "hostname"), ("ip", "::1"), ("ip", "0.0.0.0"),
    ("ip", "224.0.0.1"), ("ip", "255.255.255.255"),
    ("port", "0"), ("port", "65536"), ("port", "-1"), ("port", "1.5"),
    ("left", "-1"), ("left", "NaN"), ("left", "1" * 21),
    ("left", ""), ("event", "unknown"), ("extra", "x"),
])
def test_bad_query_is_400_and_does_not_register(field, value):
    client = create_app().test_client()
    invalid = {**query(SEED, 10), field: value}
    response = client.get("/announce", query_string=invalid)
    assert response.status_code == 400 and "error" in response.json
    assert client.get("/announce", query_string=query(LEECHER, 1)).json["peers"] == []


def test_missing_duplicate_and_false_completion_rejected_without_mutation():
    client = create_app().test_client()
    client.get("/announce", query_string=query(SEED, 10))
    for field in ("peer_id", "ip", "port", "left"):
        missing = query()
        del missing[field]
        assert client.get("/announce", query_string=missing).status_code == 400
    duplicate = list(query().items()) + [("left", "10")]
    assert client.get("/announce", query_string=duplicate).status_code == 400
    assert client.get("/announce", query_string=query(SEED, 1, "completed")).status_code == 400
    result = client.get("/announce", query_string=query(LEECHER, 1)).json
    assert result["seeds"] == 0 and result["leechers"] == 2


def test_apps_have_independent_state():
    first, second = create_app().test_client(), create_app().test_client()
    first.get("/announce", query_string=query())
    assert second.get("/announce", query_string=query(LEECHER, 1)).json["peers"] == []


def test_concurrent_announces_do_not_lose_registrations():
    state = TrackerState()
    peers = [Peer(i.to_bytes(20, "big"), "127.0.0.1", 7000 + i) for i in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda peer: state.announce(peer, left=1), peers))
    result = state.announce(SEED, left=0)
    assert (result["seeds"], result["leechers"], len(result["peers"])) == (1, 40, 40)


@pytest.fixture
def live_tracker():
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


async def test_real_http_client_discovers_seed_and_lifecycle(live_tracker):
    client = TrackerClient(live_tracker + "/announce")
    assert client.announce_interval == 30
    result = await asyncio.wait_for(client.announce(SEED, left=0, event="started"), 3)
    assert result.peers == () and result.seeds == 1
    result = await client.announce(LEECHER, left=100, event="started")
    assert result.peers == (SEED,) and result.leechers == 1
    result = await client.announce(LEECHER, left=0, event="completed")
    assert result.seeds == 2 and result.leechers == 0
    result = await client.announce(SEED, left=0, event="stopped")
    assert result.peers == (LEECHER,) and result.seeds == 1
    with pytest.raises(TrackerError, match="HTTP 404"):
        await TrackerClient(live_tracker + "/missing").announce(SEED, left=0)


@pytest.mark.parametrize("raw", [
    b"not json", b"\xff", b"null", b"[]", b"{}", b"[" * 10000 + b"]" * 10000,
    b'{"peers":[],"seeds":true,"leechers":0}',
    b'{"peers":[],"seeds":0,"leechers":-1}',
    b'{"peers":{},"seeds":0,"leechers":0}',
    b'{"peers":[{}],"seeds":1,"leechers":0}',
])
def test_invalid_tracker_responses(raw):
    with pytest.raises(TrackerError, match="invalid tracker response"):
        _decode_response(raw)


def test_bad_peer_entries_and_counts():
    for peers, count in (([SEED.to_dict(), SEED.to_dict()], 2),
                         ([{**SEED.to_dict(), "port": True}], 1),
                         ([{**SEED.to_dict(), "peer_id": "x"}], 1),
                         ([SEED.to_dict()], 0)):
        with pytest.raises(TrackerError):
            _decode_response(json.dumps({"peers": peers, "seeds": count, "leechers": 0}).encode())


async def test_client_io_runs_off_event_loop(monkeypatch):
    client = TrackerClient("http://127.0.0.1:8000/announce")
    started, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def blocking_request(url):
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(3)
        return _decode_response(b'{"peers":[],"seeds":1,"leechers":0}')

    monkeypatch.setattr(client, "_request", blocking_request)
    task = asyncio.create_task(client.announce(SEED, left=0))
    try:
        async def wait_started():
            while not started.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_started(), 2)
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 3)


def test_network_errors_and_response_size_limit(monkeypatch):
    client = TrackerClient("http://127.0.0.1:8000/announce", timeout=0.5)

    class Opener:
        error = None

        def open(self, request, timeout):
            assert timeout == 0.5
            if self.error is not None:
                raise self.error
            return io.BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1))

    opener = Opener()
    monkeypatch.setattr(client_module, "build_opener", lambda *args: opener)
    for error in (TimeoutError("timed out"), URLError("offline"), IncompleteRead(b"partial"),
                  HTTPError(client.url, 503, "unavailable", {}, io.BytesIO())):
        opener.error = error
        with pytest.raises(TrackerError):
            client._request(client.url)
    opener.error = None
    with pytest.raises(TrackerError, match="size limit"):
        client._request(client.url)


async def test_client_rejects_bad_local_arguments():
    for timeout in (0, -1, float("inf"), float("nan"), True, "5"):
        with pytest.raises(ValueError):
            TrackerClient("http://localhost/announce", timeout=timeout)
    for url in ("file:///tmp/x", "http:///announce", "http://user:pass@host/announce",
                "http://host/announce?left=0", "http://host/announce#fragment",
                "http://host:0/announce", "http://host:65536/announce"):
        with pytest.raises(ValueError):
            TrackerClient(url)
    client = TrackerClient("http://localhost/announce")
    for left, event in ((-1, ""), (True, ""), (10**20, ""), (1, "completed"), (0, "bad")):
        with pytest.raises(ValueError):
            await client.announce(SEED, left=left, event=event)
