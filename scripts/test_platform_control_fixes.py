#!/usr/bin/env python3
"""Focused interpreter/solver checks. Does not build code or access the ONU."""
from pathlib import Path
import base64
import hashlib
import io
import json
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

root = Path(__file__).resolve().parents[1]
tree = root / 'sources/openwrt'
ucode = tree / 'staging_dir/hostpkg/bin/ucode'
apk = tree / 'staging_dir/host/bin/apk'
kernel = tree / 'build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.55'


def patch_copy(source, patch, destination):
    for name in re.findall(r'^--- a/(.+)$', patch.read_text(), re.M):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((source / name).read_bytes())
    subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + str(destination),
                    str(patch)], check=True)


with tempfile.TemporaryDirectory(prefix='tyfza1-controls-') as directory:
    work = Path(directory)
    patched = work / 'openwrt'
    patch_copy(tree, root / 'port/openwrt-patches/100-wifi-ucode-txpower.patch', patched)
    before = {p: p.read_bytes() for p in patched.rglob('*') if p.is_file()}
    for _ in range(2):
        subprocess.run(['sh', str(root / 'port/scripts/apply-openwrt-patches.sh'),
                        str(patched)], check=True)
    assert all(p.read_bytes() == data for p, data in before.items())
    wifi = patched / 'package/network/config/wifi-scripts/files-ucode'
    source = (wifi / 'lib/netifd/wireless/mac80211.sh').read_text()
    functions = source[source.index('function phy_suffix('):source.index('function teardown(')]
    program = r'''
let calls = [], failed = [], up = 0, fail_power = false;
function system(command) { push(calls, command); return fail_power && index(command, 'set txpower') >= 0 ? 1 : 0; }
function log(message) {}
function set_default(obj, key, value) { obj[key] ??= value; }
function find_phy(config, rename) { return 'phy0'; }
function validate(kind, config) {}
const fs = { access: function() { return false; } };
const netifd = {
    set_data: function(data) {}, set_up: function() { up++; },
    setup_failed: function(reason) { push(failed, reason); }
};
''' + functions + r'''
for (let test in [
    { value: 0, expected: 'fixed 000' },
    { value: 10, expected: 'fixed 1000' },
    { value: null, expected: 'auto' }
]) {
    for (let fail in [false, true]) {
        calls = []; failed = []; up = 0; fail_power = fail;
        ARGV[3] = sprintf('%J', {
            config: { channel: 1, band: '2g', txpower: test.value, rxantenna: 3, txantenna: 3, distance: 0 },
            data: { rxantenna: 3, txantenna: 3 }, interfaces: {}
        });
        let result = setup();
        assert(index(calls, 'iw phy phy0 set txpower ' + test.expected) >= 0);
        assert(result == (fail ? 1 : 0));
        assert(up == (fail ? 0 : 1));
        assert(sprintf('%J', failed) == sprintf('%J', fail ? ['TXPOWER_FAILED'] : []));
    }
}
'''
    subprocess.run([str(ucode), '-e', program], check=True)

    source = (wifi / 'usr/share/ucode/iwinfo.uc').read_text()
    info = source[source.index('export function info('):source.index('export function htmodelist(')]
    info = info.replace('export function', 'function')
    program = r'''
let ifaces = {};
function format_channel(value) { return 1; }
function format_frequency(value) { return 2.4; }
function hwmodelist(iface) { return []; }
function find_phy(id) { return {}; }
function assoclist(iface) { return {}; }
function format_rate(value) { return 'unknown'; }
function dbm2quality(value) { return 0; }
''' + info + r'''
for (let raw in [null, 0, 1000]) {
    ifaces = { wlan0: { hardware: {}, assoclist: [] } };
    if (raw != null) ifaces.wlan0.wiphy_tx_power_level = raw;
    let result = info('wlan0');
    assert(result[0].txpower === (raw == null ? null : raw / 100));
}
'''
    subprocess.run([str(ucode), '-e', program], check=True)
    print('Wireless: 0/auto/10 dBm, command failure propagation and unknown/zero readings passed.')

    patch_copy(kernel, root / 'port/overlay/target/linux/rtl9617c/patches-6.18/215-cpufreq-retry-same-target.patch', work / 'kernel')
    cpufreq = (work / 'kernel/drivers/cpufreq/cpufreq.c').read_text()
    for name, end in [('__target_index', 'int __cpufreq_driver_target'),
                      ('__cpufreq_driver_target', 'EXPORT_SYMBOL_GPL(__cpufreq_driver_target)')]:
        body = cpufreq[cpufreq.index('int ' + name + '('):cpufreq.index(end, cpufreq.index('int ' + name + '(') + 4)]
        assert '!(cpufreq_driver->flags & CPUFREQ_NEED_UPDATE_LIMITS)' in body
    dt = (work / 'kernel/drivers/cpufreq/cpufreq-dt.c').read_text()
    assert 'if (of_machine_is_compatible("realtek,rtl9617c"))\n\t\tdt_cpufreq_driver.flags |= CPUFREQ_NEED_UPDATE_LIMITS;' in dt
    clock = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/clk/clk-rtl9617c-cpu.c').read_text()
    opp = clock[clock.index('static int rtl_cpu_opp_set_rate('):clock.index('static ssize_t transition_error_show')]
    assert 'ret = rtl_cpu_set_rate(' in opp and 'ret = clk_set_rate(' not in opp
    program = clock[clock.index('static int rtl_cpu_program_rate('):clock.index('static int rtl_cpu_set_rate(')]
    assert 'if (!(readl(cpu->base + CPLLDIV) & CPU_FROM_FPLL) &&' in program
    assert 'rtl_cpu_read_rate(cpu, parent_rate) == rate)' in program
    assert set(re.findall(r'writel\([^;]*?cpu->base \+ (\w+)\)', clock)) == {'CPLLDIV', 'CPLL0', 'CPLLMUX'}
    print('CPU source checks: both same-target shortcuts disabled for this board, OPP calls serialized hardware transition, FPLL cannot be mistaken for completion. No hardware execution.')

    # Exercise real APK against an isolated installed database and local index.
    installed = tree / 'build_dir/target-aarch64_cortex-a55_musl/root-rtl9617c'
    sandbox = work / 'root'
    for name in ('lib/apk/db/installed', 'etc/apk/world', 'etc/apk/arch'):
        path = sandbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(installed / name, path)
    protected = ['luci-mod-status', 'luci-mod-network', 'libiwinfo20230701', 'wifi-scripts']
    database = (sandbox / 'lib/apk/db/installed').read_text()
    rom = work / 'rom-installed'
    rom.write_text(database)
    script = (root / 'port/overlay/target/linux/rtl9617c/base-files/etc/uci-defaults/99-tyfza1-platform-packages').read_text()
    script = script.replace('/rom/lib/apk/db/installed', str(rom))
    script = script.replace('apk add', shlex.quote(str(apk)) + ' --root ' + shlex.quote(str(sandbox)) + ' --no-scripts add')
    for _ in range(2):
        subprocess.run(['sh', '-s'], input=script, text=True, check=True, capture_output=True)
    constraints = (sandbox / 'etc/apk/world').read_text().splitlines()
    pins = [line for line in constraints if line.split('><')[0] in protected]
    assert len(pins) == 4 and all('><Q1' in pin for pin in pins)

    def identity(label):
        return 'Q1' + base64.b64encode(hashlib.sha1(label.encode()).digest()).decode()

    updates = []
    for record in database.strip().split('\n\n'):
        fields = dict(line.split(':', 1) for line in record.splitlines() if ':' in line)
        if fields.get('P') not in protected:
            continue
        for version in (fields['V'], '9999.0-r0'):
            updates.append('\n'.join([
                'C:' + identity(fields['P'] + version), 'P:' + fields['P'],
                'V:' + version, 'A:' + fields['A'], 'S:0', 'I:0', 'T:replacement', 'L:GPL-2.0'
            ]) + '\n\n')
    # An unrelated package must remain free to upgrade alongside the pins.
    def plugin_record(version):
        return '\n'.join(['C:' + identity('user-plugin-' + version), 'P:user-plugin',
                          'V:' + version, 'A:aarch64_cortex-a55', 'S:0', 'I:0',
                          'T:unrelated plugin', 'L:GPL-2.0']) + '\n\n'

    with (sandbox / 'lib/apk/db/installed').open('a') as out:
        out.write('\n' + plugin_record('1.0'))
    constraints.append('user-plugin')
    (sandbox / 'etc/apk/world').write_text('\n'.join(sorted(constraints)) + '\n')
    constraints.sort()
    updates.append(plugin_record('2.0'))
    index = work / 'APKINDEX.tar.gz'
    with tarfile.open(index, 'w:gz') as archive:
        data = ''.join(updates).encode()
        entry = tarfile.TarInfo('APKINDEX'); entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    cli = [str(apk), '--root', str(sandbox), '--no-network', '--allow-untrusted',
           '--repository', str(index)]
    result = subprocess.run(cli + ['upgrade', '--simulate'], text=True, capture_output=True, check=True)
    assert 'Upgrading user-plugin' in result.stdout, result.stdout
    assert not any('Upgrading ' + pkg in result.stdout for pkg in protected), result.stdout
    # Re-running after sysupgrade must replace old-image identity pins.
    world = sandbox / 'etc/apk/world'
    stale = [line.split('><')[0] + '><' + identity('old-' + line) if line in pins else line
             for line in constraints]
    world.write_text('\n'.join(stale) + '\n')
    subprocess.run(['sh', '-s'], input=script, text=True, check=True, capture_output=True)
    assert world.read_text().splitlines() == constraints
    # Control: the same index does offer replacement packages without the pins.
    world.write_text('\n'.join(line.split('><')[0] if line in pins else line for line in constraints) + '\n')
    result = subprocess.run(cli + ['upgrade', '--simulate'], text=True, capture_output=True, check=True)
    assert 'Upgrading' in result.stdout and '9999.0-r0' in result.stdout, (result.stdout, result.stderr)
    print('APK: exact image identities pinned; upgrade preserves them; removing pins selects replacements; sysupgrade replaces old pins.')

print('Focused checks complete; no compilation, firmware build or device writes.')
