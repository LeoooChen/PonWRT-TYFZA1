#!/usr/bin/env python3
"""Compare production C RXDCK register operations with this board's ARM64 ELF."""
import ctypes as C
import hashlib
import itertools
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest() == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'

class Bus:
    def __init__(self, automatic, rek, offset):
        self.reg = {(p,r):v for p in (0,1) for r,v in [(0,0x51234),(5,automatic),(0x95,rek*8),(0x42,(30+p)*2)]}
        self.trace, self.offset = [], offset
    def read(self,p,r):
        self.trace.append(('read',p,r))
        v = self.reg.get((p,r),0)
        if r in (0x92,0x93):
            gain = (self.reg[p,0] >> 10) & 31
            offset = 32 + (self.offset if gain in (12,13,28,29) else 0)
            v = (v & ~0xfe00) | (offset << 9)
            if r == 0x92: v = (v & ~0xe0) | 3 << 5
            if r == 0x93: v |= 32
        return v
    def write(self,p,r,m,v):
        self.trace.append(('write',p,r,m,v))
        shift = (m & -m).bit_length()-1
        self.reg[p,r] = (self.reg.get((p,r),0) & ~m) | ((v << shift) & m)
        return 0
    def bb(self,r,m,v): self.trace.append(('bb',r,m,v))
    def delay(self,v): self.trace.append(('delay',v))

def callback(name,args):
    if name == 'rtw_hal_read_rf_reg':
        _,p,r,m=args
        return (stock.read(p,r)&m) >> ((m & -m).bit_length()-1)
    if name == 'halrf_wrf': return stock.write(args[1],args[2],args[3],oracle.uc.reg_read(UC_ARM64_REG_X4))
    if name == 'halrf_wreg': stock.bb(*args[1:]); return 0
    if name == 'halrf_delay_us': stock.delay(args[1]); return 0
    if name == 'halrf_thermal_rx_dck_trigger': triggers.append(args[1:3]); return 0
    if name == 'printk': return 0
    raise AssertionError((name,args))

names = ['halrf_rx_dck_init','halrf_rx_dck_8852c','halrf_thermal_rx_dck_8852c','_rx_dck_rek_check_8852c','halrf_kpath_8852c',
         'halrf_set_rx_dck_8852c','halrf_rx_dck_toggle_8852c','halrf_get_thermal_8852c',
         '_rx_dck_info_8852c','_rx_dck_read_offset_8852c','_rx_dck_check_8852c',
         '_rx_dck_value_rewrite_8852c','halrf_rxdck_track_8852c']
oracle = ModuleOracle(module,names,callback)
rf, hal = 0x6000000,0x6100000
oracle.uc.mem_map(rf,0x110000)
R = C.CFUNCTYPE(C.c_int,C.c_uint,C.c_uint,C.POINTER(C.c_uint))
W = C.CFUNCTYPE(C.c_int,C.c_uint,C.c_uint,C.c_uint,C.c_uint)
B = C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint)
T = C.CFUNCTYPE(None,C.c_uint)
@R
def read(p,r,v): v[0]=native.read(p,r); return 0
@W
def write(p,r,m,v): return native.write(p,r,m,v)
@B
def bb(r,m,v): native.bb(r,m,v)
@T
def delay(v): native.delay(v)
source = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define RTL_AX_AP_CHANNEL_H
#include "power.h"
static int (*rd)(unsigned int,unsigned int,u32 *);
static int (*wr)(unsigned int,unsigned int,u32,u32);
static void (*bb)(unsigned int,u32,u32);
static void (*dt)(unsigned int);
static int rtl_ax_phy_rf_read(const struct rtl_ax_io *io,unsigned int p,unsigned int r,u32 *v) { return rd(p,r,v); }
static int rtl_ax_phy_rf_write_masked(const struct rtl_ax_io *io,unsigned int p,unsigned int r,u32 m,u32 v) { return wr(p,r,m,v); }
static void rtl_ax_channel_field(const struct rtl_ax_io *io,unsigned int r,u32 m,u32 v) { bb(r,m,v); }
static void delay(void *ctx,unsigned int us) { dt(us); }
#include "c-rxdck.h"
void bind(void *r,void *w,void *b,void *d) { rd=r;wr=w;bb=b;dt=d; }
int track(int ready,int band,int a,int b,int olda,int oldb) {
 struct rtl_ax_rxdck state={.ready=ready,.thermal={olda,oldb}};
 u8 thermal[]={a,b}; return rtl_ax_c_rxdck_track(&state,band,thermal);
}
int run(int tssi,int channel,int rek) {
 struct rtl_ax_io io={.delay_us=delay}; struct rtl_ax_rxdck state={.rek_enabled=rek};
 return rtl_ax_c_rxdck_start(&io,&state,tssi,channel);
}
"""
rows=[]
with tempfile.TemporaryDirectory(prefix='tyfza1-c-rxdck-') as tmp:
    c,so=Path(tmp)/'test.c',Path(tmp)/'test.so';c.write_text(source)
    subprocess.run(['cc','-O2','-Wall','-Werror','-shared','-fPIC','-I',str(driver),str(c),'-o',str(so)],check=True)
    lib=C.CDLL(str(so));lib.bind.argtypes=[R,W,B,T];lib.bind(read,write,bb,delay)
    for automatic,rek,tssi,channel,offset in itertools.product((0,1),(0,1),(0,1),(0,1,2),(0,9,10,11,12)):
        oracle.uc.mem_write(rf,bytes(0x110000));oracle.word(rf+8,hal)
        oracle.uc.mem_write(rf+0x10,struct.pack('<I',8))
        oracle.uc.mem_write(rf+0x3c,bytes([channel]));oracle.uc.mem_write(rf+0x46,bytes([tssi]*2))
        stock=Bus(automatic,rek,offset)
        oracle.call('halrf_rx_dck_init',rf)
        if channel == 2: stock.trace.clear()
        oracle.call('halrf_thermal_rx_dck_8852c' if channel == 2 else 'halrf_rx_dck_8852c',rf,0,0)
        native=Bus(automatic,rek,offset)
        assert lib.run(tssi,channel,rek)==0
        # Native exports two final RF result reads per path to existing sysfs.
        trace=list(native.trace)
        for p in (0,1):
            end=max(i for i,x in enumerate(trace) if x==('read',p,0x93))
            assert trace[end-1:end+1]==[('read',p,0x92),('read',p,0x93)]
            del trace[end-1:end+1]
        assert trace==stock.trace,(automatic,rek,tssi,channel,offset,trace,stock.trace)
        assert native.reg==stock.reg
        rows.append(dict(automatic=automatic,rek=rek,tssi=tssi,channel=channel,offset=offset,operations=len(trace)))
    tracked=0
    for ready,band,path,delta in itertools.product((0,1),(0,1,2),(0,1),range(-13,14)):
        oracle.uc.mem_write(rf,bytes(0x110000));oracle.word(rf+8,hal)
        oracle.uc.mem_write(rf+0x7a80,bytes([36 if ready else 0]));oracle.uc.mem_write(rf+0x7a8c,bytes([36 if ready else 0]))
        oracle.uc.mem_write(rf+0x7a78,struct.pack('<I',band))
        thermal=[30,30];thermal[path]+=delta
        oracle.uc.mem_write(rf+0x4b,bytes(thermal));oracle.uc.mem_write(rf+0x7a95,bytes([30,30]))
        triggers=[];oracle.call('halrf_rxdck_track_8852c',rf)
        assert bool(lib.track(ready,band,*thermal,30,30))==bool(triggers)
        if triggers: assert triggers==[[0,0]]
        tracked+=1
print(json.dumps({'tracking_cases':tracked,'cases':rows,'matched':len(rows),'scope':'SCC original RXDCK with normal/channel gate; modeled RF IO, instant completion, including offset rewrites.'},indent=2))
