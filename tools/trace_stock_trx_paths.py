#!/usr/bin/env python3
"""Trace TYFZA1's actual rtw_hal_start -> 2T2R BB/PHY-user initialization."""
from pathlib import Path
import hashlib,json,struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4
root=Path(__file__).resolve().parents[1]
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names=['halbb_ctrl_trx_path','halbb_ctrl_btg_8852c','halbb_trxpath_notif','halbb_path_num','halbb_trxpath_upd','halbb_bbinfo_cfg','halbb_ch_bw_upd','halbb_ch_bw_notif']
for c in ('8852c','8192xb'):
 names += ['halbb_ctrl_trx_path_'+c,'halbb_ctrl_rx_path_'+c,'halbb_ctrl_tx_path_tmac_'+c]
writes=[]
def cb(n,a):
 if n in ('printk','dbg_rtw_mfree'):return 0
 if n=='_rtw_memset':
  o.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
 if n=='dbg_rtw_zmalloc':
  o.uc.mem_write(0x6060000,bytes(64));return 0x6060000
 if n=='halbb_fill_h2c_cmd':
  p=o.uc.reg_read(UC_ARM64_REG_X4)
  writes.append({'space':'H2C','bytes':a[1],'function':a[2],'class':a[3],'payload':bytes(o.uc.mem_read(p,a[1])).hex()});return 1
 if n in ('halbb_set_reg','halbb_set_reg_cmn'):
  writes.append({'space':'BB','address':a[1],'mask':a[2],'value':a[3]});return 0
 if n=='rtw_hal_mac_set_pwr_reg':
  writes.append({'space':'MAC','address':a[2],'mask':0xffffffff,'value':a[3]});return 0
 raise RuntimeError((n,a))
o=ModuleOracle(module,names,cb)
bb,phl,hal=0x6000000,0x6010000,0x6020000
for a in (bb,phl,hal,0x6030000,0x6060000,0x6070000):o.uc.mem_map(a,0x10000)
o.word(bb,phl);o.word(bb+8,hal);o.word(hal+1896,0x6030000)
o.uc.mem_write(phl+0x58fa,b'\x36')
records=[]
for chip,ic in [('8852c',8),('8192xb',32)]:
 o.uc.mem_write(bb+3092,struct.pack('<I',ic))
 o.uc.mem_write(hal+892,struct.pack('<I',chip=='8852c'))
 writes.clear();o.call('halbb_ctrl_trx_path',bb,4,2,4,2)
 o.uc.mem_write(0x6070000,bytes([0,int(chip=='8852c'),36 if chip=='8852c' else 6,36 if chip=='8852c' else 6])+bytes(4))
 o.call('halbb_ch_bw_notif',bb,0x6070000)
 records.append({'chip':chip,'TX_path':4,'TX_NSS':2,'RX_path':4,'RX_NSS':2,'writes':list(writes)})
(root/'manifests/radio-stock-TRX-paths.json').write_text(json.dumps({'scope':__doc__,
 'original_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'executed_functions':names,
 'start_callsite':'rtw_hal_start@1c55ec..1c5618','inputs':'RFE54,DBCC0,normal mode,capabilities2T2R',
 'records':records},indent=2)+'\n')
for r in records:print(r['chip'],len(r['writes']),r['writes'][-14:])
