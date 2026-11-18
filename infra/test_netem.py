"""Measure M1.2 on a disposable M1.1 lab. Run with sudo make test-netem."""

import json
import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def output(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, timeout=60)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} exited {result.returncode}\n{result.stdout}")
    return result.stdout.strip()


def run_make(*args):
    subprocess.run(["make", *args], cwd=ROOT, check=True)


def ns(node):
    return ["ip", "netns", "exec", "murmur-" + node]


def in_range(label, actual, low, high, unit):
    print(f"{label}: {actual:.2f} {unit} (expected {low:g} to {high:g})", flush=True)
    if not low <= actual <= high:
        raise RuntimeError(f"{label} outside acceptance range")


def ping(count, interval):
    result = output(*ns("a1"), "ping", "-n", "-q", "-c", str(count), "-i", interval,
                    "-W", "1", "10.0.2.11")
    print(result, flush=True)
    received = int(re.search(r"(\d+) received", result)[1])
    average = float(re.search(r"min/avg/max/mdev = [\d.]+/([\d.]+)", result)[1])
    return average, 100 * (count - received) / count


def throughput(source, destination, address):
    # Probe the listening socket without consuming the one-shot server's connection.
    server = subprocess.Popen([*ns(destination), "iperf3", "-s", "-1"],
                              stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not output(*ns(destination), "ss", "-Hltn", "sport", "=", ":5201"):
            if server.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f"iperf3 did not start on {destination}")
            time.sleep(0.05)
        report = json.loads(output(*ns(source), "iperf3", "-c", address, "-O", "1",
                                   "-t", "5", "-J"))
        if "error" in report:
            raise RuntimeError(report["error"])
        return report["end"]["sum_received"]["bits_per_second"] / 1_000_000
    finally:
        if server.poll() is None:
            server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run sudo make test-netem on Linux.")
    if any(line.startswith("murmur-") for line in output("ip", "netns", "list").splitlines()):
        raise SystemExit("Existing Murmur lab found. Start this test with a clean lab.")
    os.environ["LC_ALL"] = "C"
    run_make("topo", "LEECHERS_PER_LAN=1")
    try:
        ping(2, "0.1")  # warm ARP before measuring the unloaded RTT
        baseline, _ = ping(10, "0.05")
        run_make("netem", "ARGS=")
        delayed, loss = ping(20, "0.05")
        in_range("Added idle RTT", delayed - baseline, 17, 35, "ms")
        in_range("Baseline profile loss", loss, 0, 0, "%")
        for source, dest, addr in [("a1", "b1", "10.0.2.11"), ("b1", "a1", "10.0.1.11")]:
            in_range(f"WAN {source} to {dest}", throughput(source, dest, addr), 8, 10.8, "Mbit/s")

        run_make("netem", "ARGS=--rate 5 --delay 0 --loss 0")
        in_range("WAN rate override", throughput("a1", "b1", "10.0.2.11"), 4, 5.4, "Mbit/s")
        in_range("Seed to LAN A (local to R1)", throughput("seed", "a1", "10.0.1.11"),
                 8, 10.8, "Mbit/s")

        run_make("netem", "ARGS=--delay 10 --loss 10")
        _, loss = ping(200, "0.03")
        in_range("Ping loss, 10% each way", loss, 6, 35, "%")
        endpoints = [("r1", "wan0"), ("r2", "wan0"), ("seed", "eth0")]
        for node, dev in endpoints:
            stats = output(*ns(node), "tc", "-s", "qdisc", "show", "dev", dev)
            print(node, dev, stats, flush=True)
            if dev == "wan0":
                drops = re.search(r"qdisc netem 420:[^\n]*\n\s*Sent [^\n]*?dropped (\d+)", stats)
                if not drops or int(drops[1]) == 0:
                    raise RuntimeError(f"No netem drops observed on {node}/{dev}")
        run_make("netem-clear")
        run_make("netem-clear")
        for node, dev in endpoints:
            state = output(*ns(node), "tc", "qdisc", "show", "dev", dev)
            if "qdisc noqueue 0: root" not in state:
                raise RuntimeError(f"{node}/{dev} still configured: {state}")
        restored, loss = ping(20, "0.05")
        in_range("RTT change after clear", restored - baseline, -5, 5, "ms")
        in_range("Loss after clear", loss, 0, 0, "%")
    finally:
        run_make("topo-down")
    print("PASS: M1.2 routed WAN and seed shaping, reapply and clear", flush=True)


if __name__ == "__main__":
    main()
