#!/usr/bin/env python3
"""Run the actual OpenWrt NAND checker for both TYFZA1 board identities."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image', type=Path, help='Also validate an existing sysupgrade archive')
args = parser.parse_args()
upstream = root / 'sources/openwrt/package/base-files/files/lib/upgrade'
platform = (root / 'port/overlay/target/linux/rtl9617c/base-files/lib/upgrade/platform.sh').read_text()
start = platform.index('platform_check_image() {')
end = platform.index('\n}', start) + 2
checker = platform[start:end]
start = platform.index('tyfza1_check_storage_format() {')
end = platform.index('\n}', start) + 2
storage_checker = platform[start:end]

with tempfile.TemporaryDirectory(prefix='tyfza1-sysupgrade-board-') as directory:
    work = Path(directory)
    nand = work / 'nand.sh'
    nand.write_text((upstream / 'nand.sh').read_text().replace('. /lib/functions.sh', ''))
    storage_sys = work / 'ubi'
    storage_sys.mkdir()
    storage_checker = storage_checker.replace('/sys/class/ubi', str(storage_sys))
    storage_checker = storage_checker.replace('/tmp/tyfza1-storage-image.json', str(work / 'metadata.json'))
    program = work / 'check.sh'
    program.write_text('''. "$1/common.sh"
. "$2"
board_name() { printf '%s\\n' "$TEST_BOARD"; }
tyfza1_check_sizes() { [ "$TEST_SIZE_FAIL" != 1 ] && [ "$1" -gt 0 ] && [ "$2" -gt 0 ]; }
fwtool() { [ "$TEST_METADATA" != broken ]; }
jsonfilter() { printf '%s\n' "$TEST_METADATA"; }
''' + storage_checker + '\n' + checker + '\nplatform_check_image "$3"\n')
    image = work / 'sysupgrade.bin'
    with tarfile.open(image, 'w', format=tarfile.USTAR_FORMAT) as archive:
        parent = tarfile.TarInfo('sysupgrade-tianyi_tyfza1/')
        parent.type = tarfile.DIRTYPE
        archive.addfile(parent)
        for name, data in [('CONTROL', b'BOARD=tianyi_tyfza1\n'), ('kernel', b'kernel'), ('root', b'hsqsroot')]:
            member = tarfile.TarInfo('sysupgrade-tianyi_tyfza1/' + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    bad = work / 'corrupt.bin'
    data = bytearray(image.read_bytes()); data[0] ^= 1; bad.write_bytes(data)

    def run(board, path, expected, size_fail=False, metadata=''):
        env = dict(os.environ, TEST_BOARD=board, TEST_SIZE_FAIL=str(int(size_fail)), TEST_METADATA=metadata)
        result = subprocess.run(['busybox', 'ash', str(program), str(upstream), str(nand), str(path)],
                                env=env, capture_output=True, text=True)
        assert (result.returncode == 0) == expected, (board, str(path), result.stdout, result.stderr)

    for board in ('tianyi,tyfza1', 'tianyi,tyfza1-2p5g'):
        run(board, image, True)
        run(board, bad, False)
        run(board, image, False, size_fail=True)
        if args.image:
            run(board, args.image.resolve(), True)
    run('unrelated,device', image, False)
    # Either published copy requires factory-v1, even with forced upgrade.
    start = platform.index('platform_do_upgrade() {')
    end = platform.index('\n}', start) + 2
    upgrade_body = platform[start:end].replace('/proc/cmdline', str(work / 'cmdline'))
    (work / 'cmdline').write_text('tyfza1.slot=1\n')
    forced = work / 'forced.sh'
    forced.write_text('''fwtool() { [ "$TEST_METADATA" != broken ]; }
jsonfilter() { printf '%s\n' "$TEST_METADATA"; }
nand_do_upgrade() { touch "$UPGRADED"; }
''' + storage_checker + '\n' + upgrade_body + '\nFORCE=1 platform_do_upgrade "$1"\n')
    upgraded = work / 'upgraded'
    for ident, name in ((12, 'tyfza1_factory'), (13, 'tyfza1_factory_backup')):
        volume = storage_sys / f'ubi0_{ident}'
        volume.mkdir()
        (volume / 'name').write_text(name + '\n')
        for metadata in ('', 'broken', 'factory-v1'):
            expected = metadata == 'factory-v1'
            for board in ('tianyi,tyfza1', 'tianyi,tyfza1-2p5g'):
                run(board, image, expected, metadata=metadata)
            upgraded.unlink(missing_ok=True)
            result = subprocess.run(['busybox', 'ash', str(forced), str(image)],
                env=dict(os.environ, TEST_METADATA=metadata, UPGRADED=str(upgraded)), capture_output=True)
            assert (result.returncode == 0) == expected
            assert upgraded.exists() == expected
        (volume / 'name').unlink()
        volume.rmdir()
    hotfix = (root / 'scripts/fix_legacy_2p5g_upgrade.sh').read_text()
    script = work / 'hotfix.sh'
    board_file = work / 'board_name'
    live_platform = work / 'platform.sh'
    calls = work / 'calls'
    tools = work / 'bin'
    tools.mkdir()
    upgrade = tools / 'sysupgrade'
    upgrade.write_text('#!/bin/sh\n[ "$1" = -T ] || exit 99\nprintf "checked\\n" >> "$CALLS"\nexit "$CHECK_RC"\n')
    upgrade.chmod(0o755)
    script.write_text(hotfix.replace('/tmp/sysinfo/board_name', str(board_file))
                      .replace('platform=/lib/upgrade/platform.sh', 'platform=' + str(live_platform))
                      .replace('/tmp/tyfza1-platform-before-fix.', str(work / 'backup.')))
    old_check = 'nand_do_platform_check "$(board_name)" "$1" || return 1\n'
    env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], CALLS=str(calls), CHECK_RC='0')
    board_file.write_text('tianyi,tyfza1-2p5g\n')
    live_platform.write_text(old_check)
    subprocess.run(['busybox', 'ash', str(script), str(image)], env=env, check=True, capture_output=True)
    patched = live_platform.read_text()
    assert 'nand_do_platform_check tianyi,tyfza1' in patched
    subprocess.run(['busybox', 'ash', str(script), str(image)], env=env, check=True, capture_output=True)
    assert live_platform.read_text() == patched and len(calls.read_text().splitlines()) == 2
    live_platform.write_text(old_check)
    assert subprocess.run(['busybox', 'ash', str(script), str(image)], env=dict(env, CHECK_RC='1'), capture_output=True).returncode == 1
    assert live_platform.read_text() == old_check
    board_file.write_text('tianyi,tyfza1\n')
    before_calls = calls.read_bytes()
    assert subprocess.run(['busybox', 'ash', str(script), str(image)], env=env, capture_output=True).returncode == 1
    assert live_platform.read_text() == old_check and calls.read_bytes() == before_calls
    board_file.write_text('tianyi,tyfza1-2p5g\n')
    live_platform.write_text('unrecognized custom script\n')
    assert subprocess.run(['busybox', 'ash', str(script), str(image)], env=env, capture_output=True).returncode == 1
    assert live_platform.read_text() == 'unrecognized custom script\n'
print('Both boards: shared tar directory accepted; corruption, unknown board and failed size checks still rejected')
print('Legacy hotfix: board guard, idempotence, test-only sysupgrade and rollback on check failure passed')
print('Factory-v1: primary/backup format guards and forced-upgrade rejection passed')
