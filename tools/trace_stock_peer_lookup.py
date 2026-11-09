#!/usr/bin/env python3
"""Execute target per-adapter station hash and cache lookup for identical peer MACs."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--module', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()

def boundary(name, args):
    if name == '_rtw_memcmp':
        return int(bytes(o.uc.mem_read(args[0], args[2])) == bytes(o.uc.mem_read(args[1], args[2])))
    if name == 'rtw_end_of_queue_search':
        return int(args[0] == args[1])
    if name == '__local_bh_enable_ip':
        return 0
    raise AssertionError((name, args))

o = ModuleOracle(a.module, ['rtw_get_stainfo'], boundary, external_symbols={'vabits_actual': 0x5007000})
o.word(0x5007000, 48)
base = 0x6000000
o.uc.mem_map(base, 0x30000)
peer = bytes.fromhex('020001020304')
query = o.scratch
o.uc.mem_write(query, peer)
hash_value = 0
for value in peer:
    hash_value = value ^ (hash_value << 2)
hash_value = (hash_value ^ (hash_value >> 8)) % 73
records = []
for index in range(2):
    priv = base + index * 0x10000
    adapter, role, station, phl = [priv + i * 0x1000 for i in range(1, 5)]
    o.word(priv + 1256, adapter)
    o.word(adapter + 32, role)
    o.uc.mem_write(role + 8, struct.pack('<I', 2))  # AP role
    o.word(station, phl)
    o.uc.mem_write(phl + 24, b'\x01')
    o.uc.mem_write(phl + 30, peer)
    for bucket in range(73):
        head = priv + 32 + bucket * 16
        o.word(head, head)
        o.word(head + 8, head)
    head = priv + 32 + hash_value * 16
    o.word(head, station + 32)
    o.word(head + 8, station + 32)
    o.word(station + 32, head)
    o.word(station + 40, head)
    records.append((priv, station, phl))
checks = 0
for _ in range(3):
    for priv, station, phl in records:
        assert o.call('rtw_get_stainfo', priv, query) == station
        assert o.unpack(priv + 1264, 'Q')[0] == station
        checks += 1
# Clearing one adapter's active station must not fall back into the other's hash.
for index in range(2):
    priv, station, phl = records[index]
    o.uc.mem_write(phl + 24, b'\x00')
    assert o.call('rtw_get_stainfo', priv, query) == 0
    other_priv, other_station, _ = records[1 - index]
    assert o.call('rtw_get_stainfo', other_priv, query) == other_station
    o.uc.mem_write(phl + 24, b'\x01')
    checks += 2
result = {'module_sha256': hashlib.sha256(a.module.read_bytes()).hexdigest(),
          'function': 'rtw_get_stainfo@0xccae8', 'checks': checks,
          'same_peer_separate_adapter_hash_and_cache': True,
          'inactive_peer_no_cross_adapter_fallback': True,
          'scope': 'Actual target instructions with constructed station lists; memory comparison, queue-end and BH unlock boundaries modeled.'}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result))
