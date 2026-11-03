#!/usr/bin/env python3
"""Compare 2.4/5 GHz HT-only RA commands with the original ARM64 encoder."""
from pathlib import Path
import ctypes
import subprocess
import tempfile
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

root = Path(__file__).resolve().parents[1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_halbb_raregistered', 'halbb_init_ra_by_rssi', 'halbb_nss_mapping',
         'halbb_hw_mode_mapping', 'halbb_get_opt_giltf', 'halbb_ax_giltf_chk',
         'halbb_hw_bw_mapping', 'halbb_hw_bw_mode_chk', 'halbb_chk_bw_under_40', 'halbb_chk_bw_under_20',
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


driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
header = (driver / 'rate.h').read_text()
if '--before' in sys.argv:
    header = subprocess.check_output(['git', 'show', 'HEAD:' + str((driver / 'rate.h').relative_to(root))], cwd=root, text=True)
a = header.index('static inline void rtl_ax_ra_ht20(')
b = header.index('static inline void rtl_ax_ra_vht80(', a)
fixture = """#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint64_t u64;
#define BIT_ULL(n) (1ULL<<(n))
static void put_unaligned_le64(u64 v,void*p){memcpy(p,&v,8);}
""" + header[a:b] + """
void native(u8*c,u8 id,u16 legacy,u16 ht,bool sgi,bool band){rtl_ax_ra_ht20(c,id,legacy,ht,sgi,band);}
"""
if '--before' in sys.argv:
    fixture = fixture.replace('rtl_ax_ra_ht20(c,id,legacy,ht,sgi,band);', '(void)band;rtl_ax_ra_ht20(c,id,legacy,ht,sgi);')
count=0
with tempfile.TemporaryDirectory(prefix='ht-band-ra-') as directory:
 work=Path(directory); (work/'native.c').write_text(fixture)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(work/'native.c'),'-o',str(work/'native.so')],check=True)
 native=ctypes.CDLL(str(work/'native.so')).native
 native.argtypes=[ctypes.c_void_p,ctypes.c_uint8,ctypes.c_uint16,ctypes.c_uint16,ctypes.c_bool,ctypes.c_bool]
 for band,ic in [(1,8),(0,32)]:
  for macid in [1,7,63,127]:
   for ht in [0xff,0xffff,0x7f7f,0xf03]:
    for legacy in ([0xff0,0x150,0x10] if band else [0xfff,0x15,0x11]):
     for sgi in [False,True]:
      for address in (bb,phl,common,sta,halsta):oracle.uc.mem_write(address,bytes(0x10000))
      oracle.word(bb,phl);oracle.word(bb+8,common);oracle.word(bb+(macid+2)*8,sta)
      oracle.uc.mem_write(bb+1168+macid,b'\x01');oracle.uc.mem_write(bb+3092,struct.pack('<I',ic))
      oracle.uc.mem_write(phl+0x58fa,b'\x36');oracle.uc.mem_write(phl+0x5875,b'\x02')
      oracle.word(sta+2688,halsta);oracle.uc.mem_write(sta+28,bytes([macid]))
      oracle.uc.mem_write(sta+40,struct.pack('<I',band));oracle.uc.mem_write(sta+44,b'\x00')
      oracle.uc.mem_write(sta+2332,bytes([12 if band else 11]))
      oracle.uc.mem_write(sta+2656,struct.pack('<I',(2 if ht>>8 else 1)<<20));oracle.uc.mem_write(halsta+140,b'\x02')
      oracle.uc.mem_write(sta+0x9d0,struct.pack('<I',int(sgi)<<28))
      oracle.word(halsta+152,legacy | ((ht&255)<<12) | ((ht>>8)<<24));commands.clear()
      assert oracle.call('rtw_halbb_raregistered',bb,sta)==1
      out=ctypes.create_string_buffer(16);native(out,macid,legacy,ht,sgi,bool(band))
      assert out.raw.hex()==commands[0]['payload'],(band,macid,ht,legacy,sgi,out.raw.hex(),commands)
      count+=1
# The caller must shift mac80211's 5 GHz index space before applying firmware masks.
monitor=(driver/'monitor.h').read_text()
assert 'rtl_ax_ra_ht20(command, id, link->supp_rates[radio->band.band] << (band5g ? 4 : 0),' in monitor
print('HT band RA:',count,'original C/XB command vectors match; 5GHz index shift, mode and report format checked')
