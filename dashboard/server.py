import glob
import json
import os
import sys
import time
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "mock_run")
PORT = 8080
START = time.time()


def load_events():
    events = []
    for path in glob.glob(os.path.join(RUN_DIR, "events-*.jsonl")):
        with open(path) as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    events.sort(key=lambda e: e["t"])
    return events


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(BASE, "static"), **kwargs)

    def do_GET(self):
        if urlparse(self.path).path == "/api/events":
            now = time.time() - START
            body = json.dumps({"now": now, "events": [e for e in load_events() if e["t"] <= now]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


print("Dashboard on http://localhost:" + str(PORT), "reading", RUN_DIR)
HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
