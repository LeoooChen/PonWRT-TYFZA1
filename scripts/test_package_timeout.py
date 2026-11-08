#!/usr/bin/env python3
"""Exercise first-boot timeout migration, preserving deliberate custom values."""
from pathlib import Path
import json
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
script = root / 'port/overlay/target/linux/rtl9617c/base-files/etc/uci-defaults/99-tyfza1-package-timeout'
with tempfile.TemporaryDirectory(prefix='package-timeout-') as directory:
    work = Path(directory)
    state = work / 'state.json'
    state.write_text(json.dumps({'main': '60', '@uhttpd[1]': None,
                                'custom': '1200', 'disabled': '0', 'short': '30'}))
    uci = work / 'uci'
    uci.write_text('''#!/usr/bin/env python3
import json, os, sys
path = os.environ['TEST_UCI_STATE']
data = json.load(open(path))
args = [x for x in sys.argv[1:] if x != '-q']
if args[0] == 'show':
    for key in data: print('uhttpd.' + key + '=uhttpd')
    print('uhttpd.defaults=cert')
elif args[0] == 'get':
    value = data[args[1].split('.')[1]]
    if value is None: sys.exit(1)
    print(value)
elif args[0] == 'set':
    key, value = args[1].split('=')
    data[key.split('.')[1]] = value
    with open(path, 'w') as f: json.dump(data, f)
else:
    assert args == ['commit', 'uhttpd']
''')
    uci.chmod(0o755)
    env = dict(os.environ, TEST_UCI_STATE=str(state), PATH=str(work) + ':' + os.environ['PATH'])
    for _ in range(2):
        subprocess.run(['sh', str(script)], env=env, check=True)
        assert json.loads(state.read_text()) == {
            'main': '3600', '@uhttpd[1]': '3600', 'custom': '1200', 'disabled': '0', 'short': '30'}
    print('PASS: default/missing CGI limit migrated; named/anonymous sections, custom limits and repeated boot preserved')
