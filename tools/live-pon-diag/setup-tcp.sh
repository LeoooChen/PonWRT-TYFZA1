#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

irq=$(awk '$NF == "rtl9617c-rx0" {sub(":", "", $1); print $1}' /proc/interrupts)
rx_mask=$(cat "/proc/irq/$irq/effective_affinity" | tr -d ',')
allowed=$(awk '$1 == "Cpus_allowed:" {gsub(",", "", $2); print $2}' /proc/self/status)
server_mask=$(printf '%x' "$((0x$allowed & ~0x$rx_mask))")
test "$server_mask" != 0

cd /tmp/tyf-wifi-local
insmod ./pon_wifi_sim.ko tcp=1
insmod ./pon_tcp_feed.ko
sysctl -w net.ipv6.conf.ponfeed0.disable_ipv6=1
ip link set ponfeed0 up
ip addr add 198.18.255.1/32 dev ponfeed0
ip route add table 220 192.168.1.212/32 dev ponfeed0 src 198.18.255.1
ip rule add from 198.18.255.1/32 table 220 priority 22000
./set-peer
insmod ./pon_tcp_sink_final.ko
echo 8 > /sys/class/net/ponsink0/queues/rx-0/rps_cpus
# Software GSO/checksum/TX runs with BH disabled. Keep the generator off
# the NI NAPI CPU; otherwise a TX burst stalls RX and causes L3 tail drops.
rm -f tcp.pid
LD_LIBRARY_PATH=$PWD taskset "$server_mask" ./iperf3 -s -B 198.18.255.1 -p 5206 -D -I "$PWD/tcp.pid"
for attempt in 1 2 3 4 5 6 7 8 9 10; do
	if test -s tcp.pid; then break; fi
	sleep 0.1
done
pid=$(cat tcp.pid)
kill -0 "$pid"
echo "$pid" > /sys/module/pon_tcp_feed/parameters/server_pid
printf 'NI IRQ%s mask=%s; TCP generator mask=%s pid=%s\n' "$irq" "$rx_mask" "$server_mask" "$pid"
