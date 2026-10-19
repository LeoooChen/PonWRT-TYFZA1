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

with tempfile.TemporaryDirectory(prefix='tyfza1-sysupgrade-board-') as directory:
    work = Path(directory)
    nand = work / 'nand.sh'
    nand.write_text((upstream / 'nand.sh').read_text().replace('. /lib/functions.sh', ''))
    program = work / 'check.sh'
    program.write_text('''. "$1/common.sh"
. "$2"
board_name() { printf '%s\\n' "$TEST_BOARD"; }
tyfza1_check_sizes() { [ "$TEST_SIZE_FAIL" != 1 ] && [ "$1" -gt 0 ] && [ "$2" -gt 0 ]; }
''' + checker + '\nplatform_check_image "$3"\n')
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

    def run(board, path, expected, size_fail=False):
        env = dict(os.environ, TEST_BOARD=board, TEST_SIZE_FAIL=str(int(size_fail)))
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
