#!/usr/bin/env python3
"""Verify PON components and private configuration in a built TYFZA1 payload."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("payload", type=Path)
parser.add_argument("--build-unit", required=True)
parser.add_argument("--output", type=Path, default=root / "manifests/bosa-luci-build.json")
args = parser.parse_args()
archive = args.payload / "firmware/openwrt-rtl9617c-generic-tianyi_tyfza1-rootfs.tar.gz"
files = ["etc/init.d/pon", "etc/config/pon", "lib/firmware/realtek/rtl9617c/bosa-calibration.bin",
         "usr/share/rpcd/ucode/luci.tyfza1-pon", "usr/share/rpcd/acl.d/luci-app-tyfza1-pon.json",
         "usr/share/luci/menu.d/luci-app-tyfza1-pon.json", "www/luci-static/resources/view/network/tyfza1-pon.js"]
files.append("usr/sbin/pon-oamd")
with tarfile.open(archive) as tar:
    members = {member.name.removeprefix("./"): member for member in tar.getmembers()}
    checks = []
    for name in files:
        data = tar.extractfile(members[name]).read()
        checks.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "mode": oct(members[name].mode & 0o777)})
    calibration = tar.extractfile(members[files[2]]).read()
    assert calibration == (root / "analysis/private/live-files/var/config/rtkbosa_k.bin").read_bytes()
    auth = json.loads((root / "analysis/private/pon-auth.json").read_text())
    config = tar.extractfile(members["etc/config/pon"]).read().decode()
    assert "option loid '" + auth["loid"] + "'" in config
    assert "option wan_protocol 'dhcp'" in config
    assert members["etc/config/pon"].mode & 0o777 == 0o600
    runtime = json.loads((root / 'artifacts/pon-runtime-manifest.json').read_text())
    assert hashlib.sha256(config.encode()).hexdigest() == runtime['identity']['uci_sha256']
    for profile in runtime['profiles'].values():
        blob = tar.extractfile(members['lib/firmware/realtek/rtl9617c/' + profile['firmware']]).read()
        assert hashlib.sha256(blob).hexdigest() == profile['firmware_sha256']
    for name in ("epon", "epon_d10g", "epon_bi10g", "gpon", "xgpon", "xgspon"):
        assert f"lib/firmware/realtek/rtl9617c/pon_serdes_{name}.bin" in members
    service = tar.extractfile(members["etc/init.d/pon"]).read().decode()
    assert service.index('> "$device/bosa"') < service.index('> "$device/serdes"')
    assert '/usr/sbin/pon-oamd --interface' in service
    assert members["etc/rc.d/S25pon"].issym()
    acl = json.loads(tar.extractfile(members[files[4]]).read())
    assert "status" in acl["luci-app-tyfza1-pon"]["read"]["ubus"]["luci.tyfza1-pon"]
fit = json.loads((args.payload / "ram-pon/manifest.json").read_text())
assert hashlib.sha256((args.payload / "ram-pon/image.itb").read_bytes()).hexdigest() == fit["fit_sha256"]
kernel = next((args.payload / 'firmware').glob('*initramfs-Image')).read_bytes()
for marker in (b'silence_scope=shared-engine', b'refill_pending=%u', b'refill_retries=%u'):
    assert marker in kernel, marker
record = {"payload": str(args.payload.relative_to(root)) if args.payload.is_absolute() else str(args.payload),
          "build_unit": args.build_unit, "required_files": checks,
          "calibration_matches_original": True, "six_protocol_profiles": True,
          "private_identity_injected": True, "identity_file_mode": "0600", "WAN_protocol": "dhcp",
          "runtime_matches_current_preparation": True,
          "corrected_NACK_and_refill_in_kernel": True,
          "BOSA_precedes_SerDes": True, "S25pon_enabled": True,
          "hardware_boot_verified": False, "FIT": fit}
args.output.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({key: value for key, value in record.items() if key not in ("required_files", "FIT")}, indent=2))
