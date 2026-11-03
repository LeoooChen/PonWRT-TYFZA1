#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Exercise procd arguments without starting hardware or a network process.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$root/port/packages/tyfza1-fttr/files/tyfza1-fttr-sync.init"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
policy_path="$work/private policy.json"
: > "$policy_path"
chmod 600 "$policy_path"
config_load() { test "$1" = tyfza1-fttr-sync; }
config_get_bool() { enabled=$test_enabled; }
config_get() {
	case "$3" in
		interface) interface=$test_interface ;;
		ethertype) ethertype=0x88ab ;;
		policy) policy=$policy_path ;;
		*) return 1 ;;
	esac
}
procd_open_instance() { echo open >> "$work/calls"; }
procd_close_instance() { echo close >> "$work/calls"; }
procd_set_param() {
	if [ "$1" = command ]; then
		test "$#" = 6
		test "$2" = /usr/sbin/fttr-sync
		test "$3" = bridge-test
		test "$4" = serve
		test "$5" = 0x88ab
		test "$6" = "$policy_path"
	fi
	test "$1" != respawn
	echo "$1" >> "$work/calls"
}
test_enabled=0
test_interface=bridge-test
start_service
test ! -e "$work/calls"
test_enabled=1
test_interface=''
if start_service 2> "$work/error"; then exit 1; fi
test ! -e "$work/calls"
test_interface=bridge-test
start_service
test "$(cat "$work/calls")" = 'open
command
term_timeout
stdout
stderr
close'
printf '%s\n' 'sync init: disabled default, explicit interface/private policy, quoted procd arguments, no respawn passed'
