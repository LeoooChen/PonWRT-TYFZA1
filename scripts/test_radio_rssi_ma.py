#!/usr/bin/env python3
"""Compare production RSSI state and PPDU eligibility with actual stock ELF."""
import argparse
import ctypes
import hashlib
import itertools
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--module', type=Path, default=root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko')
args = parser.parse_args()
# Prefer the host-native Unicorn library on macOS before the oracle's deps path.
if sys.platform == 'darwin':
    import unicorn
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X20, UC_ARM64_REG_X22, UC_ARM64_REG_X23

names = ['halbb_rx_pkt_su_store_in_sta_info', 'halbb_media_status_update',
         'halbb_cmn_info_self_update', 'halbb_physts_parsing', 'halbb_physts_ie_hdr',
         'halbb_physts_ie_00', 'halbb_physts_ie_01', 'halbb_mod_rssi_by_path_en',
         'halbb_cnvrt_2_sign', 'halbb_physts_fd_rpl_2_rssi_cvrt', 'halbb_cmn_rpt',
         'halbb_is_cck_rate', 'hal_handle_rx_buffer_8852c']
ignored = {'printk', 'halbb_update_tx_path_div', 'halbb_rate_idx_parsor',
           'halbb_physts_rpt_gen', 'halbb_rx_pkt_su_cnt_rpt',
           'halbb_rx_pkt_su_non_data_cnt_rpt', 'halbb_rx_pkt_cnt_rpt_beacon',
           'halbb_rx_pkt_su_rssi_statistic', 'halbb_rx_pkt_mu_cnt_rpt',
           'halbb_rx_pkt_mu_rssi_statistic', 'halbb_parsing_cfo',
           'halbb_antdiv_phy_sts', 'halbb_pathdiv_phy_sts', 'halbb_sniffer_phy_sts',
           'halbb_parsing_aci2sig', 'halbb_physts_print', 'halbb_rx_pop_hist',
           'halbb_idle_time_pwr_physts', 'halbb_rx_pkt_su_phy_hist'}


def callback(name, values):
    if name in ignored:
        return 0
    raise AssertionError((name, values))


o = ModuleOracle(args.module, names, callback)
bb, sta, halsta, desc, phy_buffer, report = [0x6000000 + i * 0x10000 for i in range(6)]
for address in (bb, sta, halsta, desc, phy_buffer, report):
    o.uc.mem_map(address, 0x10000)


def put(address, fmt, value):
    o.uc.mem_write(address, struct.pack('<' + fmt, value))


def reset():
    for address in (bb, sta, halsta, desc):
        o.uc.mem_write(address, bytes(0x10000))
    o.word(bb + 24, sta)
    o.word(sta + 0xa80, halsta)
    put(sta + 0x18, 'B', 1)
    put(sta + 0x1c, 'H', 1)
    put(bb + 0x411, 'B', 1)
    put(bb + 0xc20, 'B', 2)
    put(bb + 0x3334, 'B', 1)
    put(desc + 4, 'B', 1)
    o.call('halbb_media_status_update', bb, sta, 1)


class State(ctypes.Structure):
    _fields_ = [('ma', (ctypes.c_uint16 * 3) * 2),
                ('count', ctypes.c_uint16 * 2), ('factor', ctypes.c_uint8 * 2)]


class Sample(ctypes.Structure):
    _fields_ = [('macid', ctypes.c_uint8), ('rssi', ctypes.c_uint8 * 3),
                ('paths', ctypes.c_uint8), ('beacon', ctypes.c_bool)]


prefix = '''#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define BIT_ULL(n) (1ULL << (n))
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v,p,4); return v; }
static u64 get_unaligned_le64(const void *p) { u64 v; memcpy(&v,p,8); return v; }
static void put_unaligned_le64(u64 v,void *p) { memcpy(p,&v,8); }
'''
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rate.h'
source = prefix + '#include "' + str(header) + '"\n' + '''
void init(struct rtl_ax_ra_rssi *s) { rtl_ax_ra_rssi_init(s); }
void sample(struct rtl_ax_ra_rssi *s,struct rtl_ax_ra_sample *p,u8 *r) { rtl_ax_ra_rssi_sample(s,p,r); }
void watchdog(struct rtl_ax_ra_rssi *s) { rtl_ax_ra_rssi_watchdog(s); }
unsigned bb_rssi(struct rtl_ax_ra_rssi *s) { return rtl_ax_bb_rssi(s); }
int ppdu(const u8 *p,unsigned n,u8 type,bool self,struct rtl_ax_ra_sample *s) {
    return rtl_ax_ppdu_ra_signal(p,n,type,self,s);
}
'''


def compare_state(state):
    for kind, base, count in ((0, 6, 0x10), (1, 0x14, 0x1e)):
        assert list(state.ma[kind]) == list(o.unpack(halsta + base, '3H'))
        assert state.count[kind] == o.unpack(halsta + count, 'H')[0]
    assert list(state.factor) == [o.unpack(halsta + 0x20, 'B')[0] & 15,
                                  o.unpack(halsta + 0x20, 'B')[0] >> 4]


producer_vectors = factor_vectors = qualification_vectors = 0
with tempfile.TemporaryDirectory(prefix='tyfza1-rssi-ma-') as temp:
    c, library = Path(temp) / 'native.c', Path(temp) / 'native.so'
    c.write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror', str(c), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    native.init.argtypes = native.watchdog.argtypes = [ctypes.POINTER(State)]
    native.sample.argtypes = [ctypes.POINTER(State), ctypes.POINTER(Sample), ctypes.c_void_p]
    native.ppdu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint8, ctypes.c_bool, ctypes.POINTER(Sample)]
    rng = random.Random(9617)
    for factor in (1, 2, 3, 4):
        reset()
        state, rssi = State(), (ctypes.c_uint8 * 3)()
        native.init(state)
        compare_state(state)
        state.factor[:] = [factor, factor]
        put(halsta + 0x20, 'B', factor * 17)
        # Interleave data/beacon; vary paths and zero samples independently.
        for seq in range(1024):
            sample = Sample(1, (ctypes.c_uint8 * 3)(*[rng.randrange(0, 256) for _ in range(3)]), seq % 4, bool(seq & 4))
            put(desc + 0xd, 'B', 8 if sample.beacon else 1)
            put(bb + 0x32bc, 'B', sample.paths)
            for off, value in zip((0x3390, 0x3388, 0x3389), sample.rssi):
                put(bb + off, 'B', value)
            native.sample(state, sample, rssi)
            o.call('halbb_rx_pkt_su_store_in_sta_info', bb, desc)
            compare_state(state)
            assert rssi[0] == o.unpack(halsta + 4, 'B')[0]
            assert list(rssi)[1:] == [state.ma[0][1] >> 4, state.ma[0][2] >> 4]
            producer_vectors += 1
    entry = o.functions['halbb_cmn_info_self_update']
    for data in (0, 5, 6, 20, 21, 100, 101, 65535):
        for beacon in (0, 5, 6, 65535):
            state.count[:] = [data, beacon]
            put(halsta + 0x10, 'H', data)
            put(halsta + 0x1e, 'H', beacon)
            native.watchdog(state)
            o.uc.reg_write(UC_ARM64_REG_X20, bb)
            o.uc.reg_write(UC_ARM64_REG_X22, halsta)
            o.uc.emu_start(entry + 0x368, entry + 0x3b0, count=100)
            compare_state(state)
            factor_vectors += 1

    native.bb_rssi.argtypes = [ctypes.POINTER(State)]
    for data in (0, 1, 7, 8, 127, 1024, 2040, 3000, 4080):
        for beacon in (0, 1, 7, 8, 127, 1024, 2040, 3000, 4080):
            state.ma[0][0] = data
            state.ma[1][0] = beacon
            put(halsta + 4, 'B', (data + 8) >> 4)
            put(halsta + 18, 'B', (beacon + 8) >> 4)
            put(halsta + 35, 'B', 0)  # Native does not store association RSSI.
            o.uc.reg_write(UC_ARM64_REG_X22, halsta)
            o.uc.emu_start(entry + 0xb4, entry + 0x368, count=20)
            assert native.bb_rssi(state) == o.uc.reg_read(UC_ARM64_REG_X23), (data, beacon)
    print('PASS: shared DIG/RA RSSI selection matches actual normal watchdog, 81 cases')

    # Execute the original type->is_su instruction block, then the complete
    # PHY parser/IE conversion/common-report/MA chain with bookkeeping stubbed.
    from unicorn.arm64_const import UC_ARM64_REG_X1, UC_ARM64_REG_X3, UC_ARM64_REG_X19
    wrapper = o.functions['hal_handle_rx_buffer_8852c']
    for ppdu_type in range(16):
        for to_self in (False, True):
            for flags in (0, 1, 2, 3, 5, 9, 16, 17, 19):
                for ie, bitmap in itertools.product((0, 1), (3, 6, 7, 8, 13)):
                    for paths in (0, 1, 2, 3):
                        reset()
                        state, rssi, sample = State(), (ctypes.c_uint8 * 3)(), Sample()
                        native.init(state)
                        # One/three users proves SU comes from WD type, not user count.
                        users = 1 if paths & 1 else 3
                        raw_ie = bytearray(16 if ie == 0 else 32)
                        raw_ie[0], raw_ie[1] = ie, 155
                        raw_ie[3 if ie else 15] = paths << 4
                        phy = bytes([0x80 | bitmap, (8 + len(raw_ie)) // 8, 0, 91, 89, 96, 0, 0]) + raw_ie
                        raw = struct.pack('<II', users, 0)
                        raw += struct.pack('<I', flags | 1 << 8)
                        raw += b''.join(struct.pack('<I', 3 | (i + 2) << 8) for i in range(users - 1))
                        raw += bytes((users & 1) * 4) + phy
                        got = native.ppdu(raw, len(raw), ppdu_type, to_self, sample)
                        # Actual ARM instructions 279ae0..279af8 set X3=is_su.
                        put(desc + 0x3b, 'B', ppdu_type)
                        o.uc.reg_write(UC_ARM64_REG_X19, desc)
                        o.uc.emu_start(wrapper + 0x32c, wrapper + 0x344, count=10)
                        is_su = o.uc.reg_read(UC_ARM64_REG_X3)
                        o.uc.mem_write(desc, bytes(64))
                        put(desc, 'B', is_su | min(users, 3) << 1 | int(to_self) << 3)
                        put(desc + 2, 'H', 4 if ie else 0)
                        put(desc + 4, 'B', 99)  # Parser must replace with user0 MACID.
                        put(desc + 0xc, 'B', 1 if flags & 1 else 255)
                        put(desc + 0xd, 'B', (flags >> 1) & 15)
                        o.uc.mem_write(phy_buffer, phy)
                        o.call('halbb_physts_parsing', bb, phy_buffer, len(phy), desc, report)
                        actual_count = o.unpack(halsta + 0x10, 'H')[0] + o.unpack(halsta + 0x1e, 'H')[0]
                        assert got == actual_count, (ppdu_type, to_self, flags, ie, paths, bitmap, got, actual_count)
                        if got:
                            native.sample(state, sample, rssi)
                            compare_state(state)
                            assert sample.macid == 1 and sample.paths == paths
                        qualification_vectors += 1

result = {'module': str(args.module), 'module_sha256': hashlib.sha256(args.module.read_bytes()).hexdigest(),
          'producer_vectors': producer_vectors, 'factor_vectors': factor_vectors,
          'full_PHY_parser_qualification_vectors': qualification_vectors,
          'boundary': 'Actual ELF; logging, non-RSSI statistics, report formatting and unrelated DM callbacks stubbed. No device IO.',
          'sampling_limit': 'Factor counts sampled qualifying SU PPDU PHY reports; full-flow MPDU counts are not equivalent.'}
(root / 'artifacts/radio-rssi-ma-oracle.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
