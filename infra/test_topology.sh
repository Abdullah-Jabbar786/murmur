#!/usr/bin/env bash
# Run on a Linux VM from a clean lab. Other named namespaces may exist.
set -euo pipefail
cd "$(dirname "$0")/.."
source infra/topology-common.sh

namespace_names() {
    ip netns list | awk '{print $1}' | LC_ALL=C sort
}
before=$(namespace_names)
while read -r ns; do
    if [[ $ns == murmur-* ]] && is_lab_node "${ns#murmur-}"; then
        echo "Existing lab detected. Run sudo make topo-down before this test." >&2
        exit 1
    fi
done <<< "$before"
trap 'bash infra/teardown.sh' EXIT

probe() {
    bash infra/run_in_ns.sh "$1" ping -n -c 1 -W 2 "$2" >/dev/null
}
expect_route() {
    local router=$1 target=$2 gateway=$3 route
    route=$(ip -n "murmur-$router" route get "$target")
    [[ $route == *"via $gateway dev wan0 "* ]] || { echo "Unexpected route: $route" >&2; return 1; }
}
make topo LEECHERS_PER_LAN=5
for ((i=1; i<=5; i++)); do
    probe "a$i" 10.0.2.$((10+i))
    probe "b$i" 10.0.3.11
done
probe tracker 10.0.2.11
probe dashboard 10.0.1.11
expect_route r1 10.0.2.11 10.0.12.2
expect_route r2 10.0.1.11 10.0.12.1
expect_route r2 10.0.3.11 10.0.12.1
for endpoint in 'r1 wan0' 'r2 wan0' 'r1 p-a1' 'seed eth0'; do
    read -r node device <<< "$endpoint"
    features=$(bash infra/run_in_ns.sh "$node" ethtool -k "$device")
    for feature in tcp-segmentation-offload generic-segmentation-offload generic-receive-offload; do
        grep -Eq "^$feature: off( |$)" <<< "$features"
    done
done
echo "PASS: standard topology, routed traffic, WAN routes and offloads"

make topo LEECHERS_PER_LAN=10
probe a10 10.0.2.20
probe b10 10.0.3.10
echo "PASS: repeated setup with 20 leechers"
ip netns del murmur-r2
make topo-down
make topo-down
after=$(namespace_names)
[[ $before == "$after" ]] || { echo "Namespace inventory differs after teardown" >&2; exit 1; }
trap - EXIT
echo "PASS: partial-state teardown, repeated cleanup, original namespace inventory restored"
