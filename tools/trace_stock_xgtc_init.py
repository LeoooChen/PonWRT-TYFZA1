#!/usr/bin/env python3
"""Record XGTC block initialization from the TYFZA1 original module."""
import json, struct, re
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ
root=Path(__file__).resolve().parents[1]
stages=['aal_xgpon_xgpn_glb_ctrl_set','__xgpon_dsfp_init.constprop.0','__xgpon_ploam_init','__xgpon_bwmp_init','aal_xgpon_actp_init','__xgpon_usbg_init.constprop.0']
leaf=['aal_xgpon_dsfp_xgtc_ctrl_set','aal_xgpon_dsfp_rxmac_ctrl_get','aal_xgpon_dsfp_rxmac_ctrl_set',
 'aal_xgpon_aes_plmp_glb_ctrl_set','aal_xgpon_aes_plmp_subkey_ctrl_set','aal_ploam_init',
 'aal_ploam_vendor_id_cfg_set','aal_ploam_vssn_cfg_set','aal_ploam_rgst_id_cfg_set','aal_ploam_ds_ctrl_set',
 'aal_xgpon_bwmp_bst_mg_cfg_set','aal_xgpon_bwmp_rsp_tm_set','aal_xgpon_bwmp_seq_tm_cfg_set',
 'aal_xgpon_actp_ctrl_set','aal_xgpon_actp_toz_set','aal_xgpon_actp_to1_set','aal_xgpon_actp_to23_set',
 'aal_xgpon_actp_to4_set','aal_xgpon_actp_to5_set','aal_xgpon_actp_sync_hunt_set',
 'aal_xgpon_actp_status_get','aal_xgpon_actp_status_set',
 'aal_xgpon_usbg_glb_ctrl_set','aal_xgpon_usbg_ploamu_queue_cfg_set']
register_header=root/'evidence/rtl-workspace/downloads/reference/taurus-uboot-study-20261005/bootloader/u-boot-2022.10/board/realtek_10G_PON/common/ne/taurus/include/reg/rtl9617c_registers.h'
text=register_header.read_text()
addresses={n:int(v,16)&65535 for n,v in re.findall(r'^#define (XGPN_\w+) +0x(f550[0-9a-fA-F]+)$',text,re.M)}
defaults={addresses[n]:int(v,16) for n,v in re.findall(r'^#define (XGPN_\w+)_dft +0x([0-9a-fA-F]+)$',text,re.M) if n in addresses}
scfg=(root/'analysis/private/live-files/etc/default_scfg.txt').read_text()
bwmp_response=int(re.search(r'CFG_ID_PON_BWMP_RSP_TM_CFG\s*=\s*(0x[0-9a-fA-F]+)',scfg)[1],16)
records=[]
for mode in (4,5):
 writes=[];calls=[]
 def cb(n,a):
  if n=='memset':o.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  if n=='memcpy':o.uc.mem_write(a[0],bytes(o.uc.mem_read(a[1],a[2])));return a[0]
  if n in ('ca_spin_lock_init','ca_spin_lock','ca_spin_unlock','ca_printf','printk','ca_udelay','ca_mdelay'):return 0
  if n in ('ca_event_register','ca_circle_timer_add','aal_ploam_txbuf_ctrl_set','ca_list_create'):
   calls.append((n,a));return 0
  raise RuntimeError((n,a))
 o=ModuleOracle(root/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',stages+leaf,cb)
 for n,b in [('ca_venus_pon_reg_iobase',0x6000000),('ca_venus_pon_glb_reg_iobase',0x6100000)]:o.uc.mem_map(b,0x10000);o.word(o.symbols[n][0],b)
 o.uc.mem_write(o.symbols['aal_pon_mac_mode'][0],struct.pack('<I',mode))
 for off,value in defaults.items():o.uc.mem_write(0x6000000+off,struct.pack('<I',value))
 basic=o.symbols['g_xgpn_basic_info'][0]
 o.uc.mem_write(basic,struct.pack('<II',0x54455354,0x12345678)+bytes(range(36)))
 o.uc.mem_write(basic+46,bytes([mode]))
 o.uc.mem_write(basic+52,struct.pack('<4I',80000,1000,1000,1000))
 o.uc.mem_write(basic+72,struct.pack('<I',bwmp_response)+bytes([4]))
 o.uc.hook_add(UC_HOOK_MEM_WRITE,lambda uc,acc,a,s,v,d:writes.append((a-0x6000000,v)),begin=0x6000000,end=0x600ffff)
 for stage in stages:
  calls.clear();writes.clear()
  try:
   if stage=='aal_xgpon_xgpn_glb_ctrl_set':
    o.uc.mem_write(o.scratch,bytes([0,0,2,1 if mode==4 else 2,0,0,0]))
    result=o.call(stage,0,13,o.scratch)
   else:result=o.call(stage,0,mode,instruction_budget=1000000)
  except Exception:
   print(stage,'last writes',[(hex(a),hex(v)) for a,v in writes[-10:]]);raise
  assert result==0,(stage,result)
  print(mode,stage,[(hex(a),hex(v)) for a,v in writes],calls)
  records.append({'mode':mode,'function':stage,'writes':list(writes),'modelled_dependencies':list(calls)})
(root/'logs/itu51-xgtc-block-initialization.json').write_text(json.dumps({'hardware_test':False,'synthetic_identity':True,'timer_inputs_from_original_default_scfg':True,'ASIC_reset_defaults_modelled':True,'records':records},indent=2)+'\n')

import ctypes,subprocess,tempfile
source=r'''
#include "rtl9617c-xgpon-hw.h"
void configure(const struct rtl_dq_io *io, int symmetric, const unsigned char *serial, const unsigned char *regid) {
 rtl_xgpon_mac_parameters(io,symmetric);
 rtl_xgpon_identity(io,serial,regid);
}
'''
rf=ctypes.CFUNCTYPE(ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint)
wf=ctypes.CFUNCTYPE(None,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint)
af=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint)
class IO(ctypes.Structure):_fields_=[('context',ctypes.c_void_p),('read',rf),('write',wf),('indirect',af)]
mem={}
@rf
def read(c,a):return mem.get(a,0)
@wf
def write(c,a,v):mem[a]=v
@af
def access(c,a,v):return 0
with tempfile.TemporaryDirectory(prefix='xgtc-init-',dir=root/'artifacts') as work:
 c=Path(work)/'native.c';lib=Path(work)/'native.so';c.write_text(source)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'),str(c),'-o',str(lib)],check=True)
 native=ctypes.CDLL(str(lib));native.configure.argtypes=[ctypes.POINTER(IO),ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p]
 io=IO(None,read,write,access);comparisons=[]
 for mode in (4,5):
  mem.clear();mem.update(defaults)
  original=dict(defaults)
  for record in records:
   if record['mode']==mode:original.update(record['writes'])
  native.configure(ctypes.byref(io),mode==5,b'TEST\x12\x34\x56\x78',bytes(range(36)))
  registers=[0x4004,0x4108,0x4110,0x4e00,0x4e04,0x4a04,0x4a00,0x4a08,0x4a60,0x4808,0x4804,0x480c,0x4d00,0x4d08,0x4d0c,0x4d10,0x4d14,0x4d18,0x4d04,0x4d30,0x4d2c,0x4d28,0x4600,0x4628,0x4a10,0x4a14]+list(range(0x4a34,0x4a58,4))
  for reg in registers:assert mem[reg]==original[reg],(mode,hex(reg),hex(mem[reg]),hex(original[reg]))
  comparisons.append({'mode':mode,'identity_and_36byte_registration_match':True,'DSFP_PLOAM_BWMP_ACTP_USBG_registers_match':True,'register_count':len(registers)})
 # Exercise both configured fields, including a mode bit hidden by reset defaults.
 queue_seed=0x80000002
 o.uc.mem_write(0x6004628,struct.pack('<I',queue_seed))
 assert o.call('__xgpon_usbg_init.constprop.0',0)==0
 stock_queue=struct.unpack('<I',o.uc.mem_read(0x6004628,4))[0]
 mem.clear();mem.update(defaults);mem[0x4628]=queue_seed
 native.configure(ctypes.byref(io),1,b'TEST\x12\x34\x56\x78',bytes(range(36)))
 queue_record={'initial':queue_seed,'stock':stock_queue,'native':mem[0x4628],
  'scope':'Original USBG initializer and setters versus native MAC parameters; MMIO and locks modeled, no physical queue operation.'}
 (root/'logs/stock-xgpon-ploamu-queue.json').write_text(json.dumps(queue_record,indent=2)+'\n')
 print('PLOAMU queue initial=%08x stock=%08x native=%08x'%(queue_seed,stock_queue,mem[0x4628]),flush=True)
 assert mem[0x4628]==stock_queue==2
 (root/'logs/itu51-native-xgtc-init-contract.json').write_text(json.dumps({'hardware_test':False,'register_read_defaults_modelled':True,'comparisons':comparisons},indent=2)+'\n')
 print('Native XG-PON/XGS-PON identity and block initialization match board original functions')
