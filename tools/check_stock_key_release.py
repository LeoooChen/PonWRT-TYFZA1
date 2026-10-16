#!/usr/bin/env python3
"""Execute the original 8852C key deletion and CAM-owner release instructions."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
adapter, hw, table, owner, key_data = (0x6000000 + i * 0x10000 for i in range(5))
index = 11
trace = []
failed = None


def callback(name, args):
    trace.append(name)
    if name == 'delete_key_from_dmac_tbl_8852c':
        oracle.uc.mem_write(oracle.uc.reg_read(UC_ARM64_REG_X4), bytes([index]))
        return 49 if failed == 'DMAC' else 0
    if name == 'fill_sec_cam_info_8852c':
        return 0
    if name == 'mac_upd_sec_infotbl_8852c':
        # CAM ownership must still be held at the hardware-write boundary.
        assert oracle.unpack(owner, 'B')[0] == 1
        return 49 if failed == 'CAM' else 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['mac_sta_del_key_8852c', 'm_security_cam_hal_8852c'], callback)
oracle.uc.mem_map(adapter, 0x50000)
oracle.word(adapter + 0x58, hw)
oracle.word(hw + 0x60, table)
oracle.word(table + index * 8, owner)
oracle.word(owner + 8, key_data)
rows = []
for failed in ('DMAC', 'CAM', None):
    trace.clear()
    oracle.uc.mem_write(owner, bytes([1, 7, 0, 0]))
    oracle.uc.mem_write(key_data, bytes([6]) * 20)
    result = oracle.call('mac_sta_del_key_8852c', adapter, 7, 0, 0)
    retained = oracle.unpack(owner, 'B')[0]
    assert result == (49 if failed else 0)
    assert retained == (1 if failed else 0)
    rows.append(dict(failed_boundary=failed, result=result, CAM_owner_retained=bool(retained), calls=list(trace)))
print(json.dumps(dict(factory_module_sha256=digest,
                     actual_functions=['mac_sta_del_key_8852c', 'm_security_cam_hal_8852c'],
                     cases=rows,
                     scope='Actual original ARM64 call order and CAM-owner writes; DMAC removal, security payload packing and CAM command submission are explicit callbacks. This does not execute firmware or RF.'), indent=2))
