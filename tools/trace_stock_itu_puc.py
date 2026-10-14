import sys,struct,json
from pathlib import Path
p=Path(__file__).resolve().parents[1];sys.path.insert(0,str(p/'tools'))
from arm64_module_oracle import ModuleOracle
from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
m=p/'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
with m.open('rb') as f:
 e=ELFFile(f); names=[s.name for s in e.get_section_by_name('.symtab').iter_symbols() if s['st_info']['type']=='STT_FUNC' and s.name.startswith('aal_puc_') and s.name!='aal_puc_pon_qm_buf_fine_tune_set']
for mode in (3,4,5):
 calls=[];writes=[];tables={};registers={}
 def cb(n,a):
  if n in ('kthread_create_on_node','wake_up_process'):calls.append((n,a));return 1
  if n=='aal_puc_pon_qm_buf_fine_tune_set':calls.append((n,a));return 0
  if n=='aal_port_physical_port_to_dq_get':o.uc.mem_write(a[2],struct.pack('<I',1));return 0
  if n=='aal_pon_mac_mode_get':return mode
  if n=='memset':o.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
  if n=='memcpy':o.uc.mem_write(a[0],bytes(o.uc.mem_read(a[1],a[2])));return a[0]
  if n=='scfg_read':
   key=bytes(o.uc.mem_read(a[1],128)).split(b'\0',1)[0].decode()
   if key in ('627','628','629'):
    o.uc.mem_write(a[3],struct.pack('<I',{'627':0x7a0,'628':0x3b0,'629':0x200}[key]))
   calls.append((key,a[2]));return 0
  if n=='ca_device_config_tlv_get':return 0xffffffff
  if n in ('ca_spin_lock_init','ca_spin_lock','ca_spin_unlock','ca_printf','printk','ca_udelay','ca_mdelay'):return 0
  from unicorn.arm64_const import UC_ARM64_REG_LR
  lr=o.uc.reg_read(UC_ARM64_REG_LR)
  print('caller',[(n,hex(lr-b)) for n,(b,z) in o.symbols.items() if b<=lr<b+z])
  raise RuntimeError((n,a))
 o=ModuleOracle(m,names,cb)
 for n,b in [('ca_venus_pon_reg_iobase',0x6000000),('ca_venus_pon_glb_reg_iobase',0x6100000),('g_ne_reg_iobase',0x6200000)]:o.uc.mem_map(b,0x10000);o.word(o.symbols[n][0],b)
 for n,v in [('aal_pon_mac_mode',mode),('puc_voq_map_mode',0)]:o.uc.mem_write(o.symbols[n][0],struct.pack('<I',v))
 for off,v in [(0x8088,1),(0x8148,0x80000000),(0x8050,0x41000114)]:o.uc.mem_write(0x6000000+off,struct.pack('<I',v))
 indirect={0x8000:(0x8014,0x8010,0x800c,0x8008,0x8004),0x80e8:(0x80ec,),0x81c4:(0x81cc,0x81c8),0x81ec:(0x81f4,0x81f0)}
 def wh(uc,acc,a,s,v,d):
  writes.append((a,v));off=a-0x6000000
  if off in indirect and v&0x80000000:
   key=(off,v&255);regs=indirect[off]
   if v&0x40000000:tables[key]=[struct.unpack('<I',uc.mem_read(0x6000000+r,4))[0] for r in regs]
   else:
    for r,val in zip(regs,tables.get(key,[0]*len(regs))):uc.mem_write(0x6000000+r,struct.pack('<I',val))
 def rh(uc,acc,a,s,v,d):
  if a-0x6000000 in indirect:
   val=struct.unpack('<I',uc.mem_read(a,4))[0];uc.mem_write(a,struct.pack('<I',val&0x7fffffff))
 o.uc.hook_add(UC_HOOK_MEM_WRITE,wh,begin=0x6000000,end=0x620ffff);o.uc.hook_add(UC_HOOK_MEM_READ,rh,begin=0x6000000,end=0x600ffff)
 try:r=o.call('aal_puc_init',0,mode,instruction_budget=6000000)
 except Exception:
  print('last writes',[(hex(a),hex(v)) for a,v in writes[-12:]],'calls',calls[-12:]);raise
 assert r == 0
 print('mode',mode,'ret',hex(r),'calls',calls[:5],'PVT',[(i,[hex(x) for x in tables.get((0x8000,i),[])]) for i in (0,1,15,31)],'BTC',hex(struct.unpack('<I',o.uc.mem_read(0x6008050,4))[0]))
 out={'mode':mode,'return':r,'SCFG_inputs_from_original_default_scfg':True,'dynamic_QM_tuning_and_kthread_stubbed':True,'port_to_DQ_mapping_modelled':True,'hardware_test':False,'writes':writes,'tables':{f'{a:04x}:{b}':v for (a,b),v in tables.items()},'dependency_calls':calls}
 (p/f'logs/itu50-stock-puc-mode{mode}.json').write_text(json.dumps(out,indent=2)+'\n')

import ctypes,subprocess,tempfile
source=r'''
#include "rtl9617c-pon-itu-puc.h"
int configure(const struct rtl_dq_io *io, unsigned int mode) {
 int ret = rtl_itu_puc_queues(io);
 if (!ret) {
  if (mode == 3) rtl_gpon_puc_profile(io);
  else rtl_xgpon_puc_profile(io);
 }
 return ret;
}
'''
read_fn=ctypes.CFUNCTYPE(ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint)
write_fn=ctypes.CFUNCTYPE(None,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint)
ind_fn=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint)
class IO(ctypes.Structure):
 _fields_=[('context',ctypes.c_void_p),('read',read_fn),('write',write_fn),('indirect',ind_fn)]
mem={};native_tables={}
@read_fn
def read(c,a):return mem.get(a,0)
@write_fn
def write(c,a,v):mem[a]=v
@ind_fn
def access(c,a,v):
 regs={0x8000:(0x8014,0x8010,0x800c,0x8008,0x8004),0x80e8:(0x80ec,)}[a]
 key=f'{a:04x}:{v&255}'
 if v&0x40000000:native_tables[key]=[mem.get(r,0) for r in regs]
 else:
  for r,val in zip(regs,native_tables.get(key,[0]*len(regs))):mem[r]=val
 return 0
with tempfile.TemporaryDirectory(prefix='itu-puc-',dir=p/'artifacts') as work:
 f=Path(work)/'puc.c';lib=Path(work)/'puc.so';f.write_text(source)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(p/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'),str(f),'-o',str(lib)],check=True)
 native=ctypes.CDLL(str(lib));native.configure.argtypes=[ctypes.POINTER(IO),ctypes.c_uint]
 io=IO(None,read,write,access)
 comparisons=[]
 for mode in (3,4,5):
  stock=json.loads((p/f'logs/itu50-stock-puc-mode{mode}.json').read_text())
  mem.clear();native_tables.clear();mem[0x8050]=0x41000114
  assert native.configure(ctypes.byref(io),mode)==0
  for index in range(32):assert native_tables[f'8000:{index}']==stock['tables'][f'8000:{index}'],index
  for index in range(256):assert native_tables[f'80e8:{index}']==stock['tables'][f'80e8:{index}'],index
  stock_regs={a-0x6000000:v for a,v in stock['writes']}
  for reg in (0x8050,0x804c,0x8160,0x8164,0x8168,0x8230,0x8234):assert mem[reg]==stock_regs[reg],(mode,hex(reg),hex(mem[reg]),hex(stock_regs[reg]))
  for index in range(8):assert mem[0x81bc-index*4]==stock_regs[0x81bc-index*4]
  comparisons.append({'mode':mode,'32_TCONT_maps_match':True,'256_backpressure_maps_match':True,'OMCI_and_fragmentation_registers_match':True})
 (p/'logs/itu50-native-puc-contract.json').write_text(json.dumps({'hardware_test':False,'comparisons':comparisons},indent=2)+'\n')
 print('Native GPON/XG-PON/XGS-PON PUC: T-CONT, OMCI, fragmentation and backpressure match original machine code')
