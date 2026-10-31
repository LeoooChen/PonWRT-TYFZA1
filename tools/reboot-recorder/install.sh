#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
umask 077
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo 'Requires aarch64' >&2; exit 1; }
case "$(cat /tmp/sysinfo/board_name)" in
	tianyi,tyfza1|tianyi,tyfza1-2p5g) ;;
	*) echo 'Not a TYFZA1 board; refusing installation' >&2; exit 1;;
esac
awk '$2 == "/overlay" && $3 == "ubifs" && $4 ~ /^rw[,]/ {ok=1} END {exit !ok}' /proc/mounts
[ "$(df -k /overlay | awk 'END {print $4}')" -ge 18432 ] || {
	echo 'At least 18 MiB free overlay space required' >&2; exit 1;
}
sha256sum -c SHA256SUMS
chmod 755 tyfza1-recorderd
./tyfza1-recorderd --version
if [ -e /usr/libexec/tyfza1-recorderd ] || [ -e /etc/init.d/tyfza1-recorder ] || \
	[ -e /usr/sbin/tyfza1-recorder ]; then
	echo 'Recorder already installed; refusing to overwrite. Stop and replace explicitly.' >&2
	exit 1
fi
mkdir -p /usr/libexec /usr/sbin
cp tyfza1-recorderd /usr/libexec/tyfza1-recorderd
cp tyfza1-recorder /usr/sbin/tyfza1-recorder
cp tyfza1-recorder.init /etc/init.d/tyfza1-recorder
chmod 755 /usr/libexec/tyfza1-recorderd /usr/sbin/tyfza1-recorder /etc/init.d/tyfza1-recorder
/etc/init.d/tyfza1-recorder enable
sync
/etc/init.d/tyfza1-recorder start
sleep 2
/etc/init.d/tyfza1-recorder status
echo 'Installed. Reboot not required. Evidence: /overlay/tyfza1-recorder/'
echo 'Reboot-safe, NOT factory-reset/sysupgrade-safe. Export before those operations.'
