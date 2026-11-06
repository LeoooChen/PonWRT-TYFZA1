#!/usr/bin/env python3
"""Compare native HT20 RA commands with the original board's ARM64 code."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

module = Path(sys.argv[1]) if len(sys.argv)>1 else root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['rtw_halbb_raregistered', 'halbb_init_ra_by_rssi', 'halbb_nss_mapping',
         'halbb_hw_mode_mapping', 'halbb_get_opt_giltf', 'halbb_ax_giltf_chk',
         'halbb_hw_bw_mapping', 'halbb_hw_bw_mode_chk', 'halbb_chk_bw_under_40', 'halbb_chk_bw_under_20',
         'halbb_giltf_trans', 'halbb_stbc_mapping', 'halbb_ldpc_chk',
         'halbb_sgi_chk', 'halbb_ac_n_sgi_chk', 'halbb_ramask_mod',
         'halbb_ramask_trans', 'halbb_set_csi_rate', 'halbb_rssi_lv_dec',
         'halbb_ramask_by_rssi']
commands = []


def callback(name, args):
    if name == 'printk':
        return 0
    assert name == 'halbb_fill_h2c_cmd', name
    assert args[1:4] == [16, 0, 1]
    commands.append(bytes(oracle.uc.mem_read(oracle.uc.reg_read(UC_ARM64_REG_X4), 16)))
    return 1


oracle = ModuleOracle(module, names, callback)
bb, phl, common, sta, halsta = [0x6000000 + i * 0x10000 for i in range(5)]
for address in (bb, phl, common, sta, halsta):
    oracle.uc.mem_map(address, 0x10000)
source = '''#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define BIT_ULL(n) (1ULL << (n))
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static void put_unaligned_le64(u64 v, void *p) { memcpy(p, &v, 8); }
static u64 get_unaligned_le64(const void *p) { u64 v; memcpy(&v, p, 8); return v; }
static inline u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v, p, 4); return v; }
''' + (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rate.h').read_text() + '''
void native_legacy(u8 *p, u8 id, u16 rates, bool five) { rtl_ax_ra_legacy(p,id,rates,five); }
void native(u8 *p, u8 id, u16 legacy, u16 ht, bool sgi) {
    rtl_ax_ra_ht20(p, id, legacy, ht, sgi, false);
}
void native_vht(u8 *p, u8 id, u16 legacy, u16 mcs, bool sgi) {
    rtl_ax_ra_vht80(p, id, legacy, mcs, sgi);
}
'''
# Exercise the actual peer selector as well as the packet encoders.
monitor = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()
a = monitor.index('static bool rtl_ax_ra_peer(')
b = monitor.index('\nstatic int rtl_ax_station_add(', a)
source += r'''
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define le16_to_cpu(x) (x)
#define __ffs(x) __builtin_ctz(x)
#define RTL_AX_8852C 0x52
#define NL80211_BAND_5GHZ 1
#define IEEE80211_SMPS_STATIC 1
#define IEEE80211_SMPS_DYNAMIC 2
#define IEEE80211_VHT_CAP_SHORT_GI_80 (1U<<5)
#define IEEE80211_VHT_CAP_SHORT_GI_160 (1U<<6)
#define IEEE80211_HT_CAP_SGI_20 (1U<<5)
#define IEEE80211_HT_CAP_SGI_40 (1U<<6)
struct ieee80211_sta_he_cap {bool has_he;struct {u16 rx_mcs_80,rx_mcs_160;}he_mcs_nss_supp;
 struct {u8 phy_cap_info[11];}he_cap_elem;};
struct ieee80211_link_sta {unsigned bandwidth,rx_nss,smps_mode,supp_rates[2];
 struct {bool ht_supported;u32 cap;struct {u8 rx_mask[2];}mcs;}ht_cap;
 struct {bool vht_supported;u32 cap;struct {u16 rx_mcs_map;}vht_mcs;}vht_cap;
 struct ieee80211_sta_he_cap he_cap;};
struct ieee80211_sta {struct ieee80211_link_sta deflink;struct {u32 max_amsdu_len;}*cur;};
struct rtl_ax_ap {unsigned chip;struct {unsigned bandwidth;}channel;struct {unsigned band;}band;};
''' + monitor[a:b] + r'''
int native_peer(u8 *out,unsigned five,unsigned rates,unsigned update){
 struct rtl_ax_ap radio={.chip=five?RTL_AX_8852C:0x53,.channel={3},.band={five}};
 struct ieee80211_sta sta={};typeof(*sta.cur)cur={.max_amsdu_len=7935};sta.cur=&cur;
 sta.deflink.supp_rates[five]=rates; /* No HT/VHT/HE and no NSS from peer. */
 u32 rate=~0U,caps=~0U;int ret=rtl_ax_ra_peer(&radio,&sta,1,out,&rate,&caps,update);
 memcpy(out+16,&rate,4);memcpy(out+20,&caps,4);return ret;
}
'''
vectors = 0
with tempfile.TemporaryDirectory(prefix='tyfza1-ra-contract-') as temp:
    c = Path(temp) / 'native.c'
    c.write_text(source)
    library = Path(temp) / 'native.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror', str(c), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library)).native
    native.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_bool]
    for macid in (1, 7, 63, 127):
        for ht in (0x00ff, 0xffff, 0x7f7f, 0x0f03):
            for legacy in (0xfff, 0xff5, 0x011, 0x004):
                for sgi in (False, True):
                    for address in (bb, phl, common, sta, halsta):
                        oracle.uc.mem_write(address, bytes(0x10000))
                    oracle.word(bb, phl)
                    oracle.word(bb + 8, common)
                    oracle.word(bb + (macid + 2) * 8, sta)
                    oracle.uc.mem_write(bb + 3092, struct.pack('<I', 32))
                    oracle.uc.mem_write(phl + 0x58fa, b'\x36')
                    oracle.uc.mem_write(phl + 0x5875, b'\x02')
                    oracle.word(sta + 2688, halsta)
                    oracle.uc.mem_write(sta + 28, bytes([macid]))
                    oracle.uc.mem_write(sta + 2332, b'\x0b')
                    oracle.uc.mem_write(sta + 2656, struct.pack('<I', (2 if ht >> 8 else 1) << 20))
                    oracle.uc.mem_write(sta + 0x9d0, struct.pack('<I', int(sgi) << 28))
                    oracle.uc.mem_write(halsta + 140, b'\x02')
                    oracle.word(halsta + 152, legacy | (ht & 255) << 12 | (ht >> 8) << 24)
                    commands.clear()
                    assert oracle.call('rtw_halbb_raregistered', bb, sta) == 1
                    output = ctypes.create_string_buffer(16)
                    native(output, macid, legacy, ht, sgi)
                    assert len(commands) == 1 and output.raw == commands[0], (macid, ht, legacy, sgi, output.raw.hex(), commands)
                    vectors += 1
    vht = ctypes.CDLL(str(library)).native_vht
    vht.argtypes = native.argtypes
    vht_vectors = 0
    for macid in (1, 7, 63, 127):
        for mcs in (0xfff0, 0xfff5, 0xfffa, 0xfffc, 0xfffd, 0xfffe):
            for sgi in (False, True):
                for address in (bb, phl, common, sta, halsta):
                    oracle.uc.mem_write(address, bytes(0x10000))
                oracle.word(bb, phl)
                oracle.word(bb + 8, common)
                oracle.word(bb + (macid + 2) * 8, sta)
                oracle.uc.mem_write(bb + 3092, struct.pack('<I', 8))
                oracle.uc.mem_write(phl + 0x58fa, b'\x36')
                oracle.uc.mem_write(phl + 0x5875, b'\x02')
                oracle.word(sta + 2688, halsta)
                oracle.uc.mem_write(sta + 28, bytes([macid]))
                oracle.uc.mem_write(sta + 40, struct.pack('<I', 1))
                oracle.uc.mem_write(sta + 44, b'\x02')
                oracle.uc.mem_write(sta + 2332, b'\x14')
                oracle.uc.mem_write(sta + 2656, struct.pack('<I', (1 if mcs >> 2 & 3 == 3 else 2) << 20))
                oracle.uc.mem_write(sta + 0x9d0, struct.pack('<I', int(sgi) << 30))
                oracle.uc.mem_write(halsta + 140, b'\x02')
                mask = 0xff0
                for i in range(2):
                    cap = mcs >> (i * 2) & 3
                    if cap != 3: mask |= ((1 << (8 + cap)) - 1) << (12 + i * 12)
                oracle.word(halsta + 152, mask)
                commands.clear()
                assert oracle.call('rtw_halbb_raregistered', bb, sta) == 1
                output = ctypes.create_string_buffer(16)
                vht(output, macid, 0xff0, mcs, sgi)
                assert len(commands) == 1 and output.raw == commands[0], (macid, mcs, sgi, output.raw.hex(), commands)
                vht_vectors += 1
    legacy_native = ctypes.CDLL(str(library)).native_legacy
    legacy_native.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_bool]
    selector = ctypes.CDLL(str(library)).native_peer
    selector.argtypes = [ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint]
    legacy_vectors = 0
    for five, mode, rates in ((False,1,15),(False,2,0xff0),(False,3,0xfff),(True,4,0xff0)):
        for macid in (1,7,63,127):
            for address in (bb,phl,common,sta,halsta):
                oracle.uc.mem_write(address,bytes(0x10000))
            oracle.word(bb,phl);oracle.word(bb+8,common);oracle.word(bb+(macid+2)*8,sta)
            oracle.uc.mem_write(bb+3092,struct.pack('<I',8 if five else 32))
            oracle.uc.mem_write(phl+0x58fa,b'\x36');oracle.uc.mem_write(phl+0x5875,b'\x02')
            oracle.word(sta+2688,halsta);oracle.uc.mem_write(sta+28,bytes([macid]))
            oracle.uc.mem_write(sta+40,struct.pack('<I',int(five)))
            oracle.uc.mem_write(sta+2332,bytes([mode]))
            oracle.uc.mem_write(sta+2656,struct.pack('<I',1<<20))
            oracle.uc.mem_write(halsta+140,b'\x02');oracle.word(halsta+152,rates)
            commands.clear();assert oracle.call('rtw_halbb_raregistered',bb,sta)==1
            output=ctypes.create_string_buffer(16);legacy_native(output,macid,rates,five)
            assert output.raw==commands[0],(five,mode,output.raw.hex(),commands[0].hex())
            legacy_vectors+=1
        for update in (False,True):
            output=ctypes.create_string_buffer(24)
            assert selector(output,five,rates>>4 if five else rates,update)==1
            command=bytearray(commands[0]);command[1]=1
            if update:command[2]&=~16;command[3]=(command[3]&~128)|64
            assert output.raw[:16]==command,(five,mode,update,output.raw.hex(),command.hex())
            rate,caps=struct.unpack_from('<II',output.raw,16)
            assert rate==(rates&-rates).bit_length()-1 and caps==1<<17
    assert selector(ctypes.create_string_buffer(24),False,0,False)==0
report = {'native_command_equals_original_ARM64': True, 'vectors': vectors,
          'VHT80_original_command_vectors': vht_vectors, 'legacy_original_command_vectors': legacy_vectors,
          'chip': '8192XB', 'PHY': 'HT20', 'unknown_RSSI_init_level': 0,
          'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'H2C_publication_mocked': True, 'hardware_test': False}
(root / 'logs/radio-ra-native-contract.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'Native firmware RA: {vectors} HT20, {vht_vectors} VHT80, {legacy_vectors} a/b/g vectors passed; qualified PPDU coverage is in test_radio_rssi_ma.py')
