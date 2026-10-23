# SPDX-License-Identifier: GPL-2.0-only

tyf_factory_published() {
	[ "$(cat /sys/class/ubi/ubi0_12/name 2>/dev/null)" = tyfza1_factory ] ||
		[ "$(cat /sys/class/ubi/ubi0_13/name 2>/dev/null)" = tyfza1_factory_backup ]
}

# Run in a subshell: callers can try the second copy after a failed read.
tyf_factory_unpack() (
	set -eu
	set -o pipefail
	local device="$1" directory="$2" full="${3:-0}" component expected actual
	local sys="/sys/class/ubi/${device##*/}"
	[ "$(cat "$sys/type")" = static ]
	[ "$(cat "$sys/upd_marker")" = 0 ]
	mkdir -p "$directory"
	cat "$device" > "$directory/capsule.tar"
	tar -xf "$directory/capsule.tar" -C "$directory" \
		format config.ubifs.gz apps.ubifs.gz config-files.tar.gz \
		files.txt raw.sha256 SHA256SUMS
	cd "$directory"
	sha256sum -c SHA256SUMS >/dev/null
	[ "$(cat format)" = TYFZA1_FACTORY_1 ]
	if [ "$full" = 1 ]; then
		for component in config apps; do
			expected=$(sed -n "s/  $component.ubifs\$//p" raw.sha256)
			actual=$(gzip -dc "$component.ubifs.gz" | sha256sum)
			[ "${actual%% *}" = "$expected" ]
		done
	fi
	mkdir config
	gzip -dc config-files.tar.gz | tar -xf - -C config
)
