#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Run on the board's native monitor interface; retain raw 802.11 and FCS.
set -eu
INTERFACE=${1:?monitor interface required}
OUTPUT=${2:?pcap output path required}
COUNT=${3:-64}
# Actual 802.11 version0 Data/QoS Data FC bytes. On this image libpcap
# 1.10.7 misoptimizes the masked OR expression into JEQ128 rather than
# testing the QoS FC byte136; this exact comparison matches both types.
exec tcpdump -Q in -i "$INTERFACE" -s 0 -c "$COUNT" -w "$OUTPUT" \
    'wlan[0] = 0x08 or wlan[0] = 0x88'
