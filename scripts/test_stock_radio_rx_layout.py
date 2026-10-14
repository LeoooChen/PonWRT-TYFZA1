#!/usr/bin/env python3
"""Execute stock RX WD parsing before moving RXQ ownership into PE."""
import hashlib
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
names = ['hal_parsing_rx_wd_' + chip for chip in ('8852c', '8192xb')]


def callback(name, args):
    if name == 'printk':
        return 0
    if name == '_rtw_memcpy':
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    raise AssertionError((name, args))


oracle = ModuleOracle(module, names, callback)
phl, hal, buffer, output = [0x6000000 + i * 0x10000 for i in range(4)]
for address in (phl, hal, buffer, output):
    oracle.uc.mem_map(address, 65536)
oracle.uc.mem_write(phl + 12808, struct.pack('<I', 1))
checks = 0
for chip in ('8852c', '8192xb'):
    for kind in (0, 1, 7, 10):
        for long_wd in range(2):
            for shift in range(4):
                for driver_info in range(8):
                    for mac_info in range(2):
                        length = 100 + checks % 1400
                        word = length | shift << 14 | mac_info << 23 | kind << 24 | driver_info << 28 | long_wd << 31
                        raw = struct.pack('<I8I', 0, word, 0, 0, 0, 0, 1 << 28, 0, 0)
                        oracle.uc.mem_write(buffer, raw)
                        oracle.uc.mem_write(output, bytes(512))
                        oracle.uc.mem_write(phl + 0x7ef0, struct.pack('<I', 256))
                        args = [phl, hal, buffer, output, output + 16]
                        if chip == '8192xb':
                            args.append(output + 24)
                        args.append(output + 32)
                        assert oracle.call('hal_parsing_rx_wd_' + chip, *args) == 0
                        expected = 4 + (32 if long_wd else 16) + shift * 2 + driver_info * 16
                        if mac_info and kind != 1:
                            expected += 4
                        actual = oracle.unpack(output, 'Q')[0] - buffer
                        assert (actual, oracle.unpack(output + 16, 'H')[0]) == (expected, length), (chip, kind, long_wd, shift, driver_info, mac_info, actual, expected)
                        checks += 1

result = {'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'functions': names, 'cases': checks, 'RXBD_bytes': 4,
          'WD_bytes': [16, 32], 'shift_unit': 2, 'driver_info_unit': 16,
          'MAC_info_bytes': 4, 'PPDU_type1_MAC_info_exception': True,
          'scope': 'Original host instructions with synthetic WD; not radio DMA'}
(root / 'logs/stock-radio-rx-layout.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
