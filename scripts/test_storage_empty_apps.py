#!/usr/bin/env python3
"""Run the migration's apps inventory against erased, populated and bad volumes."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = (PROJECT / 'port/packages/tyfza1-storage/files/tyfza1-migrate-storage').read_text()
START = SOURCE.index('\n', SOURCE.index('\t/usr/share/tyfza1/factory.uc "$work/mount" "$work/import-original"'))
INVENTORY = SOURCE[START:SOURCE.index('\n\tfor entry in 0:config 10:apps; do', START)]


class EmptyAppsTests(unittest.TestCase):
    def inventory(self, data, filesystem=False, read_error=False):
        with tempfile.TemporaryDirectory(prefix='tyfza1-empty-apps-') as temporary:
            root = Path(temporary)
            (root / 'capsule').mkdir()
            (root / 'capsule/files.txt').touch()
            (root / 'mount').mkdir()
            (root / 'mount/oem-file').write_text('retain original apps metadata\n')
            volume = root / 'ubi0_10'
            volume.write_bytes(data)
            before = hashlib.sha256(data).hexdigest()
            program = r'''
set -eu
set -o pipefail
work=$APPS_TEST_ROOT
dev=$work/ubi0
mounted=1
umount() { :; }
mount() {
    printf '%s\n' "$*" >> "$work/mount-calls"
    test "$1 $2 $3 $4" = '-t ubifs -o ro,noatime'
    test "$APPS_TEST_FILESYSTEM" = 1
}
import_hashes() (cd "$1" && find . -type f -exec sha256sum {} \; | sort)
'''
            if read_error:
                program += 'tr() { return 1; }\n'
            result = subprocess.run(['busybox', 'ash', '-c', program + INVENTORY],
                                    env=dict(os.environ, APPS_TEST_ROOT=str(root),
                                             APPS_TEST_FILESYSTEM=str(int(filesystem))),
                                    text=True, capture_output=True)
            self.assertEqual(hashlib.sha256(volume.read_bytes()).hexdigest(), before)
            calls = (root / 'mount-calls').read_text() if (root / 'mount-calls').exists() else ''
            return result, calls, (root / 'capsule/files.txt').read_text()

    def test_erased_volume_is_archived_without_mounting(self):
        result, calls, metadata = self.inventory(b'\xff' * (2 * 126976))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, '')
        self.assertIn('empty', metadata)

    def test_populated_volume_keeps_readonly_file_inventory(self):
        result, calls, metadata = self.inventory(b'\x31\x18\x10\x06' + b'\xff' * 4092, True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ro,noatime', calls)
        self.assertIn('oem-file', metadata)

    def test_nonempty_tail_is_not_mistaken_for_an_erased_volume(self):
        result, calls, _ = self.inventory(b'\xff' * (2 * 126976 - 1) + b'\x00')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ro,noatime', calls)

    def test_read_error_does_not_accept_an_empty_pipeline(self):
        result, calls, _ = self.inventory(b'\xff' * 4096, read_error=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
