#!/bin/sh
# Capture during a known transfer; reading these flow counters does not clear them.
set -eu
snapshot() {
    date -Iseconds
    cat /sys/bus/platform/devices/4f5500000.pon/fe
    cat /sys/kernel/debug/rtl9617c-fe/flows
    cat /proc/stat
    cat /proc/softirqs
    ubus call network.interface.wan status
}
snapshot
sleep "${1:-10}"
snapshot
