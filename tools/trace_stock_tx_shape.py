#!/usr/bin/env python3
"""Trace the real TYFZA1 RFE54 transmit shape functions with explicit IO."""
import hashlib,json,struct
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4
root=Path(__file__).resolve().parents[1]
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names=['halbb_set_tx_pow_pattern_shap','halbb_tx_triangular_shap_cfg_8852c',
 'halbb_tx_dfir_shap_cck_8852c','halbb_tx_triangular_shap_cfg_8192xb',
 'halbb_tx_dfir_shap_cck_8192xb','halbb_tssi_ctrl_set_bandedge_cfg','halbb_tssi_ctrl_set_fast_mode_cfg']
ops=[]
def cb(n,a):
 if n=='printk':return 0
 if n=='rtw_hal_mac_write_msk_pwr_reg':
  ops.append({'address':a[2],'mask':a[3],'value':o.uc.reg_read(UC_ARM64_REG_X4),'space':'MAC'});return 0
 if n in ('halbb_set_reg','halbb_set_reg_cmn'):
  ops.append({'address':a[1],'mask':a[2],'value':a[3]});return 0
 raise RuntimeError((n,a))
o=ModuleOracle(module,names,cb)
bb,phl,hal=0x6000000,0x6010000,0x6020000
for x in (bb,phl,hal):o.uc.mem_map(x,0x10000)
o.word(bb,phl);o.word(bb+8,hal);o.uc.mem_write(phl+0x58fa,b'\x36')
records=[]
for chip,ic,ch in [('8852c',8,36),('8192xb',32,6)]:
 o.uc.mem_write(bb+3092,struct.pack('<I',ic));o.uc.mem_write(hal+892,struct.pack('<I',ch>14))
 for shape in range(4):
  for modulation in ('OFDM','CCK'):
   ops.clear();o.uc.mem_write(hal+1123,bytes([shape]))
   o.call('halbb_set_tx_pow_pattern_shap',bb,ch,int(modulation=='OFDM'),0)
   records.append({'chip':chip,'channel':ch,'shape':shape,'modulation':modulation,'writes':list(ops)})
p=root/'manifests/radio-stock-TX-shape.json'
p.write_text(json.dumps({'scope':__doc__,'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
 'executed_functions':names,'records':records},indent=2)+'\n')
for r in records:print(r)
