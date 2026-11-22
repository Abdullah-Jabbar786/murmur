"""Run a routed TCP or MurmurTP swarm on Ubuntu and save its WAN capture."""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
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
from experiments import e4, e5, series, sweep
from experiments.payloads import sha256
from experiments.scaling import COUNTS, lab_peers, wan_delta
from peer.metadata import generate_dataset, parse_size


def launch(run_dir, name, args, env):
    with (run_dir / f"{name}.log").open("xb") as output:
        return subprocess.Popen(args, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)


def stop(processes, grace=15):
    # Interrupt every writer first so all peers can flush their final summaries.
    def signal_live(sig):
        for proc in processes.values():
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass

    try:
        signal_live(signal.SIGINT)
        deadline = time.monotonic() + grace
        for proc in processes.values():
            try:
                proc.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
    finally:
        signal_live(signal.SIGKILL)
        for proc in processes.values():
            proc.wait()


def wait_for_downloads(log, processes, leechers, timeout, *, tracker=True):
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
            if tracker:
                complete = row["type"] == "tracker" and row.get("event") == "completed"
            else:
                complete = row["type"] == "complete" and row.get("initial") is False
            if complete and row["node"] in pending:
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
                                 stdin=saved, capture_output=True, text=True, timeout=300)
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
    client_server = config.get("mode") == "client_server"
    with ExitStack() as cleanup:
        monitors = {}
        if capture:
            protocol = "udp" if config["transport"] == "murmurtp" else "tcp"
            monitors["capture"] = cleanup.enter_context(capture_wan(run_dir, transport_port(config), protocol))
        if not client_server:
            tracker = launch(run_dir, "tracker", prefix("tracker") + python + [
                "tracker.server", "--host", tracker_ip, "--port", str(config["tracker_port"])], env)
            monitors["tracker"] = tracker
            cleanup.callback(stop, {"tracker": tracker})
        # MurmurTP may wait for FIN retries or the 30 s inactivity timer under loss.
        cleanup.callback(stop, processes, 60 if config["transport"] == "murmurtp" else 15)
        for name, ip in peers.items():
            args = ["peer.node", "swarm", "--host", ip, "--file", str(run_dir / name / "payload.bin"),
                    "--mode", "seed" if name == "seed" else "create", "--metadata", str(metadata),
                    "--config", str(config_path),
                    "--run-dir", str(run_dir), "--total-timeout", str(timeout)]
            if not client_server:
                args.extend(["--tracker-url", config["tracker_url"]])
            elif name != "seed":
                args.extend(["--origin-seed", f"{peers['seed']}:{transport_port(config)}"])
            processes[name] = launch(run_dir, name, prefix(name) + python + args, env)
            if name == "seed" and "leechers" in config:
                # Wait for the same seed listener milestone in both E3 modes.
                deadline = time.monotonic() + min(60, timeout)
                while b"Peer listening on " not in (run_dir / "seed.log").read_bytes():
                    if processes[name].poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("seed listener did not start; see seed.log")
                    time.sleep(.05)
        leechers = {ip for name, ip in peers.items() if name != "seed"}
        wait_for_downloads(log, {**monitors, **processes}, leechers, timeout, tracker=not client_server)
    for name, proc in processes.items():
        if proc.returncode != 0:
            raise RuntimeError(f"{name} shutdown exited with {proc.returncode}; see {name}.log")
    summaries = verify(run_dir, peers, size, expected_hash, log, config["transport"])
    print(f"Verified {len(leechers)} leecher files, {size} bytes each. SHA-256 {expected_hash}")
    uploaded = sum(summaries[ip]["bytes_up"] for ip in leechers)
    if client_server and uploaded != 0:
        raise ValueError("client-server leechers uploaded payload")
    print(f"Leecher upload bytes: {uploaded}")
    return dict(sha256=expected_hash, file_bytes=size, verified_leechers=len(leechers),
                leecher_upload_bytes=uploaded, seed_upload_bytes=summaries[peers["seed"]]["bytes_up"])


def lab_command(args, timeout, *, stdout=None):
    # Shell helpers may themselves be waiting on flock or ip. Reap the whole
    # command group before teardown or retry can acquire the same resources.
    proc = subprocess.Popen(args, cwd=ROOT, stdout=stdout, start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    if code:
        raise subprocess.CalledProcessError(code, args)


def wan_counters():
    raw = subprocess.check_output(["ip", "-n", "murmur-r1", "-j", "-s", "link", "show",
                                   "dev", "wan0"], text=True, timeout=10)
    link, = json.loads(raw)
    stats = link.get("stats64") or link["stats"]
    return {direction: stats[direction]["bytes"] for direction in ("rx", "tx")}


def routed_trial(directory, config, timeout, capture):
    size, loss = config["file_size"], config["wan_loss_pct"]
    rtt = config.get("wan_rtt_ms", 20)
    count = config.get("leechers", 10)
    scaling = "leechers" in config
    print(f"Distribution: {config.get('mode', 'swarm')}, {count} leechers; "
          f"transport: {config['transport']}; WAN loss per direction: {loss}%", flush=True)
    try:
        lab_command(["bash", "infra/topology.sh", "--leechers-per-lan", str((count + 1) // 2)], 120)
        lab_command(["bash", "infra/netem.sh", "apply", "--loss", str(loss),
                     "--delay", f"{rtt / 2:g}"], 60)
        if scaling:
            before = wan_counters()
        result = run_swarm(directory, size, timeout, lab_peers(count), "10.0.3.10", config,
                           lambda name: ["bash", "infra/run_in_ns.sh", name], capture=capture)
        if scaling:
            after = wan_counters()
            wan_bytes = wan_delta(before, after)
            if wan_bytes <= 0:
                raise ValueError("expected routed traffic on R1 wan0")
            result.update(wan_before=before, wan_after=after)
            print(f"R1 wan0 transferred {wan_bytes} bytes (RX + TX)")
        if loss:
            with (directory / "wan-qdisc.log").open("x", encoding="utf-8") as counters:
                for router in ("r1", "r2"):
                    print(router, file=counters, flush=True)
                    lab_command(["bash", "infra/run_in_ns.sh", router, "tc", "-s", "qdisc",
                                 "show", "dev", "wan0"], 30, stdout=counters)
    finally:
        lab_command(["bash", "infra/teardown.sh"], 120)
    if scaling:
        print(f"PASS: E3 TCP {config['mode']}, {count}/{count} downloads verified, topology removed")
    elif config["transport"] == "tcp":
        print("PASS: M2.2 routed TCP swarm, 10/10 downloads verified, topology removed")
    else:
        print(f"PASS: routed MurmurTP swarm, {size} bytes, {loss}% WAN loss, "
              "10/10 downloads verified, topology removed")
    if capture:
        print("PASS: M2.3 routed swarm pcap saved and verified")
    return result


def flow_trial(directory, config, timeout, capture):
    """Run one verified bulk flow from LAN A to LAN B."""
    size = config["file_size"]
    source = directory / "payload.bin"
    generate_dataset(source, directory / "metadata.json", size=size, seed=config["seed"])
    digest = sha256(source)
    config_path = directory / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    tcp = config["transport"] == "tcp"
    port = transport_port(config)
    ns = ["bash", "infra/run_in_ns.sh"]
    bench = [sys.executable, "-m", "murmurtp.flowbench"]
    options = ["--host", "10.0.2.11", "--port", str(port), "--config", str(config_path),
               "--run-dir", str(directory), "--timeout", str(timeout), "--max-bytes", str(size)]
    try:
        lab_command(["bash", "infra/topology.sh", "--leechers-per-lan", "1"], 120)
        lab_command(["bash", "infra/netem.sh", "apply", "--delay", "10",
                     "--loss", str(config["wan_loss_pct"]), "--rate", "10"], 60)
        epoch = time.time()
        env = dict(os.environ, LC_ALL="C", MURMUR_T0=str(epoch), PYTHONUNBUFFERED="1")
        with ExitStack() as stack:
            if capture:
                stack.enter_context(capture_wan(directory, port, "tcp" if tcp else "udp"))
            processes = {}
            stack.callback(stop, processes)
            receiver = launch(directory, "receiver", ns + ["b1"] + bench + ["receive"] + options, env)
            processes["receiver"] = receiver
            deadline = time.monotonic() + min(30, timeout)
            while b"Listening on " not in (directory / "receiver.log").read_bytes():
                if receiver.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("flow receiver did not start; see receiver.log")
                time.sleep(.05)
            processes["ping"] = launch(directory, "ping", ns + ["a1", "ping", "-D", "-n",
                                       "-i", "1", "-W", "2", "10.0.2.11"], env)
            sender = launch(directory, "sender", ns + ["a1"] + bench + ["send", "--file", str(source)] + options, env)
            processes["sender"] = sender
            deadline = time.monotonic() + timeout
            next_sample = time.monotonic()
            with (directory / "ss.log").open("x", encoding="utf-8") as ss_log:
                while sender.poll() is None or receiver.poll() is None:
                    for name in ("receiver", "sender", "ping"):
                        if processes[name].poll() not in (None, 0):
                            raise RuntimeError(f"{name} failed; see {name}.log")
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError("E4 flow exceeded the trial deadline")
                    if tcp and now >= next_sample:
                        print(f"t={time.time()-epoch:.6f}", file=ss_log, flush=True)
                        lab_command(ns + ["a1", "ss", "-Hnti", "state", "established",
                                          "dst", f"10.0.2.11:{port}"], 5, stdout=ss_log)
                        next_sample = now + 1
                    time.sleep(.05)
            for name in ("sender", "receiver"):
                if processes[name].returncode != 0:
                    raise RuntimeError(f"{name} failed; see {name}.log")
    finally:
        lab_command(["bash", "infra/teardown.sh"], 120)
    result = e4.measure(directory, config, digest, epoch)
    print(f"PASS: verified {size} bytes, {result['samples']} sender samples, "
          f"{result['goodput_mbps']:.3f} Mbit/s receiver goodput", flush=True)
    return result


def fairness_trial(directory, config, timeout, capture):
    """Release two ready bulk senders onto the same routed WAN."""
    size = config["file_size"]
    source = directory / "payload.bin"
    generate_dataset(source, directory / "metadata.json", size=size, seed=config["seed"])
    digest = sha256(source)
    gate = directory / "start-payload"
    ns = ["bash", "infra/run_in_ns.sh"]
    bench = [sys.executable, "-m", "murmurtp.flowbench"]
    flows = []
    processes = {}
    env = dict(os.environ, LC_ALL="C", MURMUR_T0=str(time.time()), PYTHONUNBUFFERED="1")

    def wait_ready(role, marker):
        deadline = time.monotonic() + min(timeout, 60)
        for i, path, *_ in flows:
            log = path / f"{role}.log"
            while marker not in log.read_bytes():
                if any(p.poll() is not None for p in processes.values()) or time.monotonic() >= deadline:
                    raise RuntimeError(f"E5 {role} readiness failed; see flow-{i} and endpoint logs")
                time.sleep(.05)

    try:
        lab_command(["bash", "infra/topology.sh", "--leechers-per-lan", "2"], 120)
        lab_command(["bash", "infra/netem.sh", "apply", "--delay", "10", "--loss", "0", "--rate", "10"], 60)
        with ExitStack() as stack:
            for i, proto in enumerate(config["pairing"].split("-"), 1):
                path = directory / f"flow-{i}"
                path.mkdir()
                port = 6900 + i
                host = f"10.0.2.{10 + i}"
                settings = dict(config, transport=proto)
                if proto == "tcp":
                    settings["tcp_congestion_control"] = "cubic"
                cfg = path / "config.json"
                cfg.write_text(json.dumps(settings), encoding="utf-8")
                args = ["--config", str(cfg), "--run-dir", str(path), "--host", host, "--port", str(port),
                        "--timeout", str(timeout), "--max-bytes", str(size)]
                ss_log = stack.enter_context((path / "ss.log").open("x")) if proto == "tcp" else None
                flows.append((i, path, args, f"{host}:{port}", ss_log))
                if capture:
                    stack.enter_context(capture_wan(path, port, "tcp" if proto == "tcp" else "udp"))
            stack.callback(stop, processes)
            for i, path, args, _, _ in flows:
                command = [*ns, f"b{i}", *bench, "receive", *args]
                processes["receive", i] = launch(path, "receiver", command, env)
            wait_ready("receiver", b"Listening on ")
            for i, path, args, _, _ in flows:
                command = [*ns, f"a{i}", *bench, "send", "--file", str(source), "--start-gate", str(gate), *args]
                processes["send", i] = launch(path, "sender", command, env)
            wait_ready("sender", b"Ready to send payload")
            gate.touch(exist_ok=False)
            deadline = time.monotonic() + timeout
            sample_at = time.monotonic()
            while True:
                status = {key: p.poll() for key, p in processes.items()}
                for (role, i), code in status.items():
                    if code not in (0, None):
                        raise RuntimeError(f"E5 flow-{i} {role} exited with {code}; see its log")
                if all(code == 0 for code in status.values()):
                    break
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("E5 competing transfers exceeded the deadline")
                if now >= sample_at:
                    for i, _, _, address, ss_log in flows:
                        if ss_log is not None and status["send", i] is None:
                            stamp = time.time() - float(env["MURMUR_T0"])
                            print(f"t={stamp:.6f}", file=ss_log, flush=True)
                            lab_command([*ns, f"a{i}", "ss", "-Hnti", "state", "established", "dst", address],
                                        5, stdout=ss_log)
                    sample_at = now + 1
                time.sleep(.05)
    finally:
        lab_command(["bash", "infra/teardown.sh"], 120)
    result = e5.measure(directory, config, digest)
    print(f"PASS: two verified {size}-byte flows; Jain fairness {result['jain_fairness']:.4f}", flush=True)
    return result


def supervise_linux_runner():
    """Keep cleanup alive even when the CLI process itself receives SIGKILL."""
    import ctypes

    parent = os.getpid()
    worker = os.fork()
    if worker == 0:
        os.setsid()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # Linux PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "could not arm parent-death signal")
        if os.getppid() != parent:  # Parent exited between fork and prctl.
            raise KeyboardInterrupt
        return
    try:
        _, status = os.waitpid(worker, 0)
    except KeyboardInterrupt:
        try:
            os.kill(worker, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(worker, 0)
        raise
    code = os.waitstatus_to_exitcode(status)
    raise SystemExit(code if code >= 0 else 128 - code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("tcp", "murmurtp"), help="override config/default.json")
    parser.add_argument("--loss", type=int, choices=range(101), metavar="0..100",
                        help="WAN loss percentage per direction (default: 0)")
    parser.add_argument("--rtt-ms", type=int, choices=(20, 100, 200),
                        help="configured WAN round-trip delay (default: 20 ms)")
    parser.add_argument("--size", type=parse_size,
                        help="payload size, e.g. 8MiB (default) or 64MiB")
    parser.add_argument("--timeout", type=int, help="transfer deadline in seconds (900 by default, 7200 for sweeps)")
    parser.add_argument("--no-capture", action="store_true", help="run without a WAN pcap")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--series", type=Path, metavar="NEW_DIR",
                       help="run one warm-up and three recorded trials in a new directory")
    modes.add_argument("--resume", type=Path, metavar="DIR", help="resume from DIR/run.json")
    modes.add_argument("--sweep", type=Path, metavar="DIR",
                       help="start or continue an experiment matrix in DIR (E1/E2 by default)")
    parser.add_argument("--cell", choices=list(sweep.CELLS),
                        help="run one sweep condition for a pilot")
    experiments = parser.add_mutually_exclusive_group()
    experiments.add_argument("--e3", action="store_true", help="sweep TCP swarm and client-server scaling")
    experiments.add_argument("--e4", action="store_true", help="sweep single flows; --loss selects one loss level")
    experiments.add_argument("--e5", action="store_true", help="compare fairness of two concurrent flows")
    parser.add_argument("--count", type=int, choices=COUNTS, help="run both E3 modes at one peer count")
    parser.add_argument("--keep-seed-only", action="store_true",
                        help="keep one payload copy per verified sweep trial")
    args = parser.parse_args()
    if args.e5 and (not args.sweep or args.cell or args.keep_seed_only):
        parser.error("--e5 requires --sweep and runs the three transport pairings")
    if args.e4 and (not args.sweep or args.e3 or args.cell or args.keep_seed_only
                    or args.loss is not None and args.loss not in e4.LOSSES):
        parser.error("--e4 requires --sweep and supports --loss 0/1/2/5 for a pilot")
    if args.e3 and (not args.sweep or args.cell):
        parser.error("--e3 requires --sweep and selects its cells by --count")
    if args.count is not None and not args.e3:
        parser.error("--count requires --e3")
    if (args.size is not None and args.size < 1) or (args.timeout is not None and args.timeout < 1):
        parser.error("size and timeout must be positive")
    if args.cell and not args.sweep:
        parser.error("--cell requires --sweep")
    if args.keep_seed_only and not args.sweep:
        parser.error("--keep-seed-only requires --sweep")
    if args.sweep and (args.transport or (args.loss is not None and not args.e4) or args.rtt_ms is not None):
        parser.error("--sweep defines transport, loss and RTT for each cell")
    if args.resume and (args.transport or args.loss is not None or args.rtt_ms is not None
                        or args.size is not None or args.no_capture):
        parser.error("--resume restores the saved settings; only --timeout can be overridden")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run on Ubuntu with sudo .venv/bin/python experiments/run.py")
    import fcntl

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        supervise_linux_runner()
        with open("/run/lock/murmur-harness.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(1, "another Murmur harness run is active\n")
            git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                              text=True, timeout=10).strip()
            if args.sweep:
                try:
                    import matplotlib.figure  # Check reporting dependency before launching long transfers.
                except ImportError as exc:
                    parser.exit(1, f"Sweep plotting requires Matplotlib in the Ubuntu project venv: {exc}\n")
                sweep.execute(args.sweep.resolve(), args.cell, args.size or 8 * 1024**2,
                              args.timeout or 7200, not args.no_capture, git_sha,
                              fairness_trial if args.e5 else flow_trial if args.e4 else routed_trial,
                              lab_peers(), keep_seed_only=args.keep_seed_only, e3=args.e3, count=args.count,
                              flow=args.e4, flow_loss=args.loss, fairness=args.e5)
                return
            if args.resume:
                run_dir = args.resume.resolve()
                state = series.load(run_dir)
            else:
                run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
                run_dir = (args.series or ROOT / "results" / "m22" / run_id).resolve()
                config = load_config(overrides={"mode": "swarm", "file_size": args.size or 8 * 1024**2,
                                                "wan_loss_pct": args.loss or 0,
                                                "wan_rtt_ms": args.rtt_ms or 20})
                if args.transport:
                    config["transport"] = args.transport
                transport_port(config)
                state = series.create(run_dir, config, args.timeout or 900, not args.no_capture,
                                      git_sha, series=bool(args.series))
            print(f"Run directory: {run_dir}", flush=True)
            experiment = state["config"].get("experiment")
            if experiment in ("E4", "E5") and state["git_sha"] != git_sha:
                raise ValueError(f"{experiment} checkpoint belongs to another Git revision")
            runner = {"E4": flow_trial, "E5": fairness_trial}.get(experiment, routed_trial)
            series.execute(run_dir, state, git_sha, runner, timeout=args.timeout)
            if state["series"]:
                print("PASS: one warm-up and three verified recorded runs; see run.json")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Routed swarm run failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Run interrupted\n")


if __name__ == "__main__":
    main()
