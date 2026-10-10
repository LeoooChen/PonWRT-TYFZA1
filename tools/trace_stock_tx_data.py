#!/usr/bin/env python3
"""Read original DATA-WD output for the native PCIe/SCC packet controls."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = [base + '_' + chip for chip in ('8852c', '8192xb')
         for base in ('txdes_proc_data', 'mac_txdesc_len')]
names.append('_phl_fill_tx_meta_data')
def callback(name, args):
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    if name == 'printk':
        return 0
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
adapter, ops, hwinfo, metadata, output = 0x6000000, 0x6010000, 0x6020000, 0x6030000, 0x6040000
request = 0x6050000
dbcc, wmm = 0x6060000, 0x6070000
for address in (adapter, ops, hwinfo, metadata, output, request, dbcc, wmm):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter + 24, ops)
oracle.word(adapter + 88, hwinfo)
oracle.word(adapter + 1464, dbcc)
oracle.word(dbcc + 64, wmm)
oracle.uc.mem_write(wmm, bytes([4]) * 32)
oracle.word(ops + 88, oracle.callback('platform_memcpy'))
# Actual MAC interface enum PCIe2; ordinary SCC qta0. Both original
# mac*_hw_info objects have wd_checksum_en@160=0.
oracle.uc.mem_write(hwinfo + 8, struct.pack('<I', 2))
programs = []
for chip, rate in (('8852c', 4), ('8192xb', 0)):
    for tid in range(8):
        length = 324
        data = bytearray(96)
        struct.pack_into('<IIH', data, 0, 0, tid, length)
        data[12] = 0x10 | tid  # broadcast; management TID0
        struct.pack_into('<H', data, 18, 1)
        data[20] = 13  # 24-byte raw 802.11 header in two-byte units
        channel = [0,1,1,0,2,2,3,3][tid]
        data[33] = 0x20 | channel  # WD-info and management DMA8
        data[34] = 1
        struct.pack_into('<Q', data, 32, int.from_bytes(data[32:40], 'little') | rate << 51)
        struct.pack_into('<Q', data, 40, 0x1840 | (1 << 56))  # fixed rate, disable FB, no ACK
        oracle.uc.mem_write(request, bytes(256))
        oracle.uc.mem_write(request + 32, bytes(data))
        oracle.uc.mem_write(request + 108, b'\x01')
        oracle.call('_phl_fill_tx_meta_data', request, length)
        oracle.uc.mem_write(metadata, bytes(oracle.uc.mem_read(request + 32, 96)))
        oracle.uc.mem_write(output, bytes(128))
        size = oracle.call('mac_txdesc_len_' + chip, adapter, metadata)
        status = oracle.call('txdes_proc_data_' + chip, adapter, metadata, output, size)
        assert status == 0 and size == 56
        words = list(oracle.unpack(output, '14I'))
        programs.append({'chip': chip, 'packet_bytes': length, 'rate': rate, 'MAC_band': 0,
                         'MACID': 0, 'software_sequence': 1, 'header_bytes': 26, 'tid': tid, 'DMA_channel': channel,
                         'address_entries': 1, 'broadcast': True, 'no_ACK': True,
                         'original_PHL_WP_offset': oracle.unpack(metadata + 21, 'B')[0],
                         'original_PHL_WD_page_size': (oracle.unpack(metadata + 33, 'B')[0] >> 4) & 1,
                         'WD_bytes': size, 'original_words': [hex(word) for word in words]})
report = {'scope': 'original ELF descriptor construction; not a hardware transmit test',
          'original_host_sha256': hashlib.sha256(module.read_bytes()).hexdigest(), 'programs': programs}
target = root / 'manifests/radio-stock-data-WD.json'
target.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
