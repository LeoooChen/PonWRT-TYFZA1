#!/usr/bin/env python3
"""Export the public source snapshot without local evidence or Git history."""
import argparse
import json
from pathlib import Path
import re
import tarfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path, help='New directory for the public source')
parser.add_argument('--redactions', type=Path, help='Private JSON mapping of values to field names')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
values = json.loads(args.redactions.read_text()) if args.redactions else {}
substitutions = {}
for i, (value, kind) in enumerate(values.items(), 1):
    if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value):
        replacement = '02:00:00:00:00:%02x' % i
    elif re.fullmatch(r'[0-9a-fA-F]{12}', value):
        replacement = '0200000000%02x' % i
    elif kind == 'ssid':
        replacement = 'Example-AP-%d' % i
    elif kind == 'local IP':
        replacement = '192.0.2.%d' % (i % 254 + 1)
    elif kind in ('key', 'password', 'loid_password'):
        replacement = 'REDACTED'
    else:
        replacement = '<REDACTED-%s>' % kind
    substitutions[value] = replacement

paths = [root / n for n in ('README.md', 'COPYING', 'LICENSE', 'VERSION', '.gitignore', '.gitmodules')]
for directory in ('.github', 'port', 'scripts', 'tools', 'configs', 'manifests'):
    paths += sorted((root / directory).rglob('*'))
copied = 0
for source in paths:
    if not source.is_file() or source.is_symlink() or '__pycache__' in source.parts:
        continue
    if source.name.lower().startswith('readme') and source != root / 'README.md':
        continue
    if source.name.startswith('.') and source.name not in ('.gitignore', '.gitmodules'):
        continue
    if source.suffix in ('.pyc', '.bin', '.ko', '.o', '.so', '.pem', '.key', '.mod') or source.name.endswith('.mod.c'):
        continue
    if source.name in ('modules.order', 'Module.symvers'):
        continue
    if source.parent == root / 'manifests' and source.name not in (
            'radio-stock-rf-dm.json', 'wireless-study-current.json'):
        continue
    data = source.read_text()
    for value, replacement in sorted(substitutions.items(), key=lambda item: -len(item[0])):
        data = re.sub(re.escape(value), lambda match: replacement, data, flags=re.I)
    data = data.replace(str(root), '/path/to/PonWRT-TYFZA1')
    data = re.sub(r'/(?:Users|home)/[^/\s\x27\x22]+', '/path/to/user', data)
    destination = args.output / source.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(data)
    destination.chmod(source.stat().st_mode & 0o777)
    copied += 1
archive = args.output.parent / (args.output.name + '.tar.gz')
def public_owner(member):
    member.uid = member.gid = 0
    member.uname = member.gname = 'root'
    return member

with tarfile.open(archive, 'w:gz') as tar:
    tar.add(args.output, arcname='PonWRT-TYFZA1', filter=public_owner)
print(f'Public source: {copied} files; private inputs, keys and history excluded')
print(archive)
