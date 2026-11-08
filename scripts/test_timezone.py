#!/usr/bin/env python3
"""Exercise timezone migration with real UCI, preserving explicit settings."""
from pathlib import Path
import os
import shlex
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from uci_config import command

source = root / 'port/overlay/target/linux/rtl9617c/base-files/etc/uci-defaults/95-tyfza1-timezone'
with tempfile.TemporaryDirectory(prefix='timezone-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    config, stage = work / 'config', work / 'stage'
    config.mkdir()
    stage.mkdir()
    cli = command() + ['-c', str(config), '-t', str(stage)]
    wrapper = work / 'uci'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(cli) + ' "$@"\n')
    wrapper.chmod(0o755)
    env = dict(os.environ, PATH=str(work) + ':' + os.environ['PATH'])
    for zone, tz, expected in [
        ('UTC', 'GMT0', ('Asia/Shanghai', 'CST-8')),
        ('', '', ('Asia/Shanghai', 'CST-8')),
        ('Asia/Shanghai', 'GMT0', ('Asia/Shanghai', 'CST-8')),
        ('UTC', 'CST-8', ('Asia/Shanghai', 'CST-8')),
        ('Asia/Shanghai', 'CST-8', ('Asia/Shanghai', 'CST-8')),
        ('Europe/Berlin', 'CET-1CEST,M3.5.0,M10.5.0/3', ('Europe/Berlin', 'CET-1CEST,M3.5.0,M10.5.0/3')),
        ('Etc/UTC', 'UTC0', ('Etc/UTC', 'UTC0')),
        ('UTC', 'JST-9', ('UTC', 'JST-9')),
    ]:
        (config / 'system').write_text(
            "config system\n option hostname 'KeepMe'\n" +
            (f" option zonename '{zone}'\n" if zone else '') +
            (f" option timezone '{tz}'\n" if tz else '') +
            "config timeserver 'ntp'\n option enabled '0'\n list server 'time.example.test'\n")
        subprocess.run(['sh', str(source)], env=env, check=True)
        def get(name):
            return subprocess.check_output(cli + ['get', 'system.' + name], text=True).strip()
        assert (get('@system[0].zonename'), get('@system[0].timezone')) == expected
        assert get('@system[0].hostname') == 'KeepMe'
        assert get('ntp.enabled') == '0' and get('ntp.server') == 'time.example.test'
        before = (config / 'system').read_bytes()
        subprocess.run(['sh', str(source)], env=env, check=True)
        assert before == (config / 'system').read_bytes()
    assert subprocess.check_output(['date', '-d', '@0', '+%H:%M %z'], env=dict(os.environ, TZ='CST-8'), text=True).strip() == '08:00 +0800'
print('Timezone: defaults and partial Shanghai settings migrate; custom zones, NTP and hostname survive; repeat is idempotent; UTC+8 verified.')
