"""Run a routed TCP or MurmurTP swarm on Ubuntu and save its WAN capture."""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))  # Support sudo .venv/bin/python experiments/run.py.

from core.config import load_config
from core.transports import transport_port
from dashboard.live import RunLog
from peer.metadata import generate_dataset, parse_size


def lab_peers():
    peers = {"seed": "10.0.3.11"}
    for index in range(1, 6):
        for lan, subnet in (("a", 1), ("b", 2)):
            peers[f"{lan}{index}"] = f"10.0.{subnet}.{10 + index}"
    return peers


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def launch(run_dir, name, args, env):
    with (run_dir / f"{name}.log").open("xb") as output:
        return subprocess.Popen(args, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)


def stop(processes, grace=15):
    # Interrupt every writer first so all peers can flush their final summaries.
    for proc in processes.values():
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + grace
    for proc in processes.values():
        try:
            proc.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def wait_for_downloads(log, processes, leechers, timeout):
    pending = set(leechers)
    cursor = 0
    deadline = time.monotonic() + timeout
    while pending:
        for name, proc in processes.items():
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited with {proc.returncode}; see {name}.log")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {', '.join(sorted(pending))}")
        log.refresh()
        for row in log.rows[cursor:]:
            if row["type"] == "tracker" and row.get("event") == "completed" and row["node"] in pending:
                pending.remove(row["node"])
                print(f"{row['node']} completed ({len(leechers) - len(pending)}/{len(leechers)})", flush=True)
        cursor = len(log.rows)
        if pending:
            time.sleep(0.25)


def verify(run_dir, peers, size, expected_hash, log, transport="tcp"):
    log.refresh()
    if log.invalid:
        raise ValueError("malformed event records in run directory")
    summaries = {row["node"]: row for row in log.rows if row["type"] == "summary"}
    downloads = {row["node"] for row in log.rows
                 if row["type"] == "complete" and row.get("initial") is False}
    observed = {(row["node"], row.get("proto")) for row in log.rows if row["type"] == "transport"}
    if observed != {(ip, transport) for ip in peers.values()}:
        raise ValueError(f"transport events do not confirm {transport} on every peer")
    for name, ip in peers.items():
        summary = summaries.get(ip, {})
        if (summary.get("complete") is not True or summary.get("hash_fails") != 0
                or summary.get("bytes_down") != (0 if name == "seed" else size)):
            raise ValueError(f"{name}: missing or inconsistent final summary")
        path = run_dir / name / "payload.bin"
        if name != "seed" and ip not in downloads:
            raise ValueError(f"{name}: missing download completion event")
        if path.stat().st_size != size or sha256(path) != expected_hash:
            raise ValueError(f"{name}: file differs from seed")
    return summaries


@contextmanager
def capture_wan(run_dir, port, protocol="tcp"):
    trace = run_dir / "r1-wan0.pcap"
    log_path = run_dir / "capture.log"
    with trace.open("xb") as output, log_path.open("xb") as errors:
        proc = subprocess.Popen(["bash", "infra/capture.sh", str(port), protocol], cwd=ROOT,
                                stdout=output, stderr=errors, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while b"listening on wan0," not in log_path.read_bytes():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"capture did not start; see {log_path}")
                time.sleep(0.05)
            yield proc
            if proc.poll() is not None:
                raise RuntimeError(f"capture exited before swarm shutdown; see {log_path}")
        finally:
            stop({"capture": proc})

    if proc.returncode != 0:
        raise RuntimeError(f"capture shutdown exited with {proc.returncode}; see {log_path}")
    # Read the entire closed file and reconcile with tcpdump's live count.
    # A global header alone, a partial write or missing records cannot pass.
    with trace.open("rb") as saved:
        counted = subprocess.run(["tcpdump", "--count", "-nn", "-r", "-", f"{protocol} port {port}"],
                                 stdin=saved, capture_output=True, text=True)
    if counted.returncode:
        raise ValueError(f"unreadable capture {trace}: {counted.stderr.strip()}")
    count = re.fullmatch(r"(\d+) packets?", counted.stdout.strip())
    live = re.search(rb"(?m)^(\d+) packets? captured\s*$", log_path.read_bytes())
    if not count or not live or int(count[1]) == 0 or int(count[1]) != int(live[1]):
        raise ValueError(f"capture packet counts disagree or are empty; see {log_path} and {trace}")
    print(f"Verified WAN capture: {count[1]} {protocol.upper()} packets, {trace.stat().st_size} bytes, {trace}")


def run_swarm(run_dir, size, timeout, peers, tracker_ip, config, prefix, *, capture=False):
    """Launch independent processes. prefix(name) selects the host namespace."""
    seed = run_dir / "seed" / "payload.bin"
    metadata = run_dir / "metadata.json"
    generate_dataset(seed, metadata, size=size, piece_length=config["piece_size"])
    expected_hash = sha256(seed)
    config_path = run_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    env = dict(os.environ, MURMUR_T0=str(time.time()), PYTHONUNBUFFERED="1")
    python = [sys.executable, "-m"]
    log = RunLog(run_dir)
    processes = {}
    with ExitStack() as cleanup:
        monitors = {}
        if capture:
            protocol = "udp" if config["transport"] == "murmurtp" else "tcp"
            monitors["capture"] = cleanup.enter_context(capture_wan(run_dir, transport_port(config), protocol))
        tracker = launch(run_dir, "tracker", prefix("tracker") + python + [
            "tracker.server", "--host", tracker_ip, "--port", str(config["tracker_port"])], env)
        cleanup.callback(stop, {"tracker": tracker})
        # MurmurTP may wait for FIN retries or the 30 s inactivity timer under loss.
        cleanup.callback(stop, processes, 60 if config["transport"] == "murmurtp" else 15)
        for name, ip in peers.items():
            args = ["peer.node", "swarm", "--host", ip, "--file", str(run_dir / name / "payload.bin"),
                    "--mode", "seed" if name == "seed" else "create", "--metadata", str(metadata),
                    "--config", str(config_path), "--tracker-url", config["tracker_url"],
                    "--run-dir", str(run_dir), "--total-timeout", str(timeout)]
            processes[name] = launch(run_dir, name, prefix(name) + python + args, env)
        leechers = {ip for name, ip in peers.items() if name != "seed"}
        wait_for_downloads(log, {**monitors, "tracker": tracker, **processes}, leechers, timeout)
    for name, proc in processes.items():
        if proc.returncode != 0:
            raise RuntimeError(f"{name} shutdown exited with {proc.returncode}; see {name}.log")
    summaries = verify(run_dir, peers, size, expected_hash, log, config["transport"])
    print(f"Verified {len(leechers)} leecher files, {size} bytes each. SHA-256 {expected_hash}")
    print(f"Leecher upload bytes: {sum(summaries[ip]['bytes_up'] for ip in leechers)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("tcp", "murmurtp"), help="override config/default.json")
    parser.add_argument("--loss", type=int, choices=range(101), metavar="0..100", default=0,
                        help="WAN loss percentage per direction (default: 0)")
    parser.add_argument("--size", type=parse_size, default=8 * 1024 * 1024,
                        help="payload size, e.g. 8MiB (default) or 64MiB")
    parser.add_argument("--timeout", type=int, default=900, help="transfer deadline in seconds")
    parser.add_argument("--no-capture", action="store_true", help="run without a WAN pcap")
    args = parser.parse_args()
    if args.size < 1 or args.timeout < 1:
        parser.error("size and timeout must be positive")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run on Ubuntu with sudo .venv/bin/python experiments/run.py")
    import fcntl

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with open("/run/lock/murmur-harness.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(1, "another Murmur harness run is active\n")
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
            run_dir = ROOT / "results" / "m22" / run_id
            run_dir.mkdir(parents=True)
            print(f"Run directory: {run_dir}", flush=True)
            config = load_config(overrides={"mode": "swarm", "file_size": args.size, "wan_loss_pct": args.loss})
            if args.transport:
                config["transport"] = args.transport
            transport_port(config)  # Validate the selected protocol before building the lab.
            print(f"Swarm transport: {config['transport']}; WAN loss per direction: {args.loss}%", flush=True)
            try:
                subprocess.run(["bash", "infra/topology.sh", "--leechers-per-lan", "5"],
                               cwd=ROOT, check=True)
                subprocess.run(["bash", "infra/netem.sh", "apply", "--loss", str(args.loss)], cwd=ROOT, check=True)
                run_swarm(run_dir, args.size, args.timeout, lab_peers(), "10.0.3.10", config,
                          lambda name: ["bash", "infra/run_in_ns.sh", name], capture=not args.no_capture)
                if args.loss:
                    with (run_dir / "wan-qdisc.log").open("x", encoding="utf-8") as counters:
                        for router in ("r1", "r2"):
                            print(router, file=counters, flush=True)
                            subprocess.run(["bash", "infra/run_in_ns.sh", router, "tc", "-s", "qdisc",
                                            "show", "dev", "wan0"], cwd=ROOT, stdout=counters, check=True)
            finally:
                subprocess.run(["bash", "infra/teardown.sh"], cwd=ROOT, check=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Routed swarm run failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Run interrupted\n")
    if config["transport"] == "tcp":
        print("PASS: M2.2 routed TCP swarm, 10/10 downloads verified, topology removed")
    else:
        print(f"PASS: routed MurmurTP swarm, {args.size} bytes, {args.loss}% WAN loss, "
              "10/10 downloads verified, topology removed")
    if not args.no_capture:
        print("PASS: M2.3 routed swarm pcap saved and verified")


if __name__ == "__main__":
    main()
