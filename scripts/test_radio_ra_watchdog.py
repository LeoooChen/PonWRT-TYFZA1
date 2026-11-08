#!/usr/bin/env python3
"""Compare native RSSI mask updates with the board module watchdog."""
from pathlib import Path
import hashlib
import json
import struct
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['halbb_ra_update_mask_watchdog', 'rtw_halbb_raupdate', 'rtw_halbb_raregistered', 'halbb_init_ra_by_rssi', 'halbb_nss_mapping',
         'halbb_hw_mode_mapping', 'halbb_get_opt_giltf', 'halbb_ax_giltf_chk',
         'halbb_chk_bw_under_20', 'halbb_hw_bw_mapping', 'halbb_hw_bw_mode_chk', 'halbb_chk_bw_under_40',
         'halbb_giltf_trans', 'halbb_stbc_mapping',
         'halbb_ldpc_chk', 'halbb_sgi_chk', 'halbb_ac_n_sgi_chk', 'halbb_ramask_mod',
         'halbb_ramask_trans', 'halbb_set_csi_rate', 'halbb_rssi_lv_dec',
         'halbb_ramask_by_rssi', 'halbb_get_fw_ra_rpt', 'halbb_c2h_ra_parsing',
         'rtw_halbb_c2h_parsing']
commands = []
def callback(name, args):
    if name == 'printk':
        return 0
    if name == 'halbb_fill_h2c_cmd':
        pointer = oracle.uc.reg_read(UC_ARM64_REG_X4)
        commands.append({'class': args[3], 'function': args[2], 'bytes': args[1],
                         'payload': bytes(oracle.uc.mem_read(pointer, args[1])).hex()})
        return 1
    raise RuntimeError((name, args))

oracle = ModuleOracle(module, names, callback)
bb, phl, common, sta, halsta, feedback = [0x6000000 + i * 0x10000 for i in range(6)]
for address in (bb, phl, common, sta, halsta, feedback):
    oracle.uc.mem_map(address, 0x10000)
import ctypes,tempfile
prefix="""#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u64 get_unaligned_le64(const void *p){u64 v;memcpy(&v,p,8);return v;}
static void put_unaligned_le64(u64 v,void *p){memcpy(p,&v,8);}
"""
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'test.c';lib=Path(temp)/'test.so'
 c.write_text(prefix+(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rate.h').read_text()+"\nu8 run(u8 *c,u8 r,u8 p){return rtl_ax_ra_rssi_mask(c,r,p); }\nvoid noise(u8 *c,u8 n){rtl_ax_ra_environment(c,n);}\n")
 import subprocess
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Werror',str(c),'-o',str(lib)],check=True)
 native=ctypes.CDLL(str(lib)).run;native.argtypes=[ctypes.c_void_p,ctypes.c_uint8,ctypes.c_uint8];native.restype=ctypes.c_uint8
 noise=ctypes.CDLL(str(lib)).noise;noise.argtypes=[ctypes.c_void_p,ctypes.c_uint8]
 count=0
 for chip,band in [(8,1),(32,0)]:
  for mode,wm,mask in [('HE',0x24 if band else 0x23,0xffffff010 if band else 0xffffff015),('VHT',0x14,0x3ff3ff010),('HT',0xc if band else 0xb,0x0ff0ff010 if band else 0xff0ff015),('OFDM',4,0xff0),('CCK',1,15),('BG',3,0xff5)]:
   if chip==32 and mode=='VHT':continue
   if band and mode in ('CCK','BG'):continue
   for width in ([0,1,2,3] if band else [0,1]):
    for addr in [bb,phl,common,sta,halsta]:oracle.uc.mem_write(addr,bytes(65536))
    oracle.word(bb,phl);oracle.word(bb+8,common);oracle.word(bb+24,sta);oracle.word(sta+2688,halsta)
    oracle.uc.mem_write(bb+3092,struct.pack('<I',chip));oracle.uc.mem_write(phl+0x58fa,b'\x36');oracle.uc.mem_write(phl+0x5875,b'\x02')
    oracle.uc.mem_write(sta+28,b'\1');oracle.uc.mem_write(sta+40,struct.pack('<I',band));oracle.uc.mem_write(sta+44,bytes([width]));oracle.uc.mem_write(sta+2332,bytes([wm]));oracle.uc.mem_write(sta+2656,struct.pack('<I',2<<20));oracle.uc.mem_write(halsta+140,b'\2');oracle.word(halsta+152,mask)
    commands.clear();oracle.call('rtw_halbb_raregistered',bb,sta);baseline=bytes.fromhex(commands[-1]['payload'])
    for previous in range(7):
     for rssi in range(104):
      oracle.uc.mem_write(bb+0xd6a+33,bytes([previous]));oracle.uc.mem_write(halsta+4,bytes([rssi*2]));commands.clear()
      oracle.call('halbb_ra_update_mask_watchdog',bb,sta)
      out=ctypes.create_string_buffer(baseline,16);level=native(out,rssi,previous)
      want=bytes.fromhex(commands[-1]['payload'])
      assert out.raw==want,(chip,mode,width,rssi,previous,out.raw.hex(),want.hex())
      assert level==oracle.unpack(bb+0xd6a+33,'B')[0]
      count+=1
 # Registration, capability update and watchdog all consume the same NHM byte.
 for chip in (8,32):
  oracle.uc.mem_write(bb+3092,struct.pack('<I',chip))
  for ratio in (0,1,2,100,254,255):
   oracle.uc.mem_write(bb+0x234a,bytes([ratio]))
   for name in ('rtw_halbb_raregistered','rtw_halbb_raupdate','halbb_ra_update_mask_watchdog'):
    commands.clear();oracle.call(name,bb,sta)
    original=bytes.fromhex(commands[-1]['payload'])
    seed=bytearray(original);seed[15]^=1
    out=ctypes.create_string_buffer(bytes(seed),16);noise(out,ratio)
    assert out.raw==original,(chip,ratio,name,out.raw.hex(),original.hex())
 print('Native NHM: 36 registration/update/watchdog commands match original, including failed ratio255')
 print('Native RSSI watchdog:',count,'complete 16-byte commands and hysteresis states match original C/XB ARM64')
