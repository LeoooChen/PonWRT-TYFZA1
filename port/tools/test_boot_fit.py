#!/usr/bin/env python3
"""Check boot ABI, rootfs integrity and signing template without target execution."""
import lzma
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from make_boot_fit import boot_script, compress, make_its, make_verity, verify_configuration_signature

VERITY = dict(data_blocks=2, hash_start_block=2, root_hash='a' * 64, salt='b' * 64)


class BootFit(unittest.TestCase):
    def test_ram_uses_observed_bootm_not_booti(self):
        script = boot_script('ram')
        self.assertEqual(script.splitlines()[-1], 'bootm ${unzip_src}')
        self.assertNotIn('booti', script)
        self.assertNotIn('ubi.mtd', script)
        self.assertNotIn(chr(92) + "n", script)

    def test_ubi_translates_both_stock_root_selectors(self):
        script = boot_script('ubi', VERITY)
        for value in ('31:9', '31:12', 'ubi_r0', 'ubi_r1',
                      '/dev/ubiblock0_4', '/dev/ubiblock0_7'):
            self.assertIn(value, script)
        self.assertIn('ubi.mtd=ubi_device', script)
        self.assertIn('exit 1', script)
        self.assertIn('root=/dev/dm-0', script)
        self.assertIn('dm-mod.waitfor=${owrt_root}', script)
        self.assertIn('dm-mod.create=' + chr(92) + '"', script)
        self.assertNotIn('root=${root_mtd}', script)
        self.assertNotIn(chr(92) + "n", script)

    def test_scripts_do_not_disable_verification_or_write_flash(self):
        for mode in ('ram', 'ubi'):
            script = boot_script(mode, VERITY)
            for forbidden in ('saveenv', 'setenv verify', 'ubi write', 'nand write',
                              'mtd write', 'ubiformat', 'flash_erase'):
                self.assertNotIn(forbidden, script)

    def test_missing_integrity_parameters_rejected(self):
        with self.assertRaises(ValueError):
            boot_script('ubi')

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            boot_script('stock')

    def test_bad_integrity_parameters_rejected(self):
        with self.assertRaises(ValueError):
            boot_script('ubi', {**VERITY, 'salt': 'untrusted";reset'})
        with self.assertRaises(ValueError):
            boot_script('ubi', {**VERITY, 'hash_start_block': 1})

    def test_vendor_lzma_properties_and_roundtrip(self):
        parameters = dict(lc=3, lp=0, pb=2, dictionary_bytes=8388608)
        original = b'Taurus native boot ABI\n' * 1000
        data = compress(original, parameters)
        self.assertEqual(data[0], 0x5d)
        self.assertEqual(struct.unpack_from('<I', data, 1)[0], 8388608)
        self.assertEqual(lzma.decompress(data, format=lzma.FORMAT_ALONE), original)

    def test_fit_retains_stock_node_names(self):
        its = make_its(0, 0, 0x1800000)
        for item in ('kernel {', 'fdt-dts {', 'script {', 'default = "conf-dts"',
                     'compression = "lzma"', 'algo = "sha256"'):
            self.assertIn(item, its)
        self.assertNotIn('signature {', its)
        self.assertIn('UNSIGNED', its)

    def test_signature_covers_script_and_boot_images(self):
        its = make_its(0, 0, 0x1800000, signed=True)
        self.assertEqual(its.count('signature {'), 4)
        self.assertIn('sign-images = "fdt", "kernel", "script"', its)
        self.assertIn('script = "script"', its)

    @unittest.skipUnless(shutil.which('veritysetup'), 'veritysetup not installed')
    def test_verity_tree_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            source = out / 'input.squashfs'
            source.write_bytes(b'hsqs' + b'\x5a' * 8190)
            info = make_verity(source, out)
            self.assertEqual(info['data_blocks'], 3)
            root = out / 'rootfs.squashfs'
            self.assertGreater(root.stat().st_size, 3 * 4096)
            with root.open('r+b') as f:
                f.write(b'FAIL')
            result = subprocess.run(['veritysetup', 'verify', '--no-superblock',
                '--format=1', '--hash=sha256', '--data-block-size=4096',
                '--hash-block-size=4096', '--data-blocks=3', '--hash-offset=12288',
                '--salt=' + info['salt'], str(root), str(root), info['root_hash']],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


    @unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtput') and shutil.which('true'), 'FDT tools not installed')
    def test_fail_open_configuration_verifier_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'image.itb'
            source = '/dts-v1/; / { signature { key-dev { algo = "sha256,rsa2048"; required = "conf"; }; }; configurations { conf-dts { signature { value = [01]; }; }; }; };'
            subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(image)],
                           input=source, text=True, check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'broken signature'):
                verify_configuration_signature(image, image, shutil.which('true'))


    @unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtput') and shutil.which('true'), 'FDT tools not installed')
    def test_unknown_legacy_key_label_is_not_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'key.dtb'
            source = '/dts-v1/; / { signature { key-dev { algo = "sha1,rsa2048"; required = "conf"; }; }; };'
            subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(image)],
                           input=source, text=True, check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'Unrecognized legacy key'):
                verify_configuration_signature(image, image, shutil.which('true'))


if __name__ == '__main__':
    unittest.main()
