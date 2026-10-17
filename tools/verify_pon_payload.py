#!/usr/bin/env python3
"""Verify PON components and public configuration in a built TYFZA1 payload."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import posixpath
from fit_payload import embedded_rootfs, fit_images

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("payload", type=Path)
parser.add_argument("--build-unit", required=True)
parser.add_argument("--output", type=Path, default=root / "manifests/bosa-luci-build.json")
args = parser.parse_args()
fit = json.loads((args.payload / 'ram-pon/manifest.json').read_text())
fit_path = args.payload / 'ram-pon/image.itb'
assert hashlib.sha256(fit_path.read_bytes()).hexdigest() == fit['fit_sha256']
actual = fit_images(fit_path)
kernel = actual['kernel']
assert hashlib.sha256(kernel).hexdigest() == fit['image_sha256'], 'FIT kernel differs from manifest'
assert hashlib.sha256(actual['fdt']).hexdigest() == fit['dtb_sha256'], 'FIT DTB differs from manifest'
external_kernel = next((args.payload / 'firmware').glob('*initramfs-Image')).read_bytes()
assert kernel == external_kernel, 'Final FIT kernel does not match firmware directory'
external_dtb = args.payload / 'firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-pon-status.dtb'
assert actual['fdt'] == external_dtb.read_bytes(), 'Final FIT DTB does not match firmware directory'
embedded, initramfs_start, initramfs_end = embedded_rootfs(kernel)
archive = args.payload / "firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-rootfs.tar.gz"
files = ["etc/init.d/pon", "etc/config/pon", "usr/share/tyfza1/factory.uc",
         "usr/share/rpcd/ucode/luci.tyfza1-pon", "usr/share/rpcd/acl.d/luci-app-tyfza1-pon.json",
         "usr/share/luci/menu.d/luci-app-tyfza1-pon.json", "www/luci-static/resources/view/network/tyfza1-pon.js"]
files.append("usr/sbin/pon-oamd")
files.append('etc/uci-defaults/90-tyfza1-pon-wan')
with tarfile.open(archive) as tar:
    members = {member.name.removeprefix("./"): member for member in tar.getmembers()}
    checks = []
    for name in files:
        data = embedded[name]['data']
        assert data == tar.extractfile(members[name]).read(), f'FIT initramfs differs from rootfs: {name}'
        assert embedded[name]['mode'] & 0o777 == members[name].mode & 0o777
        checks.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "mode": oct(members[name].mode & 0o777)})
    # Every external regular file/symlink must belong to this same initramfs.
    for name, member in members.items():
        if member.isfile():
            assert name in embedded and embedded[name]['data'] == tar.extractfile(member).read(), name
        elif member.issym():
            def destination(link):
                return posixpath.normpath(posixpath.join(posixpath.dirname(name), link.split('\0', 1)[0])).lstrip('/')
            assert name in embedded and destination(embedded[name]['data'].decode()) == destination(member.linkname), name
    config = embedded['etc/config/pon']['data']
    assert config == (root / 'port/overlay/target/linux/rtl9617c/base-files/etc/config/pon').read_bytes()
    assert 'lib/firmware/realtek/rtl9617c/bosa-calibration.bin' not in embedded
    assert members['etc/config/pon'].mode & 0o777 == 0o600
    assert members['etc/rc.d/S09tyfza1-factory'].issym()
    assert not any(name.startswith('etc/dropbear/dropbear_') for name in embedded)
    runtime = json.loads((root / 'artifacts/pon-runtime-manifest.json').read_text())
    assert runtime['embedded_subscriber_identity'] is False
    for profile in runtime['profiles'].values():
        blob = embedded['lib/firmware/realtek/rtl9617c/' + profile['firmware']]['data']
        assert hashlib.sha256(blob).hexdigest() == profile['firmware_sha256']
    for name in ("epon", "epon_d10g", "epon_bi10g", "gpon", "xgpon", "xgspon"):
        assert f"lib/firmware/realtek/rtl9617c/pon_serdes_{name}.bin" in members
    service = embedded['etc/init.d/pon']['data'].decode()
    assert service.index('> "$device/bosa"') < service.index('> "$device/serdes"')
    assert '/usr/sbin/pon-oamd --interface' in service
    assert 'ip link set "pon$i" up' in service
    defaults = embedded['etc/uci-defaults/90-tyfza1-pon-wan']['data'].decode()
    assert "network.wan.device='pon0'" in defaults and "network.wan.proto='dhcp'" in defaults
    assert members["etc/rc.d/S25pon"].issym()
    acl = json.loads(tar.extractfile(members[files[4]]).read())
    assert "status" in acl["luci-app-tyfza1-pon"]["read"]["ubus"]["luci.tyfza1-pon"]
for marker in (b'silence_scope=shared-engine', b'refill_pending=%u', b'refill_retries=%u',
               b'pon%u', b'buffers_per_pool=%u', b'pon_deepq'):
    assert marker in kernel, marker
record = {"payload": str(args.payload.relative_to(root)) if args.payload.is_absolute() else str(args.payload),
          "build_unit": args.build_unit, "required_files": checks,
          "per_device_calibration_loaded_at_boot": True, "six_protocol_profiles": True,
          "private_identity_injected": False, "identity_file_mode": "0600", "WAN_protocol": "dhcp",
          "runtime_matches_current_preparation": True,
          "corrected_NACK_and_refill_in_kernel": True,
          "verification_entry": "final FIT selected configuration",
          "FIT_kernel_DTB_and_initramfs_match_external_files": True,
          "initramfs": {"start": initramfs_start, "end": initramfs_end, "members": len(embedded)},
          "BOSA_precedes_SerDes": True, "S25pon_enabled": True,
          "per_LLID_business_devices_in_kernel": True, "PON_DHCP_WAN_default": True,
          "hardware_boot_verified": False, "FIT": fit}
args.output.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({key: value for key, value in record.items() if key not in ("required_files", "FIT")}, indent=2))
