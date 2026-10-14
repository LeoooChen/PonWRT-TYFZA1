#!/usr/bin/env python3
"""Execute the stock header-CAM support gate for the actual two radio families."""
from pathlib import Path
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'

def callback(name, args):
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, ['mac_upd_shcut_mhdr', 'is_chip_id'], callback)
adapter, header = 0x6000000, 0x6010000
oracle.uc.mem_map(adapter, 0x10000)
oracle.uc.mem_map(header, 0x10000)
for chip in ('8852c', '8192xb'):
    oracle.word(adapter + 88, oracle.symbols['mac' + chip + '_hw_info'][0])
    result = oracle.call('mac_upd_shcut_mhdr', adapter, header, 1)
    assert result == 82, (chip, result)
    print(chip, 'original header-CAM update returns MACNOTSUP(82), no H2C allocation')
