#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -e

/usr/libexec/network/packet-steering.uc "$@"

case "$1" in
	""|1) ni_cpus=4 ;;
	*) ni_cpus=f ;;
esac

# Keep the NI receive interrupt off the CPU servicing the radio interrupts.
for irq in $(awk '$NF == "rtl9617c-rx0" { sub(":", "", $1); print $1 }' /proc/interrupts); do
	echo "$ni_cpus" > "/proc/irq/$irq/smp_affinity"
	napi=/sys/class/net/lan4/threaded
	case "$ni_cpus" in
		4)
			echo 1 > "$napi"
			for process in /proc/[0-9]*; do
				read -r name 2>/dev/null < "$process/comm" || continue
				case "$name" in
					napi/lan4-*)
						taskset -p "$ni_cpus" "${process##*/}" > /dev/null
						break
						;;
				esac
			done
			;;
		*) echo 0 > "$napi" ;;
	esac
done

case "$1" in
	""|1) ;;
	*) exit 0 ;;
esac

# Balance local Wi-Fi ingress across CPU1 and CPU3. CPU2 polls the NI
# queue and CPU0 services the radio interrupts. PE forwarding bypasses RPS.
for driver in /sys/class/net/*/device/driver; do
	case "$(readlink "$driver")" in
		*/rtl_ax_ap)
			for queue in "${driver%/device/driver}"/queues/rx-*/rps_cpus; do
				echo a > "$queue"
			done
			;;
	esac
done
