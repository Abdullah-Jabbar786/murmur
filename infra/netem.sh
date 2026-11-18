#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
source "$(dirname "$0")/topology-common.sh"

usage() {
    echo "Usage: bash infra/netem.sh apply [--delay MS] [--loss PCT] [--rate MBIT] | clear" >&2
    exit 2
}

action=${1:-}
[[ $action == apply || $action == clear ]] || usage
shift
[[ $action == apply || $# == 0 ]] || usage
delay=10
loss=0
rate=10
while (( $# )); do
    [[ $# -ge 2 && $2 =~ ^(0|[1-9][0-9]*)([.][0-9]+)?$ ]] || usage
    case "$1" in
        --delay) delay=$2 ;;
        --loss) loss=$2 ;;
        --rate) rate=$2 ;;
        *) usage ;;
    esac
    shift 2
done
awk -v loss="$loss" -v rate="$rate" 'BEGIN {exit !(loss <= 100 && rate > 0)}' || usage

# These three interfaces belong to the M1.1 lab. Use a distinctive root
# handle so clear and failure recovery leave unrelated qdiscs alone.
nodes=(murmur-r1 murmur-r2 murmur-seed)
devices=(wan0 wan0 eth0)
root_handle=410:
tc_on() {
    local i=$1
    shift
    ip netns exec "${nodes[i]}" tc qdisc "$@" dev "${devices[i]}"
}
clear_owned() {
    local i state rc=0
    for i in "${!nodes[@]}"; do
        [[ -e /run/netns/${nodes[i]} ]] || continue
        ip -n "${nodes[i]}" link show dev "${devices[i]}" >/dev/null 2>&1 || continue
        state=$(tc_on "$i" show) || { rc=1; continue; }
        if [[ $state == *"qdisc tbf $root_handle root"* ]]; then
            tc_on "$i" del root || rc=1
        fi
    done
    return "$rc"
}
lock_topology
if [[ $action == clear ]]; then
    clear_owned
    exit
fi

# Preflight all three before changing any of them. Stock M1.1 veths use noqueue.
for i in "${!nodes[@]}"; do
    state=$(tc_on "$i" show)
    if [[ $state != *"qdisc noqueue 0: root"* && $state != *"qdisc tbf $root_handle root"* ]]; then
        echo "Unexpected root qdisc on ${nodes[i]}/${devices[i]}: $state" >&2
        exit 1
    fi
done
rollback() {
    local rc=$?
    if (( rc != 0 )); then
        echo "Apply failed. Clearing Murmur shaping on all three interfaces." >&2
        clear_owned || echo "Cleanup failed. Run sudo make netem-clear." >&2
    fi
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
clear_owned
for i in "${!nodes[@]}"; do
    cap=$rate
    if [[ ${nodes[i]} == murmur-seed ]]; then cap=10; fi
    ip netns exec "${nodes[i]}" tc qdisc add dev "${devices[i]}" root handle "$root_handle" \
        tbf rate "${cap}mbit" burst 32kb latency 50ms
    if [[ ${devices[i]} == wan0 ]]; then
        # Netem delays/drops enqueued packets. The parent TBF meters departure.
        ip netns exec "${nodes[i]}" tc qdisc add dev wan0 parent 410:1 handle 420: \
            netem limit 1000 delay "${delay}ms" loss "${loss}%"
    fi
done
echo "WAN per direction: ${delay} ms, ${loss}% loss, ${rate} Mbit/s. Seed uplink: 10 Mbit/s."
