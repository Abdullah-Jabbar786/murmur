"""Serve the Murmur dashboard locally. Run python -m dashboard.server --help."""
import argparse
import json
import subprocess
import sys
import tempfile
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.live import RunLog
from peer.metadata import load_metadata

ROOT = Path(__file__).resolve().parent


class Dashboard:
    def __init__(self, directory, metadata=None, demo=False):
        self.lock = threading.Lock()
        self.demo = demo
        self.worker = self.error_stream = None
        self.temp = tempfile.TemporaryDirectory(prefix="murmur-dashboard-") if demo else None
        self.protocol = None
        self.metadata = metadata
        self.log = RunLog(Path(self.temp.name) if self.temp else directory)
        self.dataset = None

    def close(self):
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait()
        if self.error_stream:
            self.error_stream.close()
        if self.temp:
            self.temp.cleanup()

    def start(self, protocol):
        if not self.demo or protocol not in ("tcp", "murmurtp"):
            raise ValueError("Choose TCP or MurmurTP in local review mode.")
        if self.worker and self.worker.poll() is None:
            raise RuntimeError("A transfer is already running.")
        self.close()
        self.temp = tempfile.TemporaryDirectory(prefix="murmur-dashboard-")
        folder = Path(self.temp.name)
        self.log = RunLog(folder)
        self.protocol = protocol
        self.metadata = folder / "metadata.json"
        self.dataset = None
        self.error_stream = (folder / "worker.txt").open("wb")
        self.worker = subprocess.Popen([sys.executable, "-m", "dashboard.demo", str(folder), protocol],
                                       cwd=ROOT.parent, stdout=self.error_stream,
                                       stderr=subprocess.STDOUT)

    def snapshot(self, since, revision):
        page = self.log.page(since, revision)
        meta = self.metadata or self.log.root / "metadata.json"
        if self.dataset is None and meta.is_file():
            try:
                m = load_metadata(meta)
                self.dataset = dict(name=m.name, length=m.length, pieces=m.num_pieces,
                                    piece_length=m.piece_length)
            except ValueError:
                pass  # Writer may still be finishing metadata.json.
        status = "Watching"
        failure = None
        if self.demo:
            code = self.worker.poll() if self.worker else None
            status = ("Ready" if not self.worker else "Running" if code is None
                      else "Verified" if code == 0 else "Failed")
            if status == "Failed":
                lines = (self.log.root / "worker.txt").read_text(errors="replace").splitlines()
                failure = lines[-1] if lines else f"Transfer exited {code}"
        return dict(page, dataset=self.dataset, demo=self.demo, protocol=self.protocol,
                    status=status, error=failure)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]) and len(args) > 1 and str(args[1]) == "200":
            return
        super().log_message(fmt, *args)

    def reply(self, data, code=200):
        body = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path not in ("/api/state", "/api/events"):
            return super().do_GET()
        query = parse_qs(url.query)
        try:
            since = int(query.get("since", ["0"])[0])
            if since < 0:
                raise ValueError()
        except ValueError:
            return self.reply({"error": "since must be a nonnegative cursor"}, 400)
        app = self.server.app
        with app.lock:
            data = app.snapshot(since, query.get("revision", [""])[0])
        self.reply(data)

    def do_POST(self):
        if self.path not in ("/api/demo/tcp", "/api/demo/murmurtp") or not self.server.app.demo:
            return self.send_error(HTTPStatus.NOT_FOUND)
        # Only the local dashboard can launch this fixed demonstration.
        origin = self.headers.get("Origin")
        if (self.headers.get("X-Murmur-Review") != "1" or
                origin not in (None, f"http://127.0.0.1:{self.server.server_port}",
                               f"http://localhost:{self.server.server_port}")):
            return self.send_error(HTTPStatus.FORBIDDEN)
        with self.server.app.lock:
            try:
                self.server.app.start(self.path.rsplit("/", 1)[1])
            except RuntimeError as exc:
                return self.reply({"error": str(exc)}, 409)
        self.reply({"started": True}, 202)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", help="watch this run for events-*.jsonl")
    parser.add_argument("--metadata", type=Path, help="dataset metadata for the watched run")
    parser.add_argument("--demo", action="store_true", help="enable real local review transfers")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.demo and (args.directory or args.metadata):
        parser.error("--demo uses its own temporary dataset and logs")
    directory = args.directory or ROOT / "mock_run"
    if args.directory and not directory.is_dir():
        parser.error("run directory does not exist")
    if args.metadata:
        load_metadata(args.metadata)
    app = Dashboard(directory, args.metadata, args.demo)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.app = app
        print(f"Murmur dashboard: http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.close()


if __name__ == "__main__":
    main()
