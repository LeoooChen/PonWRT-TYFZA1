#!/usr/bin/env python3
"""Validate prepared NI prefixes through actual linked admission and mixed gather."""
from pathlib import Path
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'scripts'))
fixture = root / 'scripts/test_pe_ps_selection.py'
ns = {'__file__': str(fixture)}
exec(compile(fixture.read_text().split('\nrows = []')[0], str(fixture), 'exec'), ns)
BASE, PAYLOAD, EPP, NI = ns['BASE'], ns['ns']['PAYLOAD'], ns['ns']['EPP'], ns['ns']['NI']
rows = []
for header in (8, 16):
    for length in (14, 15, 64, 1000, 1001, 1500):
        machine = ns['ns']['Radio']()
        machine.packets([1])
        machine.put(BASE + 0xe810, 0)
        frame = bytes.fromhex('0200000000010200000000090800')
        frame += bytes((index * 17) & 255 for index in range(length - 14))
        machine.u.mem_write(PAYLOAD + 64, bytes(16) + frame + bytes(16))
        machine.u.mem_write(PAYLOAD + 64 + header, frame)
        if header == 16:
            machine.put(EPP + 4, (1 << 14) | (length + 16))
        else:
            machine.put(EPP + 4, 0)
            machine.u.mem_write(PAYLOAD + 64, bytes(4) +
                               ((length + header - 8) << 15).to_bytes(4, 'big'))
        ns['call'](machine, 'ni_ingest')
        assert machine.get(NI + 0x7160) == 8 and machine.get(BASE + 0x11804) == 1
        prepared = length > 1000
        address = PAYLOAD + 64 + header
        assert bytes(machine.u.mem_read(address - (8 if prepared else 0), 12)) == frame[:12]
        assert bytes(machine.u.mem_read(address + 12, length - 12)) == frame[12:]
        assert machine.get(PAYLOAD + 4) == (length | header << 16 | (1 << 24 if prepared else 0))
        rows.append({'header': header, 'length': length, 'prepared': prepared})

from test_pe_downlink_gather import case
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
mixed = []
for lengths in ([54, 1500, 64, 1514, 66], [1000, 1001, 999, 1002], [1500], [64]):
    for client in (False, True):
        mixed.append(case(lengths, 10896, True, client=client,
                          policy=10896 if len(lengths) > 1 else 0))

memory = []
for lengths in ([1514] * 7, [64], [64] * 7):
    variants = []
    for prepared in (False, True):
        counts = {'read': 0, 'write': 0}

        def observe(machine):
            def record(uc, access, address, size, value, operation):
                counts[operation] += 1
            machine.hook_add(UC_HOOK_MEM_READ, record, begin=PAYLOAD, end=PAYLOAD + 0xffff,
                             user_data='read')
            machine.hook_add(UC_HOOK_MEM_WRITE, record, begin=PAYLOAD, end=PAYLOAD + 0xffff,
                             user_data='write')

        # Compare the same linked FTTR image with queued prefixes present or
        # absent. Main's absolute counts omit FTTR ownership/generation checks.
        case(lengths, 10896, observe=observe, prepare_prefix=prepared,
             policy=10896 if len(lengths) > 1 else 0)
        variants.append(counts)
    if lengths[0] > 1000:
        assert variants[1]['read'] < variants[0]['read'], variants
        assert variants[1]['write'] < variants[0]['write'], variants
    else:
        assert variants[0] == variants[1], variants
    memory.append({'lengths': lengths, 'unprepared': variants[0], 'prepared': variants[1]})

multicast = []
for header in (8, 16):
    for source in (0, 6):
        for client, preserve in ((False, False), (True, False), (True, True)):
            for lengths in ([1000, 1001, 64, 1514], [1500]):
                multicast.append(case(lengths, 10896, True, ni_header=header,
                    client=client, multicast=True, preserve_group=preserve, source=source,
                    policy=10896 if len(lengths) > 1 else 0))
print(json.dumps({'ingress': rows, 'mixed': mixed, 'multicast': multicast,
                  'payload_memory_operations': memory}, indent=2))
