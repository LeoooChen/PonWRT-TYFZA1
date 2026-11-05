#!/usr/bin/env python3
"""Compare bandwidth PD writes with factory ARM64 and run the channel callback.

The production callback and DIG helper execute unchanged. Radio calibration,
TX/RX ownership and firmware commands are outside this check and are stubbed.
"""
import argparse
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pci-source', type=Path, default=driver / 'pci.c')
args = parser.parse_args()
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

text = args.pci_source.read_text()
start = text.index('static int rtl_ax_receive_configure(')
end = text.index('static int rtl_ax_receive_set_channel(', start)
callback_source = text[start:end]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef int8_t s8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "dig.h"
#define rtl_ax_scan_complete_frame(...) ((void)0)
#define BIT(n) (1U<<(n))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define pci_clear_master(p) ((void)(p))
#define pci_set_master(p) ((void)(p))
#define dev_err(...) ((void)0)
#define rtl_ax_tx_open(...) ((void)0)
#define rtl_ax_tx_pause(...) 0
#define rtl_ax_tx_dfs_block(...) ((void)0)
#define rtl_ax_rx_close(...) ((void)0)
#define rtl_ax_rx_open(...) ((void)0)
#define rtl_ax_hci_pause(...) ((void)0)
#define rtl_ax_h2c_start(...) ((void)0)
#define rtl_ax_dfs_stop_locked(...) 0
#define rtl_ax_dfs_start_locked(...) 0
#define rtl_ax_rx_dfs_epoch(...) ((void)0)
#define rtl_ax_power_apply(...) 0
#define rtl_ax_xb_rxdck_start(...) 0
#define rtl_ax_c_rxdck_start(...) 0
#define rtl_ax_gapk_channel(...) 0
#define rtl_ax_c_gapk_start(...) 0
#define rtl_ax_xb_iqk_start(...) 0
#define rtl_ax_c_iqk_start(...) 0
#define rtl_ax_xb_tssi_start(...) 0
#define rtl_ax_c_tssi_start20(...) 0
#define rtl_ax_xb_tssi_de(...) ((void)0)
#define rtl_ax_c_tssi_de20(...) ((void)0)
#define rtl_ax_bb_channel_notify_locked(...) 0
#define rtl_ax_rfk_notify_locked(...) 0
#define rtl_ax_channel_field(...) ((void)0)
#define rtl_ax_c_dig_bandwidth rtl_ax_dig_bandwidth
struct channel {bool configured;unsigned primary,center,bandwidth,band;};
struct rtl_ax_ap {
 struct rtl_ax_io io;struct rtl_ax_dig dig;struct channel channel;
 struct {int error;bool enabled;unsigned region;}dfs;
 struct {bool rek_enabled;}rxdck;
 struct {bool ready;}xb_tssi,c_tssi;
 int command_lock,pdev,rx,tx,phy,bb_dm;unsigned chip,rxdck_channel,dfs_domain;
 bool monitor_started,radar_required,dpk_bypassed;
};
static unsigned dfs_region;
static int set_channel(struct channel *ch,unsigned primary,unsigned center,unsigned bw){
 ch->configured=true;ch->primary=primary;ch->center=center;ch->bandwidth=bw;return 0;
}
#define rtl_ax_c_receive_channel(io,p,d,pri,ch,bw,out) set_channel(out,pri,ch,bw)
#define rtl_ax_xb_receive_channel(io,p,d,pri,ch,bw,out) set_channel(out,pri,ch,bw)
static u32 value;
static u32 read32(void *p,unsigned reg){return reg==0x1481c?value:0;}
static void write32(void *p,unsigned reg,u32 word){if(reg==0x1481c)value=word;}
static int poll32(void *p,unsigned r,unsigned mask,unsigned want,unsigned delay,unsigned timeout,unsigned *last){*last=want;return 0;}
'''
source += callback_source
source += r'''
u32 native_pd(unsigned bound,unsigned width,u32 initial){
 struct rtl_ax_io io={.read32=read32,.write32=write32};
 struct rtl_ax_dig dig={.pd_requested=bound};value=initial;
 rtl_ax_dig_bandwidth(&io,&dig,width);assert(dig.bandwidth==width);return value;
}
void channel_lifecycle(void){
 for(unsigned chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++){
  struct rtl_ax_ap radio={.chip=chip,.dig={.pd_requested=93},
   .io={.read32=read32,.write32=write32,.poll32=poll32}};
  unsigned first=chip==RTL_AX_8852C?36:1;
  value=0x20000040;
  assert(!rtl_ax_receive_configure(&radio,first,first,0));
  assert(radio.dig.bandwidth==0&&(value&BIT(29)));
  assert(!rtl_ax_receive_configure(&radio,first,first+2,1));
  assert(radio.dig.bandwidth==1&&!(value&BIT(29)));
  assert(!rtl_ax_receive_configure(&radio,first,first,0));
  assert(radio.dig.bandwidth==0&&(value&BIT(29)));
 }
}
'''
registers = {}


def boundary(name, values):
    if name == 'printk':
        return 0
    assert name == 'halbb_set_reg_cmn', (name, values)
    _, address, mask, value = values
    shift = (mask & -mask).bit_length() - 1
    registers[address] = (registers[address] & ~mask) | ((value << shift) & mask)
    return 0


module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
oracle = ModuleOracle(module, ['halbb_set_pd_lower_bound_8852c',
                              'halbb_set_pd_lower_bound_8192xb'], boundary)
bb = 0x6000000
oracle.uc.mem_map(bb, 0x10000)
cases = 0
with tempfile.TemporaryDirectory(prefix='rtl-ax-dig-bandwidth-') as folder:
    work = Path(folder)
    (work / 'test.c').write_text(source)
    subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Werror', '-shared', '-fPIC',
                    '-I' + str(driver), str(work / 'test.c'), '-o', str(work / 'test.so')], check=True)
    native = ctypes.CDLL(str(work / 'test.so'))
    native.native_pd.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint32]
    native.native_pd.restype = ctypes.c_uint32
    for chip, widths in [('8852c', range(4)), ('8192xb', range(2))]:
        for width in widths:
            for bound in range(111):
                for initial in (0, 0xa5a5a5a5):
                    registers[0x481c] = initial
                    oracle.call('halbb_set_pd_lower_bound_' + chip, bb, bound, width, 0)
                    assert native.native_pd(bound, width, initial) == registers[0x481c], (chip, width, bound)
                    cases += 1
    native.channel_lifecycle()
print(f'PASS: {cases} factory ARM64 PD vectors and production C/XB channel callbacks: 20 -> 40 -> 20 MHz')
