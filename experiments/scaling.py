"""E3 routed TCP conditions, interface measurements and comparison table."""
import csv
import statistics


COUNTS = (2, 5, 10, 20)
CELLS = {f"E3-{mode}-n{count}": (mode, count)
         for count in COUNTS for mode in ("swarm", "client_server")}


def lab_peers(count=10):
    peers = {"seed": "10.0.3.11"}
    for index in range(count):
        number = index // 2 + 1
        lan, subnet = (("a", 1), ("b", 2))[index % 2]
        peers[f"{lan}{number}"] = f"10.0.{subnet}.{10 + number}"
    return peers


def wan_delta(before, after):
    deltas = [after[key] - before[key] for key in ("rx", "tx")]
    if min(deltas) < 0:
        raise ValueError("R1 WAN counter decreased during trial")
    return sum(deltas)


def metrics(result, mode):
    seed, others = result["seed_upload_bytes"], result["leecher_upload_bytes"]
    if seed <= 0 or others < 0 or (mode == "client_server" and others != 0):
        raise ValueError("inconsistent seed/leecher upload totals")
    yield "all", "seed_upload_share", seed / (seed + others)
    yield "all", "wan_bytes", wan_delta(result["wan_before"], result["wan_after"])


def write_comparison(directory, rows):
    # Compare mode means at the same count. Savings can legitimately be negative.
    groups = {}
    for row in rows:
        if row["metric"] == "wan_bytes" and row["warmup"] == "false":
            groups.setdefault((row["leechers"], row["variant"]), []).append(row["value"])
    path = directory / "wan-savings.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("leechers", "client_server_mean_bytes", "swarm_mean_bytes",
                         "saved_bytes", "saved_pct"))
        for count in sorted({n for n, _ in groups}):
            server, swarm = (statistics.mean(groups[count, variant])
                             for variant in ("tcp-client-server", "tcp-swarm"))
            writer.writerow((count, server, swarm, server - swarm,
                             100 * (server - swarm) / server if server else None))
