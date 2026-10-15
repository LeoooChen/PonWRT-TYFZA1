#!/usr/bin/env python3
"""Execute original C channel/BW and RXBB control with tagged RF18 inputs."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4
root=Path(__file__).resolve().parents[1]
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names=['halrf_ctrl_bw_ch','halrf_ctl_ch','halrf_ctl_bw','halrf_ctl_band_ch_bw','halrf_rxbb_bw',
       'halrf_ctl_band_ch_bw_8852c','halrf_kpath_8852c','halrf_rxbb_bw_8852c',
       'halrf_set_rxbb_bw_8852c','halrf_write_fwofld_start','halrf_write_fwofld_end']
rf,hal=0x6000000,0x6200000
operations=[];registers={};ticks=0
def callback(name,args):
    global ticks
    if name=='printk':return 0
    if name=='_rtw_get_current_time':ticks+=1;return ticks
    if name=='_rtw_systime_to_us':return args[0]
    if name=='halrf_delay_us':operations.append({'delay_us':args[1]});return 0
    if name=='rtw_hal_read_rf_reg':
        path,address,mask=args[1:4]
        return (registers.get((path,address),0)&mask)>>((mask&-mask).bit_length()-1)
    if name=='halrf_wrf':
        path,address,mask=args[1:4];value=oracle.uc.reg_read(UC_ARM64_REG_X4)
        registers[(path,address)]=(registers.get((path,address),0)&~mask)|(value<<((mask&-mask).bit_length()-1)&mask)
        operations.append({'path':path,'address':hex(address),'mask':hex(mask),'value':hex(value)});return 0
    raise RuntimeError((name,args))
oracle=ModuleOracle(module,names,callback)
oracle.uc.mem_map(rf,0x100000);oracle.uc.mem_map(hal,0x10000)
records=[]
for width,bw,center in [(20,0,36),(40,1,38),(80,2,42),(160,3,50)]:
    oracle.uc.mem_write(rf,bytes(0x100000))
    oracle.word(rf+8,hal);oracle.uc.mem_write(rf+16,struct.pack('<I',8))
    oracle.uc.mem_write(rf+68,b'\xff\xff')  # cached RXBB initially unset
    oracle.uc.mem_write(hal+4,struct.pack('<I',1))  # actual board CV1
    oracle.uc.mem_write(hal+1580,b'\x00')  # DBCC disabled: both RF paths
    operations.clear();registers.clear()
    initial={(0,0x18):0xe5a5a,(0,0x10018):0xc5a5a,(1,0x18):0xa5a5a,(1,0x10018):0x85a5a}
    registers.update(initial)
    oracle.call('halrf_ctrl_bw_ch',rf,0,center,1,bw)
    records.append({'chip':'8852c','CV':1,'PHY':0,'DBCC':0,'width_MHz':width,'center':center,
                    'initial_RF18':{f'{p}:{hex(a)}':hex(v) for (p,a),v in initial.items()},
                    'operations':list(operations),
                    'final_RF18':{f'{p}:{hex(a)}':hex(registers[(p,a)]) for p,a in initial}})
(root/'manifests/radio-stock-RF-bandwidth.json').write_text(json.dumps({
    'scope':__doc__,'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
    'executed_functions':names,'records':records},indent=2)+'\n')
for r in records:print(r['width_MHz'],r['final_RF18'],r['operations'])
