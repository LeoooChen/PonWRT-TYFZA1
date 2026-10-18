#!/usr/bin/env python3
"""Exercise installation and upgrade against a mutable OEM UBI layout."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
UPGRADE = PROJECT / 'port/overlay/target/linux/rtl9617c/base-files/lib/upgrade'
LEB = 126976
OEM = [
    ('ubi_Config', 50), ('ubi_fpga', 34), ('ubi_DTB0', 3),
    ('ubi_k0', 50), ('ubi_r0', 265), ('ubi_DTB1', 3),
    ('ubi_k1', 50), ('ubi_r1', 265), ('ubi_framework1', 133),
    ('ubi_framework2', 133), ('ubi_apps', 793),
]

MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, struct, sys
root = pathlib.Path(os.environ['UBI_TEST_ROOT'])
statefile = root / 'state.json'
s = json.loads(statefile.read_text())
cmd = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
s['events'].append([cmd] + a)
statefile.write_text(json.dumps(s))
if cmd == os.environ.get('UBI_TEST_FAIL'):
    sys.exit(1)
if cmd in ('ubirsvol', 'ubirmvol', 'ubimkvol'):
    name = a[a.index('-N') + 1]
    matches = [k for k, v in s['vols'].items() if v['name'] == name]
    if cmd == 'ubimkvol':
        ident = a[a.index('-n') + 1]
        assert not matches and ident not in s['vols']
        ebs = s['free'] if '-m' in a else (int(a[a.index('-s')+1])+126975)//126976
        assert ebs <= s['free']
        s['vols'][ident] = dict(name=name, ebs=ebs, used=0)
        s['free'] -= ebs
    else:
        ident, = matches
        v = s['vols'][ident]
        if cmd == 'ubirmvol':
            s['free'] += v['ebs']
            del s['vols'][ident]
            shutil.rmtree(root / ('fs' + ident), ignore_errors=True)
        else:
            ebs = (int(a[a.index('-s')+1])+126975)//126976
            assert ebs - v['ebs'] <= s['free']
            # UBIFS must never shrink; a static root also cannot truncate data.
            if name == 'rootfs_data' or v.get('static'):
                assert ebs * 126976 >= v['used']
            s['free'] += v['ebs'] - ebs
            v['ebs'] = ebs
elif cmd == 'ubiupdatevol':
    ident = pathlib.Path(a[0]).name.split('_')[1]
    v = s['vols'][ident]
    size = 0 if a[1] == '-t' else pathlib.Path(a[1]).stat().st_size
    assert size <= v['ebs'] * 126976
    v['used'] = size
elif cmd == 'fw_printenv':
    print('0')
elif cmd == 'mount':
    ident = pathlib.Path(a[-2]).name.split('_')[1]
    target = pathlib.Path(a[-1])
    source = root / ('fs' + ident)
    source.mkdir(exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
    s['mounted'] = dict(ident=ident, target=str(target), readonly='ro' in a)
    if 'max_lebs' not in s['vols'][ident]:
        s['vols'][ident]['max_lebs'] = s['vols'][ident]['ebs']
elif cmd == 'umount':
    mounted = s.pop('mounted')
    target = pathlib.Path(mounted['target'])
    if not mounted['readonly']:
        source = root / ('fs' + mounted['ident'])
        shutil.rmtree(source)
        shutil.copytree(target, source, symlinks=True)
    for p in target.iterdir():
        if p.is_dir() and not p.is_symlink(): shutil.rmtree(p)
        else: p.unlink()
elif cmd not in ('fw_setenv', 'reboot', 'sync', 'remove_block'):
    raise AssertionError(cmd)
sysdir = root / 'sys'
for p in sysdir.glob('ubi0_*'):
    shutil.rmtree(p)
for ident, v in s['vols'].items():
    p = sysdir / ('ubi0_' + ident)
    p.mkdir()
    for f, value in [('name', v['name']), ('reserved_ebs', v['ebs']), ('usable_eb_size', 126976)]:
        (p / f).write_text(str(value))
    header = bytearray(64)
    if 'squashfs_size' in v:
        header[:4] = b'hsqs'
        struct.pack_into('<Q', header, 40, v['squashfs_size'])
    elif 'max_lebs' in v:
        struct.pack_into('<I', header, 0, 0x06101831)
        struct.pack_into('<I', header, 44, v['max_lebs'])
    (root / 'dev' / ('ubi0_' + ident)).write_bytes(header)
(sysdir / 'ubi0' / 'avail_eraseblocks').write_text(str(s['free']))
statefile.write_text(json.dumps(s))
'''


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'bin').mkdir()
        (self.root / 'dev').mkdir()
        (self.root / 'sys/ubi0').mkdir(parents=True)
        # OEM serial log: 1966 good PEBs, 40 bad-block reserves, 143 available.
        self.state = dict(free=143, events=[], vols={
            str(i): dict(name=name, ebs=ebs, used=ebs * LEB)
            for i, (name, ebs) in enumerate(OEM)
        })
        mock = self.root / 'bin/mock'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        for command in ['ubirsvol', 'ubirmvol', 'ubimkvol', 'ubiupdatevol',
                        'fw_printenv', 'fw_setenv', 'reboot', 'sync', 'remove_block', 'mount', 'umount']:
            (self.root / 'bin' / command).symlink_to(mock)
        self.env = dict(os.environ, UBI_TEST_ROOT=str(self.root),
                        PATH=str(self.root / 'bin') + ':' + os.environ['PATH'])
        busybox = shutil.which('busybox')
        self.shell = [busybox, 'ash'] if busybox else ['/bin/sh']
        if busybox:
            (self.root / 'bin/hexdump').symlink_to(busybox)
        self.helper = self.root / 'tyfza1-storage.sh'
        self.helper.write_text(self.rewrite((UPGRADE / 'tyfza1-storage.sh').read_text()))
        self.platform = self.root / 'platform.sh'
        self.platform.write_text(self.rewrite((UPGRADE / 'platform.sh').read_text()))

    def rewrite(self, text):
        text = re.sub(r'(?<![/\w.-])(ubirsvol|ubirmvol|ubimkvol|ubiupdatevol|reboot|sync|mount|umount)(?=\s|$)',
                      lambda m: str(self.root / 'bin' / m[0]), text)
        return (text.replace('/sys/class/ubi', str(self.root / 'sys'))
                .replace('/lib/upgrade/tyfza1-storage.sh', str(self.root / 'tyfza1-storage.sh'))
                .replace('/proc/cmdline', str(self.root / 'cmdline'))
                .replace('/dev/', str(self.root / 'dev') + '/')
                .replace('/tmp/tyfza1-overlay', str(self.root / 'overlay'))
                .replace('/tmp/tyfza1-boot.env', str(self.root / 'boot.env')))

    def save(self):
        (self.root / 'state.json').write_text(json.dumps(self.state))
        subprocess.run([str(self.root / 'bin/sync')], env=self.env, check=True)
        self.state['events'] = []
        (self.root / 'state.json').write_text(json.dumps(self.state))

    def run_shell(self, script, success=True):
        result = subprocess.run(self.shell + ['-c', script], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.state = json.loads((self.root / 'state.json').read_text())
        return result

    def check_oem(self, rootid):
        for i, (name, ebs) in enumerate(OEM):
            if i != rootid:
                volume = self.state['vols'][str(i)]
                self.assertEqual((volume['name'], volume['ebs']), (name, ebs))
                if i != rootid - 1:
                    self.assertEqual(volume['used'], ebs * LEB)
        for event in self.state['events']:
            if event[0] in ('ubirsvol', 'ubirmvol', 'ubimkvol'):
                self.assertIn(event[event.index('-N') + 1], [f'ubi_r{(rootid-4)//3}', 'rootfs_data'])

    def prepare_upgrade(self, size=8067048, slot=1, success=True):
        (self.root / 'cmdline').write_text(f'root=/dev/ubiblock0_{4+3*slot} tyfza1.slot={slot}')
        self.save()
        return self.run_shell(f'''
            . ./platform.sh
            nand_find_ubi() {{ echo ubi0; }}
            nand_find_volume() {{ tyfza1_find_volume "$@"; }}
            nand_remove_ubiblock() {{ remove_block "$@"; }}
            CI_ROOTPART=ubi_r{slot}
            nand_upgrade_prepare_ubi {size} squashfs 4000000
        ''', success)

    def test_first_install_reclaims_root_tail_preserving_oem(self):
        self.save()
        (self.root / 'rootfs.squashfs').write_bytes(b'R' * 8067048)
        (self.root / 'kernel.itb').write_bytes(b'K' * 4000000)
        for tool in ['fw_printenv', 'fw_setenv']:
            (self.root / tool).symlink_to(self.root / 'bin' / tool)
        (self.root / 'fw_env.config').touch()
        (self.root / 'install.sh').write_text(self.rewrite((PROJECT / 'scripts/install_ubi_telnet.sh').read_text()))
        self.run_shell('sh ./install.sh')
        self.assertEqual(self.state['vols']['7']['ebs'], 64)
        self.assertEqual(self.state['vols']['11']['ebs'], 344)
        self.assertEqual(self.state['free'], 0)
        self.check_oem(7)
        self.state['vols']['7']['squashfs_size'] = 8066952
        (self.root / 'cmdline').write_text('tyfza1.slot=1')
        self.save()
        self.run_shell('. ./tyfza1-storage.sh; ! tyfza1_boot_storage_plan')
        self.assertEqual(self.state['events'], [])

    def test_beta_16_mib_upgrade_expands_data(self):
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=133, used=1000000)
        self.state['free'] -= 133
        self.prepare_upgrade()
        self.assertEqual(self.state['vols']['7']['ebs'], 64)
        self.assertEqual(self.state['vols']['11']['ebs'], 344)
        self.check_oem(7)

    def test_larger_future_image_grows_root_before_writing(self):
        self.state['vols']['4'].update(ebs=64, used=8067048)
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=344, used=20000000)
        self.state['free'] = 0
        self.prepare_upgrade(size=24000000, slot=0)
        self.assertEqual(self.state['vols']['4']['ebs'], 190)
        self.assertEqual(self.state['vols']['11']['ebs'], 218)
        self.check_oem(4)

    def test_repeated_upgrade_keeps_all_available_space(self):
        self.prepare_upgrade()
        first = self.state['vols'].copy()
        self.prepare_upgrade()
        self.assertEqual(self.state['vols'], first)
        self.check_oem(7)

    def test_static_root_can_shrink_after_old_image_is_cleared(self):
        self.state['vols']['7']['static'] = True
        self.prepare_upgrade()
        self.assertEqual(self.state['vols']['7']['ebs'], 64)

    def test_resize_failure_stops_before_creating_data(self):
        self.env['UBI_TEST_FAIL'] = 'ubirsvol'
        self.prepare_upgrade(success=False)
        self.assertNotIn('11', self.state['vols'])
        self.assertEqual(self.state['vols']['7']['ebs'], 265)

    def test_oversize_and_no_space_fail_before_mutation(self):
        self.prepare_upgrade(size=33648641, success=False)
        self.assertEqual(self.state['events'], [])
        self.state['free'] = 0
        self.prepare_upgrade(size=24000000, success=False)
        self.assertEqual(self.state['events'], [])

    def test_reserved_id_collision_fails_before_mutation(self):
        self.state['vols']['11'] = dict(name='factory_extra', ebs=1, used=0)
        self.prepare_upgrade(success=False)
        self.assertEqual(self.state['events'], [])

    def test_block_or_data_removal_failure_stops_resize(self):
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=133, used=0)
        self.state['free'] -= 133
        self.env['UBI_TEST_FAIL'] = 'remove_block'
        self.prepare_upgrade(success=False)
        self.assertEqual([e[0] for e in self.state['events']], ['remove_block'])
        self.env['UBI_TEST_FAIL'] = 'ubirmvol'
        self.prepare_upgrade(success=False)
        self.assertEqual([e[0] for e in self.state['events']], ['remove_block', 'ubirmvol'])
        self.assertEqual(self.state['vols']['7']['ebs'], 265)

    def test_installer_data_growth_preserves_existing_ubifs(self):
        self.state['vols']['7'].update(ebs=64, used=8067048)
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=133, used=10000000)
        self.state['free'] = 211
        self.save()
        self.run_shell('. ./tyfza1-storage.sh; tyfza1_plan_storage ubi0 ubi0_7 8067048; tyfza1_grow_data ubi0')
        self.assertEqual(self.state['vols']['11'], dict(name='rootfs_data', ebs=344, used=10000000))

    def migrate_boot(self, success=True):
        return self.run_shell('''
            . ./tyfza1-storage.sh
            nand_remove_ubiblock() { remove_block "$@"; }
            tyfza1_migrate_storage
        ''', success)

    def test_direct_upgrade_from_published_versions_preserves_config(self):
        for tag, slot in [('v0.6.0-alpha', 0), ('v0.7.0-alpha', 1)]:
            with self.subTest(tag=tag):
                rootid = str(4 + slot * 3)
                self.state['free'] = 143
                self.state['vols'].pop('11', None)
                for i in [4, 7]:
                    self.state['vols'][str(i)] = dict(name=OEM[i][0], ebs=265, used=265 * LEB)
                self.state['vols'][rootid].update(used=8067072, squashfs_size=8066952, static=True)
                (self.root / 'cmdline').write_text(f'tyfza1.slot={slot}')
                old = subprocess.check_output(['git', 'show', tag + ':' + str(UPGRADE.relative_to(PROJECT) / 'platform.sh')],
                                              cwd=PROJECT, text=True)
                (self.root / 'old-platform.sh').write_text(self.rewrite(old))
                self.save()
                self.run_shell('''
                    . ./tyfza1-storage.sh
                    . ./old-platform.sh
                    nand_find_ubi() { echo ubi0; }
                    nand_find_volume() { tyfza1_find_volume "$@"; }
                    nand_remove_ubiblock() { remove_block "$@"; }
                    CI_ROOTPART=ubi_rSLOT
                    nand_upgrade_prepare_ubi 8067072 squashfs 4000000
                '''.replace('SLOT', str(slot)))
                self.assertEqual(self.state['vols']['11']['ebs'], 133)
                # nand_restore_config mounts the newly created 16 MiB UBIFS.
                self.state['vols']['11']['max_lebs'] = 133
                files = self.root / 'fs11'
                files.mkdir(exist_ok=True)
                (files / 'sysupgrade.tgz').write_bytes(b'saved old firmware configuration')
                (files / 'upper').mkdir(exist_ok=True)
                (files / 'upper/network').write_text('preserved network settings')
                self.save()
                self.migrate_boot()
                self.assertEqual(self.state['vols'][rootid]['ebs'], 64)
                self.assertEqual(self.state['vols'][rootid]['used'], 8067072)
                self.assertEqual(self.state['vols']['11']['ebs'], 344)
                self.assertEqual(self.state['vols']['11']['max_lebs'], 344)
                self.assertEqual((files / 'sysupgrade.tgz').read_bytes(), b'saved old firmware configuration')
                self.assertEqual((files / 'upper/network').read_text(), 'preserved network settings')
                self.check_oem(int(rootid))
                self.run_shell('. ./tyfza1-storage.sh; ! tyfza1_boot_storage_plan')

    def test_already_resized_ubi_with_small_ubifs_is_migrated(self):
        self.state['vols']['7'].update(ebs=64, used=8067072, squashfs_size=8066952)
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=344, used=0, max_lebs=133)
        self.state['free'] = 0
        (self.root / 'cmdline').write_text('tyfza1.slot=1')
        self.save()
        self.migrate_boot()
        self.assertEqual(self.state['vols']['11']['max_lebs'], 344)

    def test_config_backup_failure_keeps_volumes_untouched(self):
        self.state['vols']['7'].update(used=8067072, squashfs_size=8066952)
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=133, used=0, max_lebs=133)
        self.state['free'] -= 133
        (self.root / 'cmdline').write_text('tyfza1.slot=1')
        self.env['UBI_TEST_FAIL'] = 'mount'
        self.save()
        self.migrate_boot(success=False)
        self.assertEqual(self.state['vols']['7']['ebs'], 265)
        self.assertEqual(self.state['vols']['11']['ebs'], 133)
        self.assertEqual([e[0] for e in self.state['events']], ['mount'])

    def test_preinit_handoff_uses_upgrade_ramfs_before_mount_root(self):
        self.state['vols']['7'].update(used=8067072, squashfs_size=8066952)
        self.state['vols']['11'] = dict(name='rootfs_data', ebs=133, used=0, max_lebs=133)
        self.state['free'] -= 133
        (self.root / 'cmdline').write_text('tyfza1.slot=1')
        common = self.root / 'common.sh'
        ramroot = self.root / 'ramroot'
        common.write_text(f'''RAM_ROOT={ramroot}
install_bin() {{ mkdir -p "$RAM_ROOT/sbin"; touch "$RAM_ROOT/sbin/upgraded"; chmod +x "$RAM_ROOT/sbin/upgraded"; }}
''')
        hook = self.rewrite((UPGRADE.parent / 'preinit/79_tyfza1_storage').read_text())
        hook = hook.replace('/lib/upgrade/common.sh', str(common)).replace('/tmp/sysupgrade', str(self.root / 'handoff'))
        (self.root / 'preinit.sh').write_text(hook)
        self.save()
        self.run_shell('boot_hook_add() { :; }; . ./preinit.sh; tyfza1_expand_storage; exit 99')
        prefix, image, command = (self.root / 'handoff').read_bytes().split(b'\0')
        self.assertEqual(prefix.decode(), str(ramroot))
        self.assertEqual(image, b'tyfza1-storage')
        self.assertIn(b'include /lib/upgrade; tyfza1_migrate_storage', command)
        self.assertEqual(self.state['events'], [])


if __name__ == '__main__':
    unittest.main()
