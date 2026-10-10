#!/usr/bin/env python3
"""Execute the original C-only RFK channel-history and FW notification path."""
import hashlib,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4,UC_ARM64_REG_X5
root=Path(__file__).resolve().parents[1]
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
records=[]
def callback(n,a):
 if n=='printk':return 0
 if n=='halrf_fill_h2c_cmd':
  kind=o.uc.reg_read(UC_ARM64_REG_X4);p=o.uc.reg_read(UC_ARM64_REG_X5)
  records.append({'length':a[1],'function':a[2],'class':a[3],'buffer':kind,
     'payload':bytes(o.uc.mem_read(p,a[1])).hex()});return 0
 raise RuntimeError((n,a))
names=['halrf_mcc_info_init','halrf_mcc_get_ch_info','halrf_fw_ntfy_8852c']
o=ModuleOracle(module,names,callback)
rf,hal=0x6000000,0x6200000;o.uc.mem_map(rf,0x100000);o.uc.mem_map(hal,0x10000)
o.word(rf+8,hal);o.uc.mem_write(rf+16,struct.pack('<I',8))
for ch in (36,36,44,40):
 o.uc.mem_write(hal+892,struct.pack('<I',1));o.uc.mem_write(hal+905,bytes([ch]))
 o.call('halrf_mcc_get_ch_info',rf,0);o.call('halrf_fw_ntfy_8852c',rf,0)
 records[-1]['channel']=ch
out=root/'manifests/radio-stock-RFK-notify.json'
out.write_text(json.dumps({'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'scope':__doc__,'executed_original_functions':names,'records':records},indent=2)+'\n')
print(json.dumps(records))
