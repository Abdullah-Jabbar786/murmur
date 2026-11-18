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
Shaping and impairment belong to M1.2. The dashboard namespace reserves
its address for a future workload.

The lab reserves namespace names `murmur-r1`, `murmur-r2`, `murmur-tracker`,
`murmur-seed`, `murmur-dashboard`, and `murmur-a1` through `murmur-a244`
and their `b` equivalents. Setup rebuilds any idle namespaces in that set.
Stop running commands in them before rebuilding or tearing down. Setup and
teardown serialize using `/run/lock/murmur-topology.lock`. All virtual links
and bridges live within the reserved namespaces. After an interruption, rerun
`sudo make topo-down`. The test checks the namespace list against its starting
inventory after both partial-state and repeated teardown.
