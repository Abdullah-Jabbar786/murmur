#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/topology-common.sh"
lock_topology
remove_topology
