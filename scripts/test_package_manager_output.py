#!/usr/bin/env python3
"""Run the production package helper with fake apk, real ash/flock and ucode."""
from pathlib import Path
import concurrent.futures
import json
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
tree = root / 'sources/openwrt'
path = 'applications/luci-app-package-manager/root/usr/libexec/package-manager-call'
original = subprocess.check_output(
    ['git', '-C', str(tree / 'feeds/luci'), 'show', 'HEAD:' + path], text=True)

with tempfile.TemporaryDirectory(prefix='package-output-') as directory:
    work = Path(directory)
    helper = work / path
    helper.parent.mkdir(parents=True)
    helper.write_text(original)
    subprocess.run(['patch', '-p1', '--fuzz=0', '-i', str(root /
                   'port/luci-patches/113-tyfza1-package-output.patch')], cwd=work, check=True)
    production = helper.read_text()
    assert '/tmp/ipkg.lock /tmp/ipkg.err' not in production
    # Apply the frontend patch to pinned upstream too; an already patched
    # developer build must not hide a missing or broken repository patch.
    frontend_path = 'applications/luci-app-package-manager/htdocs/luci-static/resources/view/package-manager.js'
    translation_path = 'applications/luci-app-package-manager/po/zh_Hans/package-manager.po'
    for name in [frontend_path, translation_path]:
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(
            ['git', '-C', str(tree / 'feeds/luci'), 'show', 'HEAD:' + name]))
    subprocess.run(['patch', '-p1', '--fuzz=0', '-i', str(root /
                   'port/luci-patches/114-tyfza1-package-rejection.patch')], cwd=work, check=True)
    frontend = (work / frontend_path).read_text()
    handler = frontend[frontend.index('function handlePkg(ev)'):frontend.index('function handleUpload(ev)')]
    js = r'''const assert = require('assert');
let calls = 0, hidden = 0, notifications = [], mode;
const failure = new Error('connection lost');
String.prototype.format = function() { return String(this); };
const _ = s => s;
const E = (...args) => args;
const L = {hasSystemFeature: () => true};
const document = {querySelector: () => null};
const ui = {
 showModal: () => ({removeChild() {}, appendChild() {}}),
 hideModal: () => { hidden++; },
 addNotification: (title, message) => notifications.push(message)
};
const fs = {exec_direct: () => {
 calls++;
 return mode == 'network' ? Promise.reject(failure) : Promise.resolve(null);
}};
''' + handler + r'''
(async () => {
 for (mode of ['network', 'invalid-json']) {
  calls = hidden = 0; notifications = [];
  const result = await Promise.race([
   handlePkg({target:{getAttribute: key => key == 'data-command' ? 'install' : 'example'}})
    .then(() => 'resolved', err => err),
   new Promise(resolve => setTimeout(() => resolve('pending'), 200))
  ]);
  assert(result instanceof Error, 'operation must reject instead of hanging');
  if (mode == 'network') assert.strictEqual(result, failure);
  assert.strictEqual(calls, 1, 'must not retry an operation with unknown outcome');
  assert.strictEqual(hidden, 1);
  assert(notifications.some(n => JSON.stringify(n).includes('may still be running')));
 }
})().catch(err => { console.error(err); process.exitCode = 1; });
'''
    subprocess.run(['node', '-e', js], check=True, timeout=10)
    apk = work / 'apk'
    apk.write_text('''#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
root = Path(__file__).parent
try:
    fd = os.open(root / 'active', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    sys.exit(99)
try:
    os.fstat(200)
    sys.exit(98)
except OSError:
    pass
try:
    time.sleep(0.04)
    print(sys.argv[-1] + ':' + 'x' * 262144)
    print('diagnostic: "quoted"\\nnext line', file=sys.stderr)
finally:
    os.close(fd)
    os.unlink(root / 'active')
sys.exit(7 if sys.argv[-1] == 'fail' else 0)
''')
    apk.chmod(0o755)
    helper.write_text(production.replace('/tmp/', str(work) + '/').replace('/usr/bin/apk', str(apk)))
    env = dict(os.environ, PATH=str(work) + ':' + str(tree / 'staging_dir/hostpkg/bin')
               + ':' + str(tree / 'staging_dir/host/bin') + ':' + os.environ['PATH'])

    def run(name):
        result = subprocess.run(['busybox', 'ash', str(helper), 'install', name],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data['code'] == (7 if name == 'fail' else 0), data
        assert data['stdout'] == name + ':' + 'x' * 262144 + '\n'
        assert data['stderr'] == 'diagnostic: "quoted"\nnext line\n'
        assert data['pkmcmd'] == 'apk add ' + name

    run('first')
    inode = (work / 'ipkg.lock').stat().st_ino
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, ['job' + str(i) for i in range(24)] + ['fail']))
    assert (work / 'ipkg.lock').stat().st_ino == inode
    assert not list(work.glob('package-manager.*'))
    # Reproduce the old jshn failure with a log larger than one exec env string.
    libubox = next((tree / 'build_dir/target-aarch64_cortex-a55_musl').glob('libubox-*/sh/jshn.sh'))
    baseline = work / 'baseline.sh'
    baseline.write_text(f'. "{libubox}"\njson_init\n'
                        f'stdout=$(head -c 262144 /dev/zero | tr "\\000" x)\n'
                        'json_add_string stdout "$stdout"\njson_dump\n')
    result = subprocess.run(['busybox', 'ash', str(baseline)], env=env, capture_output=True)
    assert result.returncode != 0 and not result.stdout
    assert b'Argument list too long' in result.stderr, result.stderr
    print('PASS: baseline empty JSON reproduced; large logs, exit codes, quoting, '
          '26 operations, concurrent isolation, stable lock, cleanup and frontend rejection without retry')
