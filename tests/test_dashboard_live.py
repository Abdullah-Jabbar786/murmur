import hashlib
import json
import time
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dashboard.live import RunLog
from dashboard.server import Dashboard, Handler
from dashboard.mock_events import generate


def test_watch_http_serves_chart_data_and_read_only_controls(tmp_path):
    generate(tmp_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http:
        http.app = Dashboard(tmp_path)
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{http.server_port}"
        try:
            with urlopen(url, timeout=5) as response:
                assert b'id="chart"' in response.read()
            with urlopen(url+"/api/state", timeout=5) as response:
                state = json.load(response)
            assert state["more"] and not state["demo"]
            rows = state["events"].copy()
            while state["more"]:
                query = f'?since={state["cursor"]}&revision={state["revision"]}'
                with urlopen(url+"/api/events"+query, timeout=5) as response:
                    state = json.load(response)
                rows.extend(state["events"])
            assert len(rows) == state["cursor"]
            assert sum(r["type"] == "join" for r in rows) == 11
            assert any(r["type"] == "marker" and r["label"] == "Synthetic fixture" for r in rows)
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(url+"/api/demo/tcp", method="POST"), timeout=5)
            assert exc.value.code == 404
            exc.value.close()
        finally:
            http.shutdown()
            thread.join()


def test_partial_line_and_rotation_reset_the_cursor(tmp_path):
    path = tmp_path / "events-1.jsonl"
    first = b'{"t":1,"node":"1","type":"join"}\n'
    second = b'{"t":2,"node":"1","type":"have","piece":3}'
    path.write_bytes(first + second[:12])
    log = RunLog(tmp_path)
    initial = log.page(0, "")
    assert [r["type"] for r in initial["events"]] == ["join"]
    assert not log.page(1, initial["revision"])["events"]
    with path.open("ab") as stream:
        stream.write(second[12:] + b"\nnot-json\n")
    assert log.page(1, initial["revision"])["events"][0]["piece"] == 3
    assert log.invalid == 1
    path.write_bytes(first)
    reset = log.page(2, initial["revision"])
    assert reset["revision"] != initial["revision"]
    assert [r["type"] for r in reset["events"]] == ["join"]


def test_browser_review_request_runs_a_tcp_transfer(tmp_path):
    app = Dashboard(tmp_path, demo=True)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http:
        http.app = app
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{http.server_port}"
        try:
            headers = {"X-Murmur-Review": "1", "Origin": url}
            request = Request(url+"/api/demo/tcp", method="POST", headers=headers)
            with urlopen(request, timeout=5) as response:
                assert response.status == 202
            deadline = time.monotonic() + 45
            rows = []
            cursor, revision = 0, ""
            while time.monotonic() < deadline:
                with urlopen(f"{url}/api/state?since={cursor}&revision={revision}", timeout=5) as response:
                    state = json.load(response)
                rows.extend(state["events"])
                cursor, revision = state["cursor"], state["revision"]
                assert not state["error"]
                if state["status"] == "Verified" and sum(r["type"]=="summary" for r in rows)==3:
                    break
                time.sleep(.05)
            else:
                pytest.fail("browser endpoint did not deliver the final peer summaries")
            assert sum(r["type"]=="complete" and not r["initial"] for r in rows) == 2
            assert state["dataset"]["length"] == 8 * 1024 * 1024
        finally:
            http.shutdown()
            thread.join()
            app.close()


@pytest.mark.parametrize("protocol", ["tcp", "murmurtp"])
def test_local_review_transfer_emits_verified_peer_results(tmp_path, protocol):
    app = Dashboard(tmp_path, demo=True)
    try:
        app.start(protocol)
        with pytest.raises(RuntimeError):
            app.start(protocol)
        rows = []
        cursor, revision = 0, ""
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = app.snapshot(cursor, revision)
            rows.extend(state["events"])
            cursor, revision = state["cursor"], state["revision"]
            assert state["status"] != "Failed", state["error"]
            if state["status"] == "Verified" and not state["more"]:
                # Worker may have exited during the last poll.
                rows.extend(app.snapshot(cursor, revision)["events"])
                break
            time.sleep(0.05)
        else:
            pytest.fail("transfer did not finish")
        assert state["dataset"]["pieces"] == 256
        assert {r["proto"] for r in rows if r["type"] == "transport"} == {protocol}
        assert sum(r["type"] == "summary" for r in rows) == 3
        seed_hash = hashlib.sha256((app.log.root / "seed/payload.bin").read_bytes()).digest()
        for peer in ("leecher-1", "leecher-2"):
            events = [r for r in rows if r["source"].startswith(peer + "/")]
            assert {r["piece"] for r in events if r["type"] == "have"} == set(range(256))
            assert events[-1]["complete"] and events[-1]["bytes_down"] == 8 * 1024 * 1024
            assert hashlib.sha256((app.log.root / peer / "payload.bin").read_bytes()).digest() == seed_hash
    finally:
        app.close()
