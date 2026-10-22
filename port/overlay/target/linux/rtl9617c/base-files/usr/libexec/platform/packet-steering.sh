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
done

case "$1" in
	""|1) ;;
	*) exit 0 ;;
esac

# ARM already handles RXQ; an additional RPS hop costs forwarding throughput.
for driver in /sys/class/net/*/device/driver; do
	case "$(readlink "$driver")" in
		*/rtl_ax_ap)
			for queue in "${driver%/device/driver}"/queues/rx-*/rps_cpus; do
				echo 0 > "$queue"
			done
			;;
	esac
done
