#!/usr/bin/env python3
"""Execute original 8852C DMAC key-map and explicit PN-update packing."""
from pathlib import Path
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
adapter, hw, table, cache, sta = (0x6000000 + i * 0x10000 for i in range(5))
rows = []


def callback(name, args):
    assert name in ('mac_upd_dctl_info_8852c', 'rtw_hal_dmc_tbl_cfg'), name
    data = struct.unpack('<8I', oracle.uc.mem_read(args[1], 32))
    mask = struct.unpack('<8I', oracle.uc.mem_read(args[2], 32))
    assert args[3] == 7
    rows.append(dict(call=name, PN_data=list(data[:2]), PN_mask=list(mask[:2]),
                     key_selection_mask=list(mask[4:])))
    return 0


oracle = ModuleOracle(module, ['upt_dctl_secinfo_entry_8852c', 'rtw_hal_set_dctrl_tbl_seciv'], callback)
oracle.uc.mem_map(adapter, 0x50000)
oracle.word(adapter + 0x58, hw)
oracle.word(hw + 0x70, table)
oracle.word(table + 7 * 8, cache)
pn = 0x123456789abc
oracle.uc.mem_write(cache, struct.pack('<H2xI', pn & 0xffff, pn >> 16))
oracle.uc.mem_write(cache + 10, bytes([1]))
oracle.uc.mem_write(cache + 18, bytes([11]))
oracle.uc.mem_write(sta + 28, struct.pack('<H', 7))
assert oracle.call('upt_dctl_secinfo_entry_8852c', adapter, 7, 0, 0) == 0
assert rows[-1]['PN_mask'] == [0, 0]
assert oracle.call('upt_dctl_secinfo_entry_8852c', adapter, 7, 0, 1) == 0
assert rows[-1]['PN_mask'] == [0xffff0000, 0xffffffff]
assert rows[-1]['PN_data'] == [(pn & 0xffff) << 16, pn >> 16]
assert oracle.call('rtw_hal_set_dctrl_tbl_seciv', adapter, sta, pn) == 0
assert rows[-1]['PN_mask'] == [0xffff0000, 0xffffffff]
assert rows[-1]['PN_data'] == [(pn & 0xffff) << 16, pn >> 16]
assert rows[-1]['key_selection_mask'] == [0, 0, 0, 0]
print(json.dumps(dict(factory_module_sha256=digest,
                     actual_functions=['upt_dctl_secinfo_entry_8852c', 'rtw_hal_set_dctrl_tbl_seciv'],
                     cases=rows,
                     scope='Actual ARM64 field and mask packing. DMAC command/configuration calls are explicit callbacks; no firmware, TX draining or RF execution.'), indent=2))
