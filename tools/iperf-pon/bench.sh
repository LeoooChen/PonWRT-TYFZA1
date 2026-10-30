#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE=/tmp/iperf-pon-runtime
umask 077

stop() {
	[ -d "$STATE" ] || return 0
	if [ -s "$STATE/iperf.pid" ]; then
		pid=$(cat "$STATE/iperf.pid")
		case "$pid" in *[!0-9]*|'') echo 'Invalid iperf PID' >&2; return 1;; esac
		if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -q 'iperf3'; then
			kill "$pid" || return 1
			sleep 2
		fi
		rm -f "$STATE/iperf.pid"
	fi
	if [ -f "$STATE/firewall" ]; then
		handle=$(nft -a list chain inet fw4 input | sed -n '/comment "iperf-pon-bench"/s/.*# handle \([0-9][0-9]*\).*/\1/p')
		[ -n "$handle" ] && nft delete rule inet fw4 input handle "$handle"
		rm -f "$STATE/firewall"
	fi
	for module in iperf_pon_sink iperf_pon_feed iperf_pon_flow; do
		if [ -f "$STATE/$module" ]; then
			if [ "$module" = iperf_pon_sink ] || [ "$module" = iperf_pon_feed ]; then
				echo 1 > "/sys/module/$module/parameters/release" || return 1
			fi
			rmmod "$module" || return 1
			rm -f "$STATE/$module"
		fi
	done
	if [ -f "$STATE/rule" ]; then
		ip rule del priority 22000 || return 1
		rm -f "$STATE/rule"
	fi
	rm -f "$STATE/iperf.log" "$STATE/output"
	rmdir "$STATE"
}

case "${1:-}" in
stop) stop; exit 0;;
status)
	for module in iperf_pon_flow iperf_pon_feed iperf_pon_sink; do
		[ ! -e "/sys/module/$module/parameters/stats" ] || cat "/sys/module/$module/parameters/stats"
	done
	[ ! -e /sys/class/net/ponfeed0 ] || "$BASE/queue" ponfeed0
	if [ -r "$STATE/output" ]; then
		output=$(cat "$STATE/output")
		case "$output" in
		ap5|ap2) [ ! -r "/sys/class/net/$output/device/radio_tx" ] ||
			grep -E 'PE_NI_sent=|PE_Cause=' "/sys/class/net/$output/device/radio_tx";;
		esac
	fi
	[ ! -e /sys/class/net/ponsink0/queues/rx-0/rps_cpus ] || {
		printf "sink_rps_cpus="; cat /sys/class/net/ponsink0/queues/rx-0/rps_cpus
		awk '/^Udp:/{print}' /proc/net/snmp
	}
	exit 0;;
start) ;;
*) echo "Usage: $0 start tcp|udp ap5|ap2|lan1|lan2|lan3|lan4 PEER_IP PEER_MAC | stop | status" >&2; exit 2;;
esac
[ "$#" = 5 ] || exit 2
protocol=$2 output=$3 peer_ip=$4 peer_mac=$5
case "$protocol" in tcp) udp=0;; udp) udp=1;; *) exit 2;; esac
case "$output" in ap5|ap2|lan1|lan2|lan3|lan4) ;; *) exit 2;; esac
[ -d "/sys/class/net/$output" ] || { echo "Output $output unavailable" >&2; exit 1; }
[ "$(cat "/sys/class/net/$output/carrier")" = 1 ] || { echo "Output $output has no link" >&2; exit 1; }
if ip rule list | grep -q '^22000:'; then echo 'IP rule priority 22000 already in use' >&2; exit 1; fi
if nft -a list chain inet fw4 input | grep -q 'comment "iperf-pon-bench"'; then
	echo 'Existing iperf-pon firewall rule' >&2; exit 1
fi
mkdir "$STATE" || { echo 'iperf-pon already active or needs cleanup' >&2; exit 1; }
trap 'stop' EXIT HUP INT TERM
printf '%s\n' "$output" > "$STATE/output"
insmod "$BASE/iperf_pon_flow.ko" peer_ip="$peer_ip" peer_mac="$peer_mac" output="$output" udp="$udp"
touch "$STATE/iperf_pon_flow"
insmod "$BASE/iperf_pon_feed.ko" output="$output" udp="$udp" eq_backpressure="${EQ_BACKPRESSURE:-0}"
touch "$STATE/iperf_pon_feed"
if [ "$(cat /sys/module/iperf_pon_feed/parameters/init_error)" != 0 ]; then
	echo "PON feed initialization failed; releasing DMA" >&2; exit 1
fi
sysctl -w net.ipv6.conf.ponfeed0.disable_ipv6=1 >/dev/null
ip link set ponfeed0 up
[ "${EQ_BACKPRESSURE:-0}" != 1 ] || "$BASE/queue" ponfeed0 bfifo
ip addr add 198.18.255.1/32 dev ponfeed0
ip route add table 220 "$peer_ip/32" dev ponfeed0 src 198.18.255.1
ip rule add from 198.18.255.1/32 table 220 priority 22000
touch "$STATE/rule"
"$BASE/set_peer" "$peer_ip" "$peer_mac"
insmod "$BASE/iperf_pon_sink.ko" input="$output" peer_ip="$peer_ip" peer_mac="$peer_mac" udp="$udp" udp_gro="${UDP_GRO:-0}"
touch "$STATE/iperf_pon_sink"
if [ "$(cat /sys/module/iperf_pon_sink/parameters/init_error)" != 0 ]; then
	echo "Sink rule installation failed; releasing endpoint" >&2; exit 1
fi
# Keep NI polling on CPU2 and spread sink processing over CPU1 and CPU3.
# The netdevice is temporary; this setting disappears when it is removed.
echo "${RPS_CPUS:-a}" > /sys/class/net/ponsink0/queues/rx-0/rps_cpus
nft insert rule inet fw4 input iifname ponsink0 ip saddr "$peer_ip" ip daddr 198.18.255.1 "$protocol" dport 5206 accept comment 'iperf-pon-bench'
touch "$STATE/firewall"
iperf=${IPERF3:-iperf3}
export LD_LIBRARY_PATH=${IPERF_LIB_DIR:-$BASE}
"$iperf" -s -B 198.18.255.1 -p 5206 -D -I "$STATE/iperf.pid" --logfile "$STATE/iperf.log"
for attempt in 1 2 3 4 5; do [ -s "$STATE/iperf.pid" ] && break; sleep 1; done
[ -s "$STATE/iperf.pid" ] && kill -0 "$(cat "$STATE/iperf.pid")"
if [ -n "${IPERF_CPU_MASK:-}" ]; then
	taskset -p "$IPERF_CPU_MASK" "$(cat "$STATE/iperf.pid")" >/dev/null
fi
cat "$STATE/iperf.pid" > /sys/module/iperf_pon_feed/parameters/server_pid
trap - EXIT HUP INT TERM
echo "iperf3 server ready: PON source7 -> $output, $protocol, peer $peer_ip"
