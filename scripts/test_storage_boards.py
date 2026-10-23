#!/usr/bin/env python3
"""Run storage preinit dispatch for both boards, failures and initramfs boots."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
files = root / 'port/packages/tyfza1-storage/files'
with tempfile.TemporaryDirectory(prefix='storage-boards-') as temporary:
    work = Path(temporary)
    board_file = work / 'board'
    migrate = work / 'migrate'
    events = work / 'events'
    migrate.write_text('#!/bin/sh\necho migrate >> "$EVENTS"\nexit "$MIGRATE_RC"\n')
    migrate.chmod(0o755)
    hook = (files / '79_tyfza1_storage').read_text()
    hook = hook.replace('/tmp/sysinfo/board_name', str(board_file))
    hook = hook.replace('/usr/libexec/tyfza1-migrate-storage', str(migrate))
    hook = hook.replace('/dev/console', str(work / 'console'))
    program = work / 'preinit.sh'
    program.write_text("""boot_hook_add() { registered=$2; }
run_failsafe_hook() {
    test "$FAILSAFE" = true && test "$pi_preinit_no_failsafe" = n || exit 99
    echo failsafe >> "$EVENTS"
}
""" + hook + '\n[ -z "$registered" ] || "$registered"\n')
    for board in ('tianyi,tyfza1', 'tianyi,tyfza1-2p5g', 'unrelated,device'):
        board_file.write_text(board + '\n')
        for initramfs, failure in ((0, 0), (0, 1), (1, 0)):
            events.write_text('')
            env = dict(os.environ, EVENTS=str(events), INITRAMFS=str(initramfs), MIGRATE_RC=str(failure))
            subprocess.run(['busybox', 'ash', str(program)], env=env, check=True)
            expected = [] if initramfs or board == 'unrelated,device' else ['migrate'] + (['failsafe'] if failure else [])
            assert events.read_text().splitlines() == expected
    # Exercise the actual executable's board gate before any volume access.
    source = (files / 'tyfza1-migrate-storage').read_text()
    gate = source[source.index('case "$(cat /tmp/sysinfo/board_name)"'):source.index('slot=$(sed')]
    gate = gate.replace('/tmp/sysinfo/board_name', str(board_file))
    for board in ('tianyi,tyfza1', 'tianyi,tyfza1-2p5g', 'unrelated,device'):
        board_file.write_text(board + '\n')
        result = subprocess.run(['busybox', 'ash', '-c', 'fail() { exit 1; };\n' + gate])
        assert (result.returncode == 0) == (board != 'unrelated,device')
print('Both storage board gates, preinit failure handling and initramfs exclusion passed')
