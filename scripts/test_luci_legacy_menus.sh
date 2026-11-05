#!/bin/sh
# Exercise real APK installation and LuCI menu generation in an ARM64 chroot.
set -eu

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 ROOTFS EASYTIER_APK OPENCLASH_APK" >&2
    exit 1
fi
source_root=$(realpath "$1")
easytier=$(realpath "$2")
openclash=$(realpath "$3")
# /tmp is commonly mounted nodev; the chroot needs working device nodes.
root=$(mktemp -d "${TMPDIR:-$PWD}/tyfza1-luci-test.XXXXXXXX")
ubusd_pid=
cleanup() {
    if [ -n "$ubusd_pid" ]; then
        kill "$ubusd_pid" 2>/dev/null || true
        wait "$ubusd_pid" 2>/dev/null || true
    fi
    rm -rf "$root"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

cp -a "$source_root/." "$root/"
mkdir -p "$root/rom/lib/apk/db" "$root/dev" "$root/tmp" "$root/var/run/ubus"
cp "$root/lib/apk/db/installed" "$root/rom/lib/apk/db/installed"
rm -f "$root/etc/resolv.conf"
cp /etc/resolv.conf "$root/etc/resolv.conf"
for name in null zero random urandom; do
    case "$name" in null) minor=3;; zero) minor=5;; random) minor=8;; urandom) minor=9;; esac
    [ -e "$root/dev/$name" ] || mknod -m 666 "$root/dev/$name" c 1 "$minor"
    chmod 666 "$root/dev/$name"
done
chroot "$root" /bin/sh -c ': > /dev/null'
cp "$easytier" "$root/tmp/easytier.apk"
cp "$openclash" "$root/tmp/openclash.apk"

chroot "$root" /bin/sh /etc/uci-defaults/98-tyfza1-apk-compat
chroot "$root" /bin/sh /etc/uci-defaults/99-tyfza1-platform-packages
chroot "$root" /usr/bin/apk update
before=$(sha256sum "$root/usr/lib/ucode/lua.so" | cut -d' ' -f1)
# Do not start services or modify the host's network when installing test packages.
chroot "$root" /usr/bin/apk --no-scripts --force-non-repository add --allow-untrusted /tmp/easytier.apk
chroot "$root" /usr/bin/apk --no-scripts --force-non-repository add --allow-untrusted /tmp/openclash.apk
after=$(sha256sum "$root/usr/lib/ucode/lua.so" | cut -d' ' -f1)
test "$before" = "$after"
chroot "$root" /usr/bin/ucode -e 'print(require("lua"))'

chroot "$root" /sbin/ubusd -s /var/run/ubus/ubus.sock >"$root/tmp/ubusd-test.log" 2>&1 &
ubusd_pid=$!
sleep 1
rm -f "$root"/tmp/luci-indexcache.*
chroot "$root" /usr/bin/env REQUEST_METHOD=GET SCRIPT_NAME=/cgi-bin/luci \
    PATH_INFO=/admin SERVER_NAME=localhost SERVER_PORT=80 QUERY_STRING= \
    /www/cgi-bin/luci >"$root/tmp/luci-cgi-test.out"
python3 - "$root" <<'PY'
import glob, json, sys
paths = glob.glob(sys.argv[1] + '/tmp/luci-indexcache.*.json')
assert len(paths) == 1, paths
with open(paths[0]) as stream:
    admin = json.load(stream)['children']['admin']['children']
assert 'easytier' in admin['vpn']['children']
assert 'openclash' in admin['services']['children']
print('LuCI menus: VPN/EasyTier and Services/OpenClash passed')
PY
