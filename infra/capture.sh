#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

# Foreground capture. Its caller owns stdout (classic pcap) and stops it
# with SIGINT so tcpdump flushes the file and reports its packet counters.
protocol=${2-tcp}
if [[ $# -lt 1 || $# -gt 2 || ! $1 =~ ^[1-9][0-9]{0,4}$ || ! $protocol =~ ^(tcp|udp)$ ]] || (( $1 > 65535 )); then
    echo "Usage: bash infra/capture.sh <swarm port> [tcp|udp] > trace.pcap" >&2
    exit 2
fi
if [[ -t 1 ]]; then
    echo "Redirect the binary capture to a .pcap file." >&2
    exit 2
fi
command -v tcpdump >/dev/null || { echo "Install tcpdump in Ubuntu first." >&2; exit 1; }
exec bash "$(dirname "$0")/run_in_ns.sh" r1 tcpdump -p -nn -i wan0 -s 128 -w - \
    "$protocol port $1 and net 10.0.2.0/24 and (net 10.0.1.0/24 or net 10.0.3.0/24)"
