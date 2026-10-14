#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run on TYFZA1 during a transfer. qm_state counters are read-clear.
# radio_mac80211 copy/timing counters require its reported profile=1.
set -eu
SAMPLES=${1:-15}
RADIO=${2:-0000:01:00.0}
AP=${3:-ap5}
NI=/sys/class/net/eth3/device
PCI=/sys/bus/pci/devices/$RADIO
uname -r
iw dev "$AP" station dump
cat /tmp/dhcp.leases
cat "$PCI/radio_state"
cat /sys/class/net/pon-data0/device/fe
cat "$PCI/radio_tx"
cat "$PCI/radio_rx"
for COUNTER in $(seq 1 "$SAMPLES"); do
    cat /proc/uptime
    head -n 5 /proc/stat
    cat "$NI/qm_state"
    cat "$NI/datapath_profile"
    cat "$PCI/radio_mac80211"
    cat "$PCI/radio_tx" | grep -E 'RPQ_release_config=|PE_slot=|PE_NI_MPDU=|PE_NI_sent=|PE_downlink_peer='
    cat "$PCI/radio_rx" | grep -E 'rxq=|PE_uplink_frames=|VP[0-9]+_submitted=|PE_uplink_slow_pending=|PE_uplink_slow_format=|PE_RXQ_buffer_pool=|PE_RXQ_scanned=|PE_RPQ_owned='
    sleep 1
done
cat "$PCI/radio_tx"
cat "$PCI/radio_rx"
cat /sys/class/net/pon-data0/device/fe
