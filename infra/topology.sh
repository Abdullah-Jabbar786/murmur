#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/topology-common.sh"

k=5
if (( $# )); then
    if [[ $# != 2 || $1 != --leechers-per-lan || ! $2 =~ ^[1-9][0-9]{0,2}$ ]] || (( $2 > 244 )); then
        echo "Usage: bash infra/topology.sh [--leechers-per-lan 1..244]" >&2
        exit 2
    fi
    k=$2
fi

for tool in ip ethtool sysctl flock; do
    command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done
lock_topology
remove_topology

# All links are born in named namespaces, so deleting the names also cleans
# up a build interrupted between creation and configuration of a link.
cleanup_on_exit() {
    local rc=$?
    if (( rc != 0 )); then remove_topology || echo "Retry make topo-down." >&2; fi
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

new_node() {
    ip netns add "murmur-$1"
    ip -n "murmur-$1" link set lo up
}

bridge_lan() {
    local router=$1 bridge=$2 gateway=$3
    ip -n "$router" link add "$bridge" type bridge
    ip -n "$router" addr add "$gateway/24" dev "$bridge"
    ip -n "$router" link set "$bridge" up
}

disable_offloads() {
    ip netns exec "$1" ethtool -K "$2" tso off gso off gro off >/dev/null
}

add_host() {
    local router=$1 bridge=$2 node=$3 prefix=$4 last=$5
    local ns="murmur-$node" port="p-$node"
    new_node "$node"
    ip -n "$router" link add "$port" type veth peer name eth0 netns "$ns"
    ip -n "$router" link set "$port" master "$bridge"
    disable_offloads "$router" "$port"
    disable_offloads "$ns" eth0
    ip -n "$router" link set "$port" up
    ip -n "$ns" addr add "$prefix.$last/24" dev eth0
    ip -n "$ns" link set eth0 up
    ip -n "$ns" route add default via "$prefix.1"
}

r1=murmur-r1
r2=murmur-r2
new_node r1
new_node r2
bridge_lan "$r1" br-a 10.0.1.1
bridge_lan "$r1" br-c 10.0.3.1
bridge_lan "$r2" br-b 10.0.2.1
ip -n "$r1" link add wan0 type veth peer name wan0 netns "$r2"
ip -n "$r1" addr add 10.0.12.1/30 dev wan0
ip -n "$r2" addr add 10.0.12.2/30 dev wan0
for router in "$r1" "$r2"; do
    disable_offloads "$router" wan0
    ip -n "$router" link set wan0 up
    ip netns exec "$router" sysctl -qw net.ipv4.ip_forward=1
done
ip -n "$r1" route add 10.0.2.0/24 via 10.0.12.2
ip -n "$r2" route add 10.0.1.0/24 via 10.0.12.1
ip -n "$r2" route add 10.0.3.0/24 via 10.0.12.1

add_host "$r1" br-c tracker 10.0.3 10
add_host "$r1" br-c seed 10.0.3 11
add_host "$r1" br-c dashboard 10.0.3 12
for ((i=1; i<=k; i++)); do
    add_host "$r1" br-a "a$i" 10.0.1 "$((10+i))"
    add_host "$r2" br-b "b$i" 10.0.2 "$((10+i))"
done
echo "Murmur topology ready: $k leechers per LAN."
