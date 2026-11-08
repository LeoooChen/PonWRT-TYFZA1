#!/usr/bin/env python3
"""Compare XB OP5K ADC sequences and tracking against the board ELF."""
import ctypes as C
import hashlib
import itertools
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest()=='745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
class State(C.Structure):
 _fields_=[('defaults',C.c_ubyte*2),('result',C.c_ubyte*2),('thermal',C.c_ubyte*2),('history',(C.c_ubyte*2)*2),('index',C.c_ubyte),('ready',C.c_bool),('adjusted',C.c_bool),('runs',C.c_uint)]
class Bus:
 def __init__(self,zeros,backup=(8,8)):
  self.zeros=zeros;self.thermal=[20,21];self.trace=[];self.reg={0xc0d8:backup[0]<<5,0xc1d8:backup[1]<<5}
 def read(self,r):
  self.trace.append(('read',r))
  if r in (0xc0fc,0xc1fc):
   p=(r>>8)&1;code=(self.reg[0xc0d8+p*256]>>5)&15
   return (0 if code in self.zeros[p] else 7)<<20
  return self.reg.get(r,0)
 def write(self,r,m,v):
  self.trace.append(('write',r,m,v));shift=(m&-m).bit_length()-1
  self.reg[r]=(self.reg.get(r,0)&~m)|((v<<shift)&m)
 def meter(self,p):self.trace.append(('thermal',p));return self.thermal[p]
def cb(n,a):
 if n=='halrf_rreg':return (stock.read(a[1])&a[2])>>((a[2]&-a[2]).bit_length()-1)
 if n=='halrf_wreg':stock.write(*a[1:]);return 0
 if n=='halrf_get_thermal':return stock.meter(a[1])
 if n=='halrf_delay_us':stock.trace.append(('delay',a[1]));return 0
 if n=='_rtw_memset':o.uc.mem_write(a[0],bytes([a[1]])*a[2]);return a[0]
 if n=='printk':return 0
 raise AssertionError((n,a))
o=ModuleOracle(module,['halrf_op5k_init_8192xb','halrf_op5k_trigger_8192xb','halrf_op5k_tracking_8192xb','halrf_bw_setting_op5k_8192xb'],cb)
RF,H=0x6000000,0x6100000;o.uc.mem_map(RF,0x110000)
R=C.CFUNCTYPE(C.c_uint,C.c_uint);W=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint);T=C.CFUNCTYPE(None,C.c_uint);M=C.CFUNCTYPE(C.c_uint,C.c_uint)
@R
def read(r):return native.read(r)
@W
def write(r,m,v):native.write(r,m,v)
@T
def delay(v):native.trace.append(('delay',v))
@M
def meter(p):return native.meter(p)
source=r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
typedef uint8_t u8;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define RTL_AX_AP_RF_RUNTIME_H
#include "power.h"
static u32(*rd)(u32);static void(*wr)(u32,u32,u32);static void(*dt)(u32);static u32(*mt)(u32);
static u32 read(void *ctx,u32 r){return rd(r&65535);}
static void delay(void *ctx,u32 us){dt(us);}
static void rtl_ax_channel_field(const struct rtl_ax_io *io,u32 r,u32 m,u32 v){wr(r,m,v);}
static int rtl_ax_rf_thermal(const struct rtl_ax_io *io,u32 p,u8 *v){*v=mt(p);return 0;}
#include "xb-op5k.h"
void bind(void *r,void *w,void *d,void *m){rd=r;wr=w;dt=d;mt=m;}
int run(struct rtl_ax_xb_op5k *s,int action,int cv){struct rtl_ax_io io={.read32=read,.delay_us=delay};
 if(action==0)return rtl_ax_xb_op5k_run(&io,s);
 if(action==1)return rtl_ax_xb_op5k_track(&io,s);
 rtl_ax_xb_op5k_channel(&io,s,cv);return 0;}
"""
with tempfile.TemporaryDirectory(prefix='tyfza1-op5k-') as tmp:
 c,so=Path(tmp)/'test.c',Path(tmp)/'test.so';c.write_text(source)
 subprocess.run(['cc','-O2','-Wall','-Werror','-shared','-fPIC','-I',str(driver),str(c),'-o',str(so)],check=True)
 lib=C.CDLL(str(so));lib.bind.argtypes=[R,W,T,M];lib.bind(read,write,delay,meter);lib.run.argtypes=[C.POINTER(State),C.c_int,C.c_int]
 def init(zeros,backup=(8,8)):
  global stock,native
  o.uc.mem_write(RF,bytes(0x110000));o.word(RF+8,H);o.uc.mem_write(H+4,struct.pack('<I',1));o.uc.mem_write(RF+0x24,struct.pack('<I',0x38e17f))
  stock=Bus(zeros,backup);native=Bus(zeros,backup)
  o.call('halrf_op5k_init_8192xb',RF);s=State();s.thermal[:]=[26,26];return s
 def check(s,label):
  if stock.trace!=native.trace:
   i=next(i for i,(a,b) in enumerate(zip(stock.trace,native.trace)) if a!=b)
   raise AssertionError((label,i,stock.trace[i:i+8],native.trace[i:i+8],len(stock.trace),len(native.trace)))
  assert stock.reg==native.reg,label
  assert bytes(s.thermal)==bytes(o.uc.mem_read(RF+0xab5fc,2)),label
  result=o.unpack(RF+0xab5c8,'2I');assert list(s.result)==list(result),label
 rows=[]
 patterns=[(range(4,12),range(5,10)),([],[]),(range(0,4),range(12,16)),([1,2,3,7,8,9],[2,3]),(range(16),range(16))]
 for zeros,backup in itertools.product(patterns,[(8,8),(0,15)]):
  s=init(zeros,backup);o.call('halrf_op5k_trigger_8192xb',RF,instruction_budget=200000)
  assert lib.run(C.byref(s),0,1)==0;check(s,('run',zeros,backup));rows.append(len(stock.trace))
 s=init((range(4,12),range(5,10)))
 o.call('halrf_bw_setting_op5k_8192xb',RF);lib.run(C.byref(s),2,1);check(s,'channel')
 for thermal in ((26,26),(21,21),(19,19),(17,18),(26,27),(32,31),(20,22),(12,15)):
  stock.trace=[];native.trace=[];stock.thermal=list(thermal);native.thermal=list(thermal)
  o.call('halrf_op5k_tracking_8192xb',RF,instruction_budget=300000);assert lib.run(C.byref(s),1,1)==0
  check(s,('track',thermal));assert s.adjusted==bool(o.unpack(RF+0xab60a,'B')[0]);rows.append(len(stock.trace))
 # CV0 bandwidth setup and an unconfigured tracker perform no IO.
 s=init((range(4,12),range(5,10)));o.uc.mem_write(H+4,struct.pack('<I',0))
 o.call('halrf_bw_setting_op5k_8192xb',RF);lib.run(C.byref(s),2,0)
 o.call('halrf_op5k_tracking_8192xb',RF);lib.run(C.byref(s),1,0)
 assert stock.trace==native.trace==[]
 print(json.dumps(dict(cases=len(rows)+2,matched_operations=rows,cv0_and_unconfigured_no_io=True)))
