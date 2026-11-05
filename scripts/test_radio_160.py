#!/usr/bin/env python3
"""Check 160 MHz selectors, firmware notification and TSSI against this board's ELF."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from elftools.elf.elffile import ELFFile


def function(file, name):
    text = (driver / file).read_text()
    start = text.rfind('\n', 0, text.index(name + '(')) + 1
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def structure(file, name):
    return re.search(r'struct ' + name + r' \{.*?\}', (driver / file).read_text(), re.S)[0] + ';\n'


source = r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include "power.h"
typedef uint8_t u8;typedef int8_t s8;typedef uint16_t u16;typedef int16_t s16;typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define le16_to_cpu(v) (v)
#define NL80211_BAND_5GHZ 1
#define IEEE80211_VHT_CAP_SHORT_GI_80 BIT(5)
#define IEEE80211_VHT_CAP_SHORT_GI_160 BIT(6)
#define IEEE80211_HT_CAP_SGI_20 BIT(5)
#define IEEE80211_HT_CAP_SGI_40 BIT(6)
#define IEEE80211_SMPS_STATIC 2
#define IEEE80211_SMPS_DYNAMIC 3
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u64 get_unaligned_le64(const void *p){u64 v;memcpy(&v,p,8);return v;}
static void put_unaligned_le64(u64 v,void *p){memcpy(p,&v,8);}
#include "rate.h"
static u32 regs[0x20000/4];
static u32 read32(void *ctx,unsigned int address){return regs[address/4];}
static void write32(void *ctx,unsigned int address,u32 value){regs[address/4]=value;}
static void rtl_ax_store32(void *p,u32 value){memcpy(p,&value,4);}
static const struct rtl_ax_io io={.read32=read32,.write32=write32};
'''
for file, name in [('channel.h', 'rtl_ax_channel'), ('board-cal.h', 'rtl_ax_board_cal'),
                   ('trim.h', 'rtl_ax_trim'), ('tssi-de.h', 'rtl_ax_tssi_de'),
                   ('dig.h', 'rtl_ax_dig_unit'), ('dig.h', 'rtl_ax_dig')]:
    source += structure(file, name)
for file, names in [('channel.h', ('rtl_ax_channel_field', 'rtl_ax_channel_primary_index', 'rtl_ax_channel_txsc')),
                    ('c-iqk.h', ('rtl_ax_c_iqk_rxclock',)),
                    ('dig.h', ('rtl_ax_dig_bandwidth',)),
                    ('tssi-de.h', ('rtl_ax_tssi_cck_group', 'rtl_ax_tssi_ofdm_group', 'rtl_ax_tssi_trim_group',
                                   'rtl_ax_tssi_flash_ofdm', 'rtl_ax_tssi_flash_ofdm_de', 'rtl_ax_tssi_trim_de',
                                   'rtl_ax_c_tssi_bw_diff', 'rtl_ax_c_tssi_slope', 'rtl_ax_c_tssi_de20'))]:
    source += ''.join(function(file, name) for name in names)
source += '''
struct rtl_ax_ap {struct rtl_ax_channel channel;unsigned int chip;struct {unsigned int band;}band;};
struct ieee80211_sta_he_cap {
 bool has_he;
 struct {u16 rx_mcs_80,rx_mcs_160;} he_mcs_nss_supp;
 struct {u8 phy_cap_info[11];} he_cap_elem;
};
struct ieee80211_link_sta {
 unsigned int bandwidth,rx_nss,smps_mode,supp_rates[2];
 struct {struct {u8 rx_mask[2];}mcs;u32 cap;bool ht_supported;}ht_cap;
 struct {u32 cap;bool vht_supported;struct {u16 rx_mcs_map;}vht_mcs;}vht_cap;
 struct ieee80211_sta_he_cap he_cap;
};
struct ieee80211_sta {struct ieee80211_link_sta deflink;struct {u32 max_amsdu_len;}*cur;};
static u8 information[20];
static int rtl_ax_bb_info_locked(struct rtl_ax_ap *radio,u8 *p){memcpy(information,p,20);return 0;}
'''
source += function('pci.c', 'rtl_ax_bb_channel_notify_locked')
source += function('monitor.h', 'rtl_ax_ra_peer')
source += r'''
void peer(u8 *out){
 struct rtl_ax_ap radio={.chip=RTL_AX_8852C,.channel={.bandwidth=3},.band={.band=1}};
 struct ieee80211_sta sta={};typeof(*sta.cur) cur={.max_amsdu_len=7935};sta.cur=&cur;
 sta.deflink.bandwidth=3;sta.deflink.rx_nss=2;sta.deflink.supp_rates[1]=255;
 sta.deflink.he_cap.has_he=true;
 sta.deflink.he_cap.he_mcs_nss_supp.rx_mcs_80=0xfffa;
 sta.deflink.he_cap.he_mcs_nss_supp.rx_mcs_160=0xfffc;
 u32 rate,caps;
 rtl_ax_ra_peer(&radio,&sta,1,out,&rate,&caps,false);
 memcpy(out+16,&rate,4);
 sta.deflink.bandwidth=2;
 rtl_ax_ra_peer(&radio,&sta,1,out+20,&rate,&caps,false);
 memcpy(out+36,&rate,4);
 sta.deflink.bandwidth=3;sta.deflink.he_cap.has_he=false;
 sta.deflink.vht_cap.vht_supported=true;sta.deflink.vht_cap.vht_mcs.rx_mcs_map=0xfffa;
 sta.deflink.vht_cap.cap=IEEE80211_VHT_CAP_SHORT_GI_80;
 rtl_ax_ra_peer(&radio,&sta,1,out+40,&rate,&caps,false);
}
unsigned int selectors(unsigned int primary,unsigned int center,unsigned int bandwidth){return rtl_ax_channel_txsc(primary,center,bandwidth);}
void notify(unsigned int primary,unsigned int center,unsigned int bandwidth,u8 *out){
 struct rtl_ax_ap radio={.channel={.band=1,.primary=primary,.center=center,.bandwidth=bandwidth}};
 rtl_ax_bb_channel_notify_locked(&radio);memcpy(out,information,20);
}
void iqk_clock(u32 *out){
 memset(regs,0,sizeof(regs));rtl_ax_c_iqk_rxclock(&io,0,3);
 memcpy(out,regs,sizeof(regs));
}
u32 packet_detection(unsigned int bandwidth,unsigned int requested){
 struct rtl_ax_dig dig={.pd_requested=requested};regs[0x1481c/4]=0xa55a;
 rtl_ax_dig_bandwidth(&io,&dig,bandwidth);return regs[0x1481c/4];
}
void tssi(unsigned int bandwidth,u32 adc,u16 *out){
 struct rtl_ax_board_cal cal={};struct rtl_ax_trim trim={};struct rtl_ax_tssi_de state={};
 for(unsigned int p=0;p<2;p++){
  for(unsigned int g=0;g<14;g++)cal.tssi_5g[p][g]=p?-7:11;
  for(unsigned int g=0;g<8;g++)trim.tssi[p][g]=p?-3:2;
  for(unsigned int b=0;b<4;b++)for(unsigned int g=0;g<14;g++)cal.tssi_bw_diff[p][b][g]=b*3-(int)p;
 }
 memset(regs,0,sizeof(regs));regs[0x101a8/4]=adc;
 rtl_ax_c_tssi_de20(&io,&cal,&trim,50,bandwidth,&state);
 memcpy(out,state.expected,sizeof(state.expected));
}
'''
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
writes = []
adc = 0


def boundary(name, args):
    if name == 'printk':
        return 0
    if name == 'halrf_rreg':
        return (adc & args[2]) >> ((args[2] & -args[2]).bit_length() - 1) if args[1] == 0x1a8 else 0
    if name == '_halrf_tssi_get_cck_efuse_group_8852c':
        return 0
    if name == '_halrf_tssi_get_ofdm_tssi_trim_de_8852c':
        return (2 if args[2] == 0 else -3) & 255
    if name == '_halrf_tssi_get_ofdm_efuse_tssi_de_8852c':
        return (11 if args[2] == 0 else -7) & 255
    if name == '_halrf_tssi_get_ofdm_efuse_tssi_bw_diff_8852c':
        return (args[2] * 3 - args[3]) & 255
    if name == 'halrf_wreg':
        writes.append((args[1], args[2], args[3]))
        return 0
    if name == 'halbb_set_reg_cmn':
        writes.append((args[1], args[2], args[3]))
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['halrf_tssi_set_efuse_to_de_8852c', 'rtw_get_vht_highest_rate',
                              'rtw_vht_mcs_to_data_rate', 'halbb_set_pd_lower_bound_8852c'], boundary)
rf, hal, phl = 0x6000000, 0x6200000, 0x6210000
oracle.uc.mem_map(rf, 0x100000)
oracle.uc.mem_map(hal, 0x10000)
oracle.uc.mem_map(phl, 0x10000)
bb = 0x6300000
oracle.uc.mem_map(bb, 0x10000)
oracle.word(rf, phl)
oracle.word(rf + 8, hal)
oracle.uc.mem_write(phl + 0x58fa, b'\x36')
oracle.uc.mem_write(hal + 905, b'\x32')
offsets = [0x5858, 0x5860, 0x5838, 0x5840, 0x5848, 0x5850, 0x5828, 0x5830]
oracle.uc.mem_write(oracle.scratch, b'\xfa\xff')
highest = oracle.call('rtw_get_vht_highest_rate', oracle.scratch)
vht_highest = (oracle.call('rtw_vht_mcs_to_data_rate', 3, 1, highest) + 1) >> 1
assert vht_highest == 1734
assert 'cpu_to_le16(1734)' in (driver / 'monitor.h').read_text()
with tempfile.TemporaryDirectory(prefix='radio-160-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-I' + str(driver), str(work / 'native.c'),
                    '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.packet_detection.restype = ctypes.c_uint32
    for bandwidth in (0, 2, 3):
        for requested in (0, 30, 60, 93):
            writes.clear()
            oracle.call('halbb_set_pd_lower_bound_8852c', bb, requested, bandwidth, 0)
            expected = 0xa55a
            for address, mask, value in writes:
                assert address == 0x481c
                shift = (mask & -mask).bit_length() - 1
                expected = (expected & ~mask) | (value << shift & mask)
            assert native.packet_detection(bandwidth, requested) == expected
    peer = ctypes.create_string_buffer(56)
    native.peer(peer)
    assert peer.raw[0] == 0xe4 and peer.raw[3] & 7 == 0
    assert struct.unpack_from('<Q', peer.raw, 4)[0] == 0x80000000000ff010
    assert struct.unpack_from('<I', peer.raw, 16)[0] >> 16 & 3 == 3
    assert peer.raw[20] == 0xa4 and peer.raw[23] & 7 == 1
    assert struct.unpack_from('<I', peer.raw, 36)[0] >> 16 & 3 == 2
    assert peer.raw[40] == 0xd4 and not peer.raw[42] & 0x20
    bandwidths = json.loads((root / 'manifests/radio-stock-bandwidth.json').read_text())['records']
    channels = 0
    for row in bandwidths:
        if row['chip'] != '8852c':
            continue
        primary, center = row['primary_channel'], row['center_channel']
        expected = int(next(w['value'] for w in row['MAC_writes'] if w['address'] == '0xc088'), 16)
        assert native.selectors(primary, center, row['bandwidth_enum']) == expected
        out = ctypes.create_string_buffer(20)
        native.notify(primary, center, row['bandwidth_enum'], out)
        assert out.raw.hex() == row['BBinfo_H2C'][0]['payload']
        channels += row['width_MHz'] == 160
    clocks = (ctypes.c_uint32 * (0x20000 // 4))()
    native.iqk_clock(clocks)
    calibration = json.loads((root / 'manifests/radio-stock-C-calibration-bandwidth.json').read_text())['records']
    row = next(row for row in calibration if row['width_MHz'] == 160 and row['path'] == 0
               and row['function'] == '_iqk_rxk_setting_8852c')
    clock_writes = {}
    for write in row['writes']:
        address = int(write.get('address', '0'), 16)
        if address != 0x12a0 and not 0xc000 <= address < 0xc100:
            continue
        mask = int(write['mask'], 16)
        shift = (mask & -mask).bit_length() - 1
        previous = clock_writes.get(address, 0)
        clock_writes[address] = (previous & ~mask) | (int(write['value'], 16) << shift & mask)
    assert all(clocks[(address | 0x10000) // 4] == value for address, value in clock_writes.items())
    for bandwidth, adc in [(2, 0x0008fd05), (3, 0x0008fd05), (3, 0x00817f80)]:
        oracle.uc.mem_write(hal + 896, struct.pack('<I', bandwidth))
        writes.clear()
        oracle.call('halrf_tssi_set_efuse_to_de_8852c', rf, 0)
        expected = {(a, m): v for a, m, v in writes}
        out = (ctypes.c_uint16 * 16)()
        native.tssi(bandwidth, adc, out)
        for path in range(2):
            for index, address in enumerate(offsets):
                assert out[path * 8 + index] == expected[(address + path * 0x2000, 0x3ff000)], (bandwidth, path, index)
    with module.open('rb') as stream:
        elf = ELFFile(stream)
        symbol = elf.get_section_by_name('.symtab').get_symbol_by_name('rtw_5ghz_a_channels')[0]
        data = elf.get_section(symbol['st_shndx']).data()[symbol['st_value']:symbol['st_value'] + symbol['st_size']]
        factory = [struct.unpack_from('<H', data, offset + 10)[0] for offset in range(0, len(data), 64)]
    table = re.search(r'channels_5g\[\] = \{(.*?)\}', (driver / 'monitor.h').read_text(), re.S)[1]
    assert list(map(int, re.findall(r'\d+', table))) == factory
report = {'original_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'primary_positions_and_BBinfo': channels, 'TSSI_ADC_vectors': 3, 'IQK_RX_clock_160': True,
          'packet_detection_bandwidth_threshold_vectors': 12,
          'peer_HE160_separate_MCS_and_HE80_limit': True, 'peer_VHT160_separate_SGI': True,
          'factory_5GHz_channels': factory, 'factory_VHT160_highest_Mbps': vht_highest, 'hardware_test': False,
          'boundary': 'Register IO and TSSI calibration queries modelled; original builders and native bodies execute unchanged.'}
(root / 'manifests/radio-160-channel-tssi-contract.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
