#!/usr/bin/env python3
"""Check NHM statistics against actual C/XB factory utility functions."""
from pathlib import Path
import ctypes as C
import hashlib
import itertools
import json
import random
import struct
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
module=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest()=='745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
def cb(n,a):
 if n=='halbb_nhm_cal_wgt_avg':return 0
 if n=='halbb_get_reg':
  r,m=a[1:3]
  if r==0x1aa4:v=0x10000|59000
  elif r==0x1aa0:v=200<<16|100
  else:
   i=(r-0x1a88)//2;v=bins[i]|bins[i+1]<<16
  return (v&m)>>((m&-m).bit_length()-1)
 raise AssertionError((n,a))
o=ModuleOracle(module,['halbb_cr_cfg_env_mntr_init','halbb_nhm_get_result','halbb_nhm_get_utility','halbb_nhm_cal_wgt','halbb_nhm_exclu_noise_figure','halbb_ccx_get_ratio'],cb)
BB,H=0x6000000,0x6010000;o.uc.mem_map(BB,0x20000)
source=r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
typedef uint8_t u8; typedef int8_t s8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "environment.h"
static const u16 *bins;
static u32 read32(void *ctx,unsigned int r) {
 if(r==0x11aa4)return 0x10000|59000;
 if(r==0x11aa0)return 200U<<16|100;
 if(r>=0x11a88 && r<0x11aa0) {unsigned int i=(r-0x11a88)/2;return bins[i]|(u32)bins[i+1]<<16;}
 return 0;
}
static void write32(void *ctx,unsigned int r,u32 v) {}
u32 run(unsigned int chip,unsigned int bw,int loss,const u16 *input) {
 struct rtl_ax_io io={.read32=read32,.write32=write32};struct rtl_ax_environment state={0};
 bins=input;rtl_ax_environment_initialize(&io,chip,&state);
 rtl_ax_environment_trigger(&io,&state,loss);
 rtl_ax_environment_sample(&io,&state,bw);
 return state.nhm_noise_ratio | state.nhm_cca_ratio<<8 | state.nhm_idle_ratio<<16;
}
"""
rng=random.Random(52);cases=[]
for chip,bw,loss,peak in itertools.product((0x52,0x53),(0,1,2,3),(-12,0,10,24),range(12)):
 bins=[0]*12;bins[peak]=59375;cases.append((chip,bw,loss,bins))
for i in range(120):
 bins=[rng.randrange(5000) for _ in range(12)];cases.append((0x52 if i&1 else 0x53,i%4,(i%4)*8,bins))
with tempfile.TemporaryDirectory(prefix='tyfza1-nhm-') as tmp:
 c,so=Path(tmp)/'test.c',Path(tmp)/'test.so';c.write_text(source)
 subprocess.run(['cc','-O2','-Wall','-Werror','-shared','-fPIC','-I',str(driver),str(c),'-o',str(so)],check=True)
 lib=C.CDLL(str(so));lib.run.argtypes=[C.c_uint,C.c_uint,C.c_int,C.POINTER(C.c_ushort)];lib.run.restype=C.c_uint
 mismatches=[]
 for chip,bw,loss,bins in cases:
  o.uc.mem_write(BB,bytes(0x20000));o.word(BB+8,H)
  o.uc.mem_write(BB+0xc14,struct.pack('<I',8 if chip==0x52 else 32))
  o.uc.mem_write(BB+0xc1c,struct.pack('<I',2));o.call('halbb_cr_cfg_env_mntr_init',BB)
  o.uc.mem_write(H+0x380,struct.pack('<I',bw))
  thresholds=[v*2 for v in (18,21,24,27,30,35,40,45,50,55,60)]
  if chip==0x52:thresholds[-1]=218
  thresholds=[(v-(0 if chip==0x52 and i==10 else loss))&255 for i,v in enumerate(thresholds)]
  o.uc.mem_write(BB+0x2208,bytes(thresholds));o.uc.mem_write(BB+0x21f4,struct.pack('<H',59375))
  o.uc.mem_write(BB+0x2238,struct.pack('<12H',*bins));o.uc.mem_write(BB+0x22ce,struct.pack('<H',sum(bins)&65535))
  o.uc.mem_write(BB+0x2250,struct.pack('<3H',200,100,59000))
  assert o.call('halbb_nhm_get_result',BB)==1
  expected=bytes(o.uc.mem_read(BB+0x22ca,4))
  actual=lib.run(chip,bw,loss,(C.c_ushort*12)(*bins))
  want=expected[3]|expected[1]<<8|expected[2]<<16
  if actual!=want:mismatches.append(dict(chip=chip,bw=bw,loss=loss,bins=bins,actual=hex(actual),expected=hex(want)))
 print(json.dumps(dict(cases=len(cases),mismatches=len(mismatches),examples=mismatches[:8]),indent=2))
 assert not mismatches
