#!/usr/bin/env python3
"""Execute TYFZA1 original default station CMAC/DMAC table construction.

CAM creation and BB host-list insertion are explicit external boundaries.
The actual HAL table initialization, antenna selection and MAC H2C encoders
execute from the original ELF. No hardware access or RF claim is made.
"""
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_INVALID
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_hal_add_sta_entry', 'rtw_hal_update_cctrl_tbl', '_hal_sta_set_default_value', '_hal_update_dctrl_tbl',
         'rtw_hal_cmc_tbl_cfg', 'rtw_hal_dmc_tbl_cfg', 'mac_upd_cctl_info_8852c', 'mac_upd_cctl_info_8192xb', 'mac_upd_dctl_info_8852c', 'mac_upd_dctl_info_8192xb',
         'rtw_hal_bb_cfg_cmac_tx_ant', 'halbb_cfg_cmac_tx_ant',
         'halbb_cfg_cmac_tx_ant_8852c', 'halbb_cfg_cmac_tx_ant_8192xb']
hal, common, sta, hsta, bb, adapter, ops, platform, packet, payload = [0x6000000+i*0x10000 for i in range(10)]
operations = []
size = 0
buffer_class = 0

def callback(name, args):
    global size, buffer_class
    if name == '_rtw_memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name in ('rtw_hal_mac_addr_cam_add_entry', 'rtw_hal_bb_stainfo_add', 'mac_upd_role_cctrl_v1_8852c', 'mac_upd_role_cctrl_v1_8192xb'):
        operations.append({'boundary': name})
        return 0
    if name in ('printk', 'h2cb_free', 'h2c_pkt_build_txd', 'platform_TX'):
        return 0
    if name == 'h2cb_alloc':
        buffer_class = args[1]
        oracle.uc.mem_write(packet, bytes(256))
        return packet
    if name == 'h2cb_put':
        size = args[1]
        oracle.uc.mem_write(payload, bytes(512))
        return payload
    if name == 'h2c_pkt_set_hdr':
        extra = [oracle.uc.reg_read(r) for r in (UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
        operations.append({'H2C_buffer': buffer_class, 'category': args[3], 'class': extra[0],
            'function': extra[1], 'receive_ACK': extra[2], 'done_ACK': extra[3],
            'payload': bytes(oracle.uc.mem_read(payload, size)).hex()})
        return 0
    if name == 'h2c_agg_enqueue':
        return 1
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
for address in (hal, common, sta, hsta, bb, adapter, ops, platform, packet, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(hal, common)
oracle.word(hal+8,common)
oracle.word(hal+328, adapter)
oracle.word(hal+336, bb)
oracle.word(sta+2688, hsta)
oracle.word(adapter, ops)
oracle.word(adapter+24, platform)
oracle.uc.mem_write(adapter+33, b'\x04')


oracle.word(platform+48, oracle.callback('platform_TX'))
role, dvobj, rf = 0x60a0000, 0x60b0000, 0x7000000
oracle.uc.mem_map(role,0x10000);oracle.uc.mem_map(dvobj,0x10000);oracle.uc.mem_map(rf,0x100000)
oracle.word(sta+16,role)
oracle.uc.mem_write(role+8,struct.pack('<I',2))
oracle.uc.mem_write(role+204,b'\xff\xff')
oracle.word(common+1896,dvobj);oracle.word(dvobj+176,rf)
def invalid(uc,access,address,size,value,data):
    print('Unmapped original input',hex(address),size,hex(uc.reg_read(UC_ARM64_REG_PC)),file=__import__('sys').stderr); return False
oracle.uc.hook_add(UC_HOOK_MEM_INVALID,invalid)
records=[]
for chip, ic in [('8852c', 8), ('8192xb', 32)]:
    oracle.word(adapter+88, oracle.symbols['mac'+chip+'_hw_info'][0])
    # Read the real relocated operation slots, instead of substituting
    # a same-named generic encoder. This was the earlier trace mistake.
    original_ops=oracle.symbols['mac'+chip+'_ops'][0]
    for offset, name in ((272,'mac_upd_dctl_info_'+chip),(280,'mac_upd_cctl_info_'+chip)):
        target=oracle.unpack(original_ops+offset,'Q')[0]
        assert target == oracle.functions[name], (chip,offset,target,name)
        oracle.word(ops+offset,target)
    oracle.uc.mem_write(bb+3092, struct.pack('<I', ic))
    for tx_nss in (1,2):
        oracle.uc.mem_write(common+0x5875, bytes([tx_nss]))
        for macid in (0,1):
            operations.clear()
            oracle.uc.mem_write(sta+28, struct.pack('<H', macid))
            status=oracle.call('rtw_hal_add_sta_entry', hal, sta)
            records.append({'chip':chip,'tx_nss':tx_nss,'MACID':macid,'status':status,'operations':list(operations)})
updates=[]
for chip in ('8852c','8192xb'):
    oracle.word(adapter+88,oracle.symbols['mac'+chip+'_hw_info'][0])
    # Read the real relocated operation slots, instead of substituting
    # a same-named generic encoder. This was the earlier trace mistake.
    original_ops=oracle.symbols['mac'+chip+'_ops'][0]
    for offset, name in ((272,'mac_upd_dctl_info_'+chip),(280,'mac_upd_cctl_info_'+chip)):
        target=oracle.unpack(original_ops+offset,'Q')[0]
        assert target == oracle.functions[name], (chip,offset,target,name)
        oracle.word(ops+offset,target)
    oracle.uc.mem_write(sta+40,struct.pack('<I',1 if chip=='8852c' else 0))
    for macid in (0,1):
        operations.clear();oracle.uc.mem_write(sta+28,struct.pack('<H',macid))
        status=oracle.call('rtw_hal_update_cctrl_tbl',hal,sta)
        updates.append({'chip':chip,'MACID':macid,'status':status,'parameters':'legacy AP, no aggregation, default RTS/data retry limits255, port0/MBID0, no forced HE rate', 'operations':list(operations)})
aggregation=[]
for chip in ('8852c','8192xb'):
    oracle.word(adapter+88,oracle.symbols['mac'+chip+'_hw_info'][0])
    original_ops=oracle.symbols['mac'+chip+'_ops'][0]
    oracle.word(ops+280,oracle.unpack(original_ops+280,'Q')[0])
    oracle.uc.mem_write(sta+40,struct.pack('<I',chip=='8852c'))
    oracle.uc.mem_write(sta+28,struct.pack('<H',1))
    oracle.uc.mem_write(sta+2332,struct.pack('<I',8))  # WLAN_MD_11N
    for number in (4,16,64):
        oracle.uc.mem_write(sta+2506,struct.pack('<H',number))
        operations.clear()
        status=oracle.call('rtw_hal_update_cctrl_tbl',hal,sta)
        aggregation.append({'chip':chip,'MACID':1,'maximum_aggregation':number,'status':status,
                            'operations':list(operations)})
output=root/'manifests/radio-stock-station-tables.json'
output.write_text(json.dumps({'scope':__doc__,'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_original_functions':names,'records':records,'legacy_updates':updates,
    'HT_aggregation_updates':aggregation},indent=2)+'\n')
for r in records:
    print(r['chip'], r['tx_nss'], r['MACID'], r['status'], [o for o in r['operations'] if 'payload' in o])

for r in updates:
    print(r)
for r in aggregation:
    print(r)
