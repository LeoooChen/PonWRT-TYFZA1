#!/usr/bin/env python3
"""Derive native TPU byte/register mapping by executing original C/XB ELF.

The power-query boundary supplies distinct tagged half-dBm values. Original
TPU constructors, bit masks and MAC register writers execute unchanged. This
records the mapping; production power is read from the actual board files.
"""
import hashlib
import argparse
import json
import struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--width',type=int,choices=(20,40,80),default=20)
parser.add_argument('--output',type=Path,default=root/'manifests/radio-stock-power-registers.json')
args=parser.parse_args()
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
chips=('8852c',) if args.width==80 else ('8852c','8192xb')
names=['halrf_set_power_by_rate_to_struct_'+c for c in chips]
names += [f'halrf_get_power_limit_to_struct_{args.width}m_'+c for c in chips]
names += [f'halrf_get_power_limit_ru_to_struct_{args.width}m_'+c for c in chips]
names += ['halrf_modify_pwr_table_bitmask','mac_write_pwr_by_rate_reg','mac_write_pwr_ofst_mode',
    'mac_write_pwr_limit_reg','mac_write_pwr_limit_rua_reg','check_mac_en','halrf_hw_rate_to_pwr_by_rate','halrf_get_dcm_offset_pwr_by_rate']
rf,hal,adapter,ops,io_ops,platform=[0x6000000+i*0x10000 for i in range(6)]
queries=[]; writes=[]
def callback(name,args):
    if name in ('printk',): return 0
    if name == '_rtw_memset':
        oracle.uc.mem_write(args[0],bytes([args[1]&255])*args[2]); return args[0]
    if name in ('halrf_get_power_by_rate','halrf_get_power_limit','halrf_get_power_limit_ru'):
        extra=[oracle.uc.reg_read(r) for r in (UC_ARM64_REG_X4,UC_ARM64_REG_X5,UC_ARM64_REG_X6,UC_ARM64_REG_X7)]
        tag=len(queries)+1
        queries.append({'tag':tag,'api':name,'arguments':args[1:]+extra})
        return tag*2
    if name=='write32':
        writes.append({'address':hex(args[1]),'value':hex(args[2]&0xffffffff),'byte_tags':list(struct.pack('<I',args[2]&0xffffffff))}); return 0
    if name=='read32': return 0
    raise RuntimeError((name,args))
oracle=ModuleOracle(module,names,callback)
for a in (rf,hal,adapter,ops,io_ops,platform): oracle.uc.mem_map(a,0x10000)
oracle.word(rf+8,hal)
oracle.word(adapter,ops); oracle.word(ops,io_ops); oracle.word(adapter+24,platform)
oracle.word(io_ops+32,oracle.callback('read32')); oracle.word(io_ops+40,oracle.callback('write32'))
oracle.uc.mem_write(adapter+32,b'\x01\x04');oracle.uc.mem_write(adapter+67,b'\x01\x01\x00')
oracle.uc.mem_write(hal+905,b'\x24')
records=[]
for chip in chips:
    oracle.word(adapter+88,oracle.symbols['mac'+chip+'_hw_info'][0])
    center=(36 if args.width==20 else 38 if args.width==40 else 42) if chip=='8852c' else 6
    oracle.uc.mem_write(hal+904,bytes([center if args.width==20 else center-args.width//10+2,center]))
    for kind in ('rate','limit','ru'):
        queries.clear();writes.clear();oracle.uc.mem_write(hal+920,bytes(180))
        function=({'rate':'halrf_set_power_by_rate_to_struct_', 'limit':f'halrf_get_power_limit_to_struct_{args.width}m_', 'ru':f'halrf_get_power_limit_ru_to_struct_{args.width}m_'}[kind])+chip
        oracle.call(function,rf,0)
        oracle.call('halrf_modify_pwr_table_bitmask',rf,0,1 if kind=='rate' else 2 if kind=='limit' else 4)
        if kind=='rate':
            oracle.call('mac_write_pwr_by_rate_reg',adapter,0,hal+948)
            oracle.call('mac_write_pwr_ofst_mode',adapter,0,hal+920)
        elif kind=='limit': oracle.call('mac_write_pwr_limit_reg',adapter,0,hal+992)
        else: oracle.call('mac_write_pwr_limit_rua_reg',adapter,0,hal+920)
        for q in queries:
            if kind=='rate':
                query_args=q['arguments'];q['native_input_index']=oracle.call('halrf_get_dcm_offset_pwr_by_rate',rf,query_args[2],query_args[3],query_args[4])
        records.append({'chip':chip,'kind':kind,'queries':list(queries),'writes':list(writes)})
args.output.write_text(json.dumps({'scope':__doc__,'width_MHz':args.width,'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'executed_functions':names,'records':records},indent=2)+'\n')
for r in records:
    print(r['chip'],r['kind'],json.dumps(r['queries']),json.dumps(r['writes']))
