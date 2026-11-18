# Shared by the Linux topology commands. The caller sets Bash strict mode.
is_lab_node() {
    [[ $1 =~ ^(r1|r2|tracker|seed|dashboard|[ab]([1-9]|[1-9][0-9]|1[0-9][0-9]|2[0-3][0-9]|24[0-4]))$ ]]
}

lock_topology() {
    if (( EUID != 0 )); then
        echo "Run this topology command with sudo." >&2
        return 1
    fi
    exec 9>/run/lock/murmur-topology.lock
    flock -x 9
}

remove_topology() {
    local listing ns rest pids
    local -a nodes=()
    listing=$(ip netns list) || return 1
    while read -r ns rest; do
        [[ $ns == murmur-* ]] && is_lab_node "${ns#murmur-}" || continue
        nodes+=("$ns")
    done <<< "$listing"

    # Removing a name while it has live processes leaves a hidden namespace.
    for ns in "${nodes[@]}"; do
        pids=$(ip netns pids "$ns") || return 1
        if [[ -n $pids ]]; then
            echo "$ns is in use by PID(s): $pids. Stop its workloads first." >&2
            return 1
        fi
    done
    for ns in "${nodes[@]}"; do
        ip netns del "$ns" || return 1
    done
}
