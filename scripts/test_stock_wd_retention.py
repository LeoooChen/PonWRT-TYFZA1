#!/usr/bin/env python3
"""Execute this board's C/XB WD release path, including its eight-page work ring."""
from pathlib import Path
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
phl, common, hci, bd, wd, work, pages = [0x6000000 + n * 0x10000 for n in range(7)]
released = []


def callback(name, args):
    if name == 'enqueue_idle_wd_page':
        assert args[:2] == [phl, wd]
        released.append(args[2])
    elif name not in ('rtw_phl_get_stainfo_by_macid_wo_lock', '__local_bh_enable_ip'):
        raise AssertionError((name, args))
    return 0


oracle = ModuleOracle(module, ['rtw_release_busy_wd_page', 'hal_hci_cfg_8852ce',
                              'hal_hci_cfg_8192xbe'], callback,
                      external_symbols={'vabits_actual': 0x5007f00})
put_vabits = struct.pack('<Q', 48)
oracle.uc.mem_write(0x5007f00, put_vabits)
oracle.uc.mem_map(phl, 0x80000)


def put(address, value, fmt='I'):
    oracle.uc.mem_write(address, struct.pack('<' + fmt, value))


for chip in ('8852ce', '8192xbe'):
    oracle.uc.mem_write(phl, bytes(0x80000))
    released.clear()
    oracle.word(phl + 0x1228, common)
    oracle.word(phl + 0x5d20, hci)
    oracle.word(hci + 8, bd)
    assert oracle.call('hal_hci_cfg_' + chip, common) == 0
    assert oracle.unpack(common + 0x3232, 'H')[0] == 8
    assert oracle.unpack(common + 0x3234, 'B')[0] == 1
    oracle.word(wd + 0x10, work)
    put(wd + 0x62, 8, 'H')
    initial = [pages + i * 128 for i in range(8)]
    for i, address in enumerate(initial):
        oracle.word(work + i * 8, address)
    for i in range(32):
        page = pages + (i + 8) * 128
        sentinel = wd + 0x28
        oracle.word(sentinel, page)
        oracle.word(sentinel + 8, page)
        oracle.word(page, sentinel)
        oracle.word(page + 8, sentinel)
        put(wd + 0x5e, 1, 'H')
        assert oracle.call('rtw_release_busy_wd_page', phl, wd, 0, 1) == 0
        assert released[-1] == pages + i * 128
        assert page not in released
        assert oracle.unpack(wd + 0x64, 'H')[0] == (i + 1) % 8
    assert released == [pages + i * 128 for i in range(32)]

print(json.dumps({'stock_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                  'chips': ['8852ce', '8192xbe'], 'release_calls': 64,
                  'consumed_WD_pages_retained_per_channel': 8,
                  'scope': 'Actual factory config/release instructions; station lookup, '
                           'idle enqueue and BH accounting are explicit callbacks. '
                           'No hardware timing simulation.'}, indent=2))
