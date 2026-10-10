#!/usr/bin/env python3
"""Parse actual board RX WD snapshots with the original ARM64 function."""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
source = Path(sys.argv[1])
text = source.read_text()
chip = sys.argv[2]
name = 'hal_parsing_rx_wd_' + chip
def callback(name, args):
    if name == 'printk':
        return 0
    if name == '_rtw_memcpy':
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, [name], callback)
phl, hal, rxbuffer, output = 0x6000000, 0x6010000, 0x6020000, 0x6030000
for address in (phl, hal, rxbuffer, output):
    oracle.uc.mem_map(address, 0x10000)
# Actual parser adds RXBD4 for PCIe HCI1. Its uncached normal RX path
# uses the original RXSC_INVALID_MACID=256, not a cached mdata result.
oracle.uc.mem_write(phl + 12808, struct.pack('<I', 1))
oracle.uc.mem_write(phl + 0x7ef0, struct.pack('<I', 256))
results = []
pattern = r'encoding=(\d+) sequence=(\d+) bytes=(\d+) driver_info=(\d+) payload_offset=(\d+)[^\n]*\nWD=([0-9a-f,]+) prefix=([0-9a-f]*)'
for match in re.finditer(pattern, text):
    encoding, sequence, length, info, offset = map(int, match.groups()[:5])
    if not sequence:
        continue
    words = [int(value, 16) for value in match[6].split(',')]
    oracle.uc.mem_write(rxbuffer, bytes(0x4000))
    oracle.uc.mem_write(rxbuffer + 4, struct.pack('<8I', *words))
    oracle.uc.mem_write(rxbuffer + 4 + offset, bytes.fromhex(match[7]))
    oracle.uc.mem_write(output, bytes(512))
    # XB's original signature has a separate PHY-status pointer output in X5;
    # its RX metadata is in X6. C has metadata in X5 and no PHY pointer output.
    args = [phl, hal, rxbuffer, output, output + 16]
    if chip == '8192xb':
        args.append(output + 24)
    args.append(output + 32)
    status = oracle.call(name, *args)
    metadata = output + 32
    original_pointer = oracle.unpack(output, 'Q')[0]
    original_length = oracle.unpack(output + 16, 'H')[0]
    original_offset = original_pointer - rxbuffer - 4
    assert status == 0 and original_length == length and original_offset == offset
    assert oracle.unpack(metadata + 17, 'B')[0] * 16 == info
    results.append({'encoding': encoding, 'actual_snapshot_sequence': sequence,
                    'original_status': status, 'original_packet_length': original_length,
                    'original_payload_offset_from_WD': original_offset,
                    'original_driver_info_bytes': info,
                    'original_rate': oracle.unpack(metadata + 24, 'H')[0],
                    'original_GI_LTF': oracle.unpack(metadata + 26, 'B')[0],
                    'original_bandwidth': oracle.unpack(metadata + 30, 'B')[0],
                    'original_CRC_error': oracle.unpack(metadata + 45, 'B')[0],
                    'original_ICV_error': oracle.unpack(metadata + 46, 'B')[0]})
report = {'scope': 'actual original parser on WD bytes captured from this board; no simulated RF completion',
          'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'observation_source': str(source), 'observation_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'chip': chip, 'native_packet_length_and_offset_equal_original': True, 'snapshots': results}
target = Path(sys.argv[3])
target.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
