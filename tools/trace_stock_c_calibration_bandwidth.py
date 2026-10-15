#!/usr/bin/env python3
"""Execute original C IQK/RX-clock/ADC branches for 20/40/80/160MHz."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4
root=Path(__file__).resolve().parents[1]
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names=['_iqk_rxk_setting_8852c','_iqk_one_shot_8852c','_iqk_check_cal_8852c',
       'halrf_rxck_force_8852c','rtw_hal_bb_adc_cfg','halbb_adc_cfg','halbb_adc_cfg_8852c',
       'iqk_start_iqk_8852c','_lok_finetune_check_8852c','_rxk_group_sel_8852c',
       'halrf_write_fwofld_start','halrf_write_fwofld_end']
rf,common,hal,bb,phl=0x6000000,0x6200000,0x6210000,0x6220000,0x6230000
writes=[]
def callback(name,args):
    if name in ('printk','halrf_write_fwofld_end'):return 0
    if name=='halrf_get_thermal_8852c':return 32
    if name=='rtw_hal_read_rf_reg':
        return 24<<14 | 24<<8 if args[2]==0x5c else 0
    if name in ('halrf_wreg','halbb_set_reg','halbb_set_reg_cmn'):
        writes.append({'space':'BB','address':hex(args[1]),'mask':hex(args[2]),'value':hex(args[3])});return 0
    if name=='halrf_wrf':
        writes.append({'space':'RF','path':args[1],'address':hex(args[2]),'mask':hex(args[3]),
                       'value':hex(oracle.uc.reg_read(UC_ARM64_REG_X4))});return 0
    if name=='halrf_delay_us':
        writes.append({'space':'delay_us','value':args[1]});return 0
    if name=='halrf_rreg':
        return 0x55 if args[1]==0xbff8 else 0x8000 if args[1]==0x80fc else 0
    raise RuntimeError((name,args))
oracle=ModuleOracle(module,names,callback)
oracle.uc.mem_map(rf,0x100000)
for address in (common,hal,bb,phl):oracle.uc.mem_map(address,0x10000)
oracle.word(rf,phl);oracle.word(rf+8,common);oracle.word(common+1888,hal);oracle.word(hal+336,bb)
oracle.word(bb,phl);oracle.uc.mem_write(bb+3092,struct.pack('<I',8))
oracle.uc.mem_write(phl+0x58fa,b'\x36')
records=[]
for bandwidth in (0,1,2,3):
    for path in (0,1):
        oracle.uc.mem_write(rf+0x4968+60+path,bytes([bandwidth]))
        oracle.uc.mem_write(rf+0x4968+52+path,b'\x01')
        for function,args in [('_iqk_rxk_setting_8852c',(rf,path))] + [
                ('_iqk_one_shot_8852c',(rf,0,path,action)) for action in range(8)] + [
                ('iqk_start_iqk_8852c',(rf,0,path))]:
            oracle.uc.mem_write(rf+0x4968+52,b'\x01\x01')
            oracle.uc.mem_write(rf+0x4968+60,bytes([bandwidth,bandwidth]))
            oracle.uc.mem_write(rf+0x4968+220,struct.pack('<I',1))  # actual CV1 syn1to2
            writes.clear();status=oracle.call(function,*args,instruction_budget=1000000)
            records.append({'bandwidth':bandwidth,'width_MHz':20<<bandwidth,'path':path,
                            'function':function,'arguments':list(args[1:]),'status':status,'writes':list(writes)})
(root/'manifests/radio-stock-C-calibration-bandwidth.json').write_text(json.dumps({
    'scope':__doc__,'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions':names,'hardware_boundary':'Completion reads supply55/8000, thermal32, RF5c IDAC24/24; other RF reads0. Actual RF results are recorded separately from the board.',
    'records':records},indent=2)+'\n')
for row in records:
    print(row['width_MHz'],row['path'],row['function'],row['arguments'],
          [(w['address'],w['value']) for w in row['writes'] if w.get('address') in ('0x12a0','0x32a0','0x5670','0x7670','0x8000','0xc0d8','0xc1d8')])
