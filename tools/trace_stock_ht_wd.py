#!/usr/bin/env python3
"""Execute actual C/XB DATA descriptors for HT MCS0..15 and long/short GI."""
from pathlib import Path
import hashlib
import json
import struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['_phl_fill_tx_meta_data'] + [base + '_' + chip for chip in ('8852c', '8192xb')
                                    for base in ('txdes_proc_data', 'mac_txdesc_len')]

def callback(name, args):
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    if name == 'printk':
        return 0
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
adapter, platform, hwinfo, metadata, output, request = [0x6000000 + n * 0x10000 for n in range(6)]
dbcc, wmm = 0x6060000, 0x6070000
for address in (adapter, platform, hwinfo, metadata, output, request, dbcc, wmm):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter + 24, platform)
oracle.word(adapter + 88, hwinfo)
oracle.word(platform + 88, oracle.callback('platform_memcpy'))
oracle.word(adapter + 1464, dbcc)
oracle.word(dbcc + 64, wmm)
oracle.uc.mem_write(wmm, bytes([4]) * 32)
oracle.uc.mem_write(hwinfo + 8, struct.pack('<I', 2))
records = []
for chip in ('8852c', '8192xb'):
    for mcs in range(16):
        for gi, aggregate in ((0, False), (1, False), (1, True)):
            data = bytearray(96)
            struct.pack_into('<IIH', data, 0, 0, 0, 1536)
            struct.pack_into('<H', data, 14, 1)  # peer MACID1
            struct.pack_into('<H', data, 18, 7)
            data[20] = 13  # raw QoS header26
            data[33] = 0x20  # SCC DATA0,WD-info
            struct.pack_into('<Q', data, 32, int.from_bytes(data[32:40], 'little') | (0x80 + mcs) << 51)
            if aggregate:
                value = int.from_bytes(data[32:40], 'little') | 64 << 32 | 1 << 41 | 4 << 43
                struct.pack_into('<Q', data, 32, value)
            struct.pack_into('<Q', data, 40, 0x1840 | gi)  # fixed rate,no fallback,ACK required
            oracle.uc.mem_write(request, bytes(256))
            oracle.uc.mem_write(request + 32, bytes(data))
            oracle.uc.mem_write(request + 108, b'\x01')
            oracle.call('_phl_fill_tx_meta_data', request, 1536)
            oracle.uc.mem_write(metadata, bytes(oracle.uc.mem_read(request + 32, 96)))
            oracle.uc.mem_write(output, bytes(128))
            size = oracle.call('mac_txdesc_len_' + chip, adapter, metadata)
            assert size == 56
            assert oracle.call('txdes_proc_data_' + chip, adapter, metadata, output, size) == 0
            records.append({'chip': chip, 'MCS': mcs, 'GI': gi, 'bandwidth': 20, 'MACID': 1,
                            'AMPDU': aggregate, 'maximum_aggregation': 64 if aggregate else 0,
                            'density': 4 if aggregate else 0,
                            'metadata': bytes(oracle.uc.mem_read(metadata, 96)).hex(),
                            'WD_words': [hex(w) for w in oracle.unpack(output, '14I')]})
(root / 'manifests/radio-stock-HT-WD.json').write_text(json.dumps({
    'scope': __doc__, 'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions': names, 'records': records}, indent=2) + '\n')
for chip in ('8852c', '8192xb'):
    for r in records:
        if r['chip'] == chip and r['MCS'] in (0, 15):
            print(chip, r['MCS'], r['GI'], r['AMPDU'], r['WD_words'])
