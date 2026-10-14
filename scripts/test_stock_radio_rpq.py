#!/usr/bin/env python3
"""Execute the stock C/XB RPQ decoder, including its internal TID/channel maps."""
import hashlib
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
names = [stem + '_' + chip for chip in ('8852ce', '8192xbe') for stem in
         ('hal_handle_rx_report', 'hal_qsel_to_tid', 'hal_mapping_hw_tx_chnl')]


def callback(name, args):
    assert name == 'printk', (name, args)
    return 0


oracle = ModuleOracle(module, names, callback)
hal, common, packet, output = [0x6000000 + i * 0x10000 for i in range(4)]
for address in (hal, common, packet, output):
    oracle.uc.mem_map(address, 65536)
oracle.word(hal + 8, common)
checks = 0
for chip in ('8852ce', '8192xbe'):
    for qsel, channel in ((0, 0), (1, 1), (2, 2), (3, 3), (0x12, 8), (0x11, 9)):
        for status in range(8):
            for polluted in range(2):
                for tid_indicator in range(2):
                    for wp in (0, 255, 256, 383, 4094):
                        macid = (checks * 17) & 127
                        report = (macid | qsel << 8 | status << 13 | wp << 16 |
                                  tid_indicator << 30 | polluted << 31)
                        oracle.uc.mem_write(packet, struct.pack('<I', report))
                        oracle.uc.mem_write(output, bytes([0xa5]) * 16)
                        # This stock ABI has a ninth argument on the stack: TX status.
                        oracle.word(oracle.scratch + 0xff00, output + 6)
                        assert oracle.call('hal_handle_rx_report_' + chip,
                            hal, packet, 4, output, output + 1, output + 2,
                            output + 4, output + 5) == 4
                        retry, dma, sequence, actual_macid, ac, actual_status = oracle.unpack(output, 'BBHBBB')
                        assert (dma, sequence, actual_macid, ac, actual_status) == (
                            channel, wp, macid, qsel & 3, status)
                        assert retry == bool(status or polluted)
                        checks += 1

result = {
    'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'functions': names,
    'cases': checks,
    'report_bytes': 4,
    'fields': {'MACID': '0..7', 'QSEL': '8..12', 'status': '13..15',
               'WP_sequence': '16..27', 'TID_indicator': 30, 'polluted': 31},
    'unacknowledged': 'nonzero status OR polluted bit',
    'channels': {'QSEL0..3': 'ACH0..3', 'QSEL0x12': 'channel8', 'QSEL0x11': 'channel9'},
    'scope': 'Full original host decoder and internal maps; no live RPQ DMA',
}
(root / 'logs/stock-radio-rpq-contract.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
