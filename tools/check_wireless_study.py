#!/usr/bin/env python3
"""Compare the user's current wireless material with the frozen project inputs."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('--baseline', type=Path, default=root / 'evidence/wireless-study-20261008')
parser.add_argument('--output', type=Path, default=root / 'logs/wireless-study-current.json')
args = parser.parse_args()

def inventory(directory):
    entries = {}
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path.name != '.DS_Store' and '.git' not in path.parts:
            data = path.read_bytes()
            entries[str(path.relative_to(directory))] = {
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return entries

current = inventory(args.source)
baseline = inventory(args.baseline)
added = sorted(current.keys() - baseline.keys())
removed = sorted(baseline.keys() - current.keys())
changed = sorted(name for name in current.keys() & baseline.keys()
                 if current[name]['sha256'] != baseline[name]['sha256'])
record = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
          'source': str(args.source.resolve()), 'baseline': str(args.baseline.resolve()),
          'files': current, 'added': added, 'changed': changed, 'removed': removed}
args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'files': len(current), 'bytes': sum(v['bytes'] for v in current.values()),
                  'added': added, 'changed': changed, 'removed': removed,
                  'next': 'Read changed inputs before using them' if added or changed else 'Current inputs match frozen snapshot'},
                 ensure_ascii=False, indent=2))
