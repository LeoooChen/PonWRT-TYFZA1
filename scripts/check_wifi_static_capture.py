#!/usr/bin/env python3
"""Verify Wi-Fi ID separation and delete-to-miss using captured ASIC packets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('capture', type=Path)
args = parser.parse_args()
text = args.capture.read_text()
pattern = (r'phase=(hit|deleted) seq=(\d+) q=(\d+) epp=([0-9a-f]+)/([0-9a-f]+) '
           r'hA=([0-9a-f]+)/([0-9a-f]+) hCPU=([0-9a-f]+)/([0-9a-f]+) '
           r'id=(\d+) len=(-?\d+) frame=([0-9a-f]+)')
packets = {'hit': {}, 'deleted': {}}

def checksum(data):
    data += bytes(len(data) & 1)
    value = sum(struct.unpack('>' + 'H' * (len(data) // 2), data))
    while value >> 16:
        value = (value & 65535) + (value >> 16)
    return value

for row in re.findall(pattern, text):
    phase, sequence, queue, command, size, h0, h1, cpu_high, cpu_low, id, length, frame = row
    sequence, id, length = int(sequence), int(id), int(length)
    command, size, h0, h1, cpu_high, cpu_low = [int(v, 16) for v in
                                            [command, size, h0, h1, cpu_high, cpu_low]]
    frame = bytes.fromhex(frame)
    assert sequence not in packets[phase], (phase, sequence)
    assert len(frame) == 60 and frame[12:14] == b'\x08\x00'
    assert checksum(frame[14:34]) == 65535
    udp_len = struct.unpack_from('>H', frame, 38)[0]
    pseudo = frame[26:34] + bytes([0, 17]) + struct.pack('>H', udp_len)
    assert checksum(pseudo + frame[34:34 + udp_len]) == 65535
    assert frame[42:46] == b'WFNE' and struct.unpack_from('>I', frame, 46)[0] == sequence
    assert (h1 >> 9) & 63 == 18 and size & 0x3fff == 76
    assert (size >> 14) & 63 == id
    if phase == 'hit':
        assert id == 5 + (sequence & 1) and length == 60
        assert cpu_low & 255 == id and (cpu_low >> 8) & 15 == 15
        assert cpu_high == [0x189355ff, 0xee745766][sequence & 1]
        assert cpu_low >> 16 == [0xf014, 0x997e][sequence & 1]
    else:
        assert id == 0 and cpu_low & 255 == 1 + (sequence & 1)
        assert (cpu_low >> 8) & 15 == 3
    packets[phase][sequence] = frame
assert all(sorted(packets[phase]) == list(range(8)) for phase in packets)
assert packets['hit'] == packets['deleted']
assert 'IPPB18_restored=1 VP9_disabled=1 error=0' in text
assert len(re.findall(r'deleted slot=\d+ age=0 hash=00000000', text)) >= 2
record = {'capture': str(args.capture), 'capture_sha256': hashlib.sha256(args.capture.read_bytes()).hexdigest(),
          'hardware_hit_frames': 8, 'after_delete_miss_frames': 8,
          'same_five_tuple_input_IDs': [1, 2], 'distinct_output_IDs': [5, 6],
          'IPv4_UDP_checksums_valid': True, 'payload_unchanged_after_rule_delete': True,
          'real_AP_traffic': False, 'temporary_hardware_configuration_restored': True}
(root / 'logs/wifi-static-capture-check.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
