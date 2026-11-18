#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/topology-common.sh"
if (( $# < 2 )) || ! is_lab_node "${1#murmur-}"; then
    echo "Usage: bash infra/run_in_ns.sh <r1|r2|tracker|seed|dashboard|aN|bN> <command> [args...]" >&2
    exit 2
fi
ns="murmur-${1#murmur-}"
shift
exec ip netns exec "$ns" "$@"
