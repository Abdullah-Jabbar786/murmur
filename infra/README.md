# Murmur routed lab

Run the commands below in the repository root inside Ubuntu. Setup requires
root, Bash, GNU Make, iproute2 (`ip`), ethtool, procps (`sysctl`) and util-linux
(`flock`). The test also uses ping. Run `sudo make test-topo` on a clean lab
for the routed acceptance checks. It creates and removes the lab itself.

```bash
sudo make topo
sudo bash infra/run_in_ns.sh a1 ping -c 2 10.0.2.11
sudo bash infra/run_in_ns.sh a1 traceroute -n -I 10.0.2.11
sudo bash infra/run_in_ns.sh r2 ip route
sudo make topo-down
```

The traceroute from a1 to b1 should pass through R1 and R2. The default
is five leechers per LAN. Use `sudo make topo LEECHERS_PER_LAN=10` for 20
total, or `sudo bash infra/topology.sh --leechers-per-lan 10`. Values 1–244
fit the addressing scheme below.

| Segment | Router interface | Host names and addresses |
|---|---|---|
| LAN A | R1 `br-a` at 10.0.1.1/24 | a1 at 10.0.1.11, a2 at .12, onwards |
| LAN B | R2 `br-b` at 10.0.2.1/24 | b1 at 10.0.2.11, b2 at .12, onwards |
| LAN C | R1 `br-c` at 10.0.3.1/24 | tracker .10, seed .11, dashboard .12 |
| WAN | `wan0` on both routers | R1 10.0.12.1/30, R2 10.0.12.2/30 |

Each host uses `eth0` and a default route to its LAN gateway. Each router
bridge owns ports named `p-<host>`. Only the two routers need forwarding.
Setup disables TSO, GSO and GRO on both endpoints of each veth pair.
The dashboard namespace reserves its address for a future workload.

The lab reserves namespace names `murmur-r1`, `murmur-r2`, `murmur-tracker`,
`murmur-seed`, `murmur-dashboard`, and `murmur-a1` through `murmur-a244`
and their `b` equivalents. Setup rebuilds any idle namespaces in that set.
Stop running commands in them before rebuilding or tearing down. Setup and
teardown serialize using `/run/lock/murmur-topology.lock`. All virtual links
and bridges live within the reserved namespaces. After an interruption, rerun
`sudo make topo-down`. The test checks the namespace list against its starting
inventory after both partial-state and repeated teardown.

## WAN and seed shaping (M1.2)

From the Ubuntu repository root, build the topology, then apply the baseline:

```bash
sudo make topo
sudo make netem
sudo bash infra/run_in_ns.sh r1 tc -s qdisc show dev wan0
sudo bash infra/run_in_ns.sh r2 tc -s qdisc show dev wan0
sudo bash infra/run_in_ns.sh seed tc -s qdisc show dev eth0
sudo make netem ARGS="--delay 50 --loss 2 --rate 10"
sudo make netem-clear
sudo make topo-down
```

`bash infra/netem.sh apply` accepts the same flags. Each apply starts from
defaults: 10 ms **per WAN direction** (20 ms added RTT), 0% independent
packet loss per direction, and 10 Mbit/s on each WAN egress. For E2 use
one-way delays of 10, 50, or 100 ms. An ICMP echo crosses the WAN twice:
with loss probability p per direction its expected loss is 1 − (1 − p)².
`--rate` changes the WAN only. The seed's egress stays at 10 Mbit/s.

On each router's wan0, netem (handle 420:, 1000-packet queue) delays and
drops packets before the parent token bucket (410:, 32 KiB burst) meters
transmission. The seed uses a standalone TBF on eth0 with a 50 ms queue
latency setting. Each apply replaces the previous lab profile. A failure
during installation clears the lab profile on all three endpoints. Clear
can be repeated. Rebuilding the topology removes shaping with its veths.

On a clean lab run `sudo make test-netem` (requires iperf3, iproute2,
iputils-ping and Python 3). It measures the added idle RTT, receiver TCP
goodput in both WAN directions, an overridden WAN rate, and the seed upload
to LAN A on a path bypassing the WAN. It samples 200 pings at 10% loss each
way and checks for drops at both routers. Finally it clears twice, checks
the original queues and idle RTT, and removes the test topology.
The printed rates are application payload Mbit/s. The 10 Mbit/s acceptance
band is 8–10.8 Mbit/s to allow for protocol overhead and VM scheduling.
