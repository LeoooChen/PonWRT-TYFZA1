#!/usr/bin/env python3
"""Exercise repository selection through the real overlay synchronization script."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
script = (root / 'port/scripts/sync-overlay.sh').read_text()
with tempfile.TemporaryDirectory(prefix='tyfza1-apk-config-') as directory:
    work = Path(directory)
    port, tree = work / 'port', work / 'openwrt'
    for path in ('scripts', 'configs', 'host-patches', 'overlay/target/linux/rtl9617c',
                 'packages/luci-app-tyfza1-pon', 'packages/tyfza1-pon-control'):
        (port / path).mkdir(parents=True, exist_ok=True)
    (tree / 'package/network/services/hostapd/patches').mkdir(parents=True)
    (work / 'VERSION').write_text('0.7.0-alpha\n')
    (port / 'configs/tyfza1-boot.config').write_text('CONFIG_PACKAGE_keep=y\n')
    shutil.copyfile(root / 'port/configs/tyfza1-compat.config', port / 'configs/tyfza1-compat.config')
    required = [line for line in (port / 'configs/tyfza1-compat.config').read_text().splitlines()
                if line.startswith('CONFIG_PACKAGE_')]
    for patch in (root / 'port/host-patches').glob('*.patch'):
        shutil.copyfile(patch, port / 'host-patches' / patch.name)
    subprocess.run(['git', 'init', '-q', str(tree)], check=True)
    subprocess.run(['git', '-C', str(tree), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit',
                    '--allow-empty', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip()
    sync = port / 'scripts/sync-overlay.sh'
    sync.write_text(script.replace('PIN=79bc56cb7483bc716da07c369530b792631904b4', 'PIN=' + revision))
    env = {k: v for k, v in os.environ.items() if k not in
           ('TYFZA1_APK_REPOSITORY', 'GITHUB_REPOSITORY', 'RELEASE_TAG', 'TYFZA1_CONFIG')}
    config = tree / '.config'
    original = 'CONFIG_PACKAGE_keep=y\nCONFIG_VERSION_REPO="https://old.example/build"\n'
    config.write_text(original)

    # Local builds discard stale release URLs and use official feeds alone.
    cases = [{}, {'GITHUB_REPOSITORY': 'owner/project', 'RELEASE_TAG': 'v1-local'},
             {'TYFZA1_APK_REPOSITORY': 'https://packages.example/build-002'}]
    for settings in cases:
        for _ in range(2):
            subprocess.run(['sh', str(sync), str(tree)], env={**env, **settings},
                           check=True, capture_output=True)
            lines = config.read_text().splitlines()
            assert [line for line in lines if line.startswith('CONFIG_VERSION_REPO=')] == [
                'CONFIG_VERSION_REPO="https://downloads.openwrt.org/snapshots"']
            assert lines.count('CONFIG_SIGNED_PACKAGES=y') == 1
            assert lines.count('CONFIG_SIGN_EACH_PACKAGE=y') == 1
            assert all(lines.count(setting) == 1 for setting in required)
            assert 'CONFIG_PACKAGE_keep=y' in lines

    custom = work / 'custom.config'
    custom.write_text('CONFIG_PACKAGE_custom=y\nCONFIG_VERSION_REPO="https://stale.example"\n')
    subprocess.run(['sh', str(sync), str(tree)], env={**env, 'TYFZA1_CONFIG': str(custom),
                    'TYFZA1_APK_REPOSITORY': 'https://packages.example/build-003'},
                   check=True, capture_output=True)
    assert 'CONFIG_PACKAGE_custom=y' in config.read_text()
    assert 'CONFIG_VERSION_REPO="https://downloads.openwrt.org/snapshots"' in config.read_text()
    assert 'https://stale.example' in custom.read_text()

    # Execute the production make macro; release/custom URLs cannot add a feed.
    feeds = work / 'distfeeds.list'
    makefile = work / 'Makefile'
    makefile.write_text('qstrip = $(strip $(subst ",,$(1)))\n' +
        'include ' + str(root / 'port/overlay/target/linux/rtl9617c/base-files.mk') +
        '\nall:\n\t$(call FeedSourcesAppendAPK,' + str(feeds) + ')\n')
    official = [f'https://downloads.openwrt.org/snapshots/packages/aarch64_cortex-a53/{feed}/packages.adb'
                for feed in ('base', 'luci', 'packages', 'routing', 'telephony', 'video')]
    for repository in ('', 'https://packages.example/build-004'):
        subprocess.run(['make', '-s', '-f', str(makefile), 'CONFIG_VERSION_REPO="' + repository + '"'],
                       check=True, capture_output=True)
        actual = [line for line in feeds.read_text().splitlines() if not line.startswith('#')]
        assert actual == official

    # Rootfs assembly writes one architecture; first boot must restore both.
    apk = work / 'etc/apk'
    (apk / 'keys').mkdir(parents=True)
    (apk / 'arch').write_text('aarch64_cortex-a55\n')
    defaults = root / 'port/overlay/target/linux/rtl9617c/base-files/etc/uci-defaults/98-tyfza1-apk-compat'
    migration = work / 'migrate.sh'
    migration.write_text(defaults.read_text().replace('/etc/apk/', str(apk) + '/'))
    assert subprocess.run(['sh', str(migration)]).returncode != 0
    assert (apk / 'arch').read_text() == 'aarch64_cortex-a55\n'
    (apk / 'keys/openwrt-snapshots.pem').write_text('fixture public key\n')
    for _ in range(2):
        subprocess.run(['sh', str(migration)], check=True)
        assert (apk / 'arch').read_text().splitlines() == ['aarch64_cortex-a55', 'aarch64_cortex-a53']

print('APK: official-only feeds, common plugin dependencies, repeat sync and first-boot A53 compatibility passed')
