#!/usr/bin/env python3
"""Compare this board's DFS classifier and report control with its actual ELF."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle


def function(file, name):
    source = (driver / file).read_text()
    start = source.rfind('\n', 0, source.index(name + '(')) + 1
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
names = ['halbb_dfs_init', 'halbb_dfs_rgn_dmn_dflt_cnfg', 'halbb_dfs_rgn_dmn_cnfg_by_ch',
         'halbb_dfs_change_dmn', 'halbb_radar_detect', 'halbb_radar_info_processing',
         'halbb_radar_seq_inspctn', 'halbb_radar_ptrn_cmprn', 'halbb_radar_chrp_mntr',
         'cfg_dfs', 'en_bbrpt', 'get_bbrpt_dle_cfg_8852c', 'halbb_radar_detect_enable',
         'halbb_radar_detect_disable']
registers, writes = {}, []


def boundary(name, args):
    if name == 'printk':
        return 0
    if name in ('memcpy', '_rtw_memcpy', 'halbb_mem_cpy'):
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name in ('memset', 'halbb_mem_set'):
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name in ('read8', 'read32'):
        value = registers.get(args[1], 0)
        if args[1] == 0x9630 and value & 16:
            value |= 32  # Hardware stop completion, not a fabricated classifier result.
        return value & (255 if name == 'read8' else 0xffffffff)
    if name in ('write8', 'write32'):
        writes.append((args[1], args[2]))
        registers[args[1]] = args[2]
        return 0
    if name == 'halbb_set_reg_phy0_1':
        address, mask, value = args[1:]
        address |= 0x10000
        value <<= (mask & -mask).bit_length() - 1
        registers[address] = registers.get(address, 0) & ~mask | value
        writes.append((address, registers[address]))
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, names, boundary)
bb, common, phl, report, data, adapter, bus, ops, config = [0x6000000 + i * 0x10000 for i in range(9)]
for address in (bb, common, phl, report, data, adapter, bus, ops, config):
    oracle.uc.mem_map(address, 0x10000)


def word(address, value):
    oracle.uc.mem_write(address, struct.pack('<I', value))


def initialize(region, primary, bandwidth):
    for address in (bb, common, phl):
        oracle.uc.mem_write(address, bytes(0x10000))
    oracle.word(bb, phl)
    oracle.word(bb + 8, common)
    word(bb + 3328, 8)  # DFS support ability.
    word(bb + 3092, 8)  # RTL8852C.
    word(bb + 3100, 2)  # This board's CLIENT CR type.
    word(phl + 0x64d8, region)
    word(common + 896, bandwidth)
    oracle.uc.mem_write(common + 904, bytes([primary]))
    oracle.call('halbb_dfs_init', bb)
    oracle.call('halbb_dfs_change_dmn', bb, primary, bandwidth)
    # Width-dependent branch of dfs_dyn_setting; no environment callbacks here.
    oracle.uc.mem_write(bb + 0x24f0 + 24, bytes([
        bandwidth == 3, region != 1 and primary <= 64 and bandwidth == 3]))


def pulse(width, interval, sequence, chirp=0, segment=0):
    value = ((width << 28) | ((interval & 15) << 24) | ((interval >> 4) << 20) |
             (chirp << 19) | (sequence << 56)) if not segment else (
             (width << 9) | (interval << 1) | chirp | (sequence << 49))
    return struct.pack('<Q', value)


source = r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define spin_lock_irqsave(lock,flags) ((void)(lock),(flags)=0)
#define spin_unlock_irqrestore(lock,flags) ((void)(lock),(void)(flags))
#define dma_wmb() ((void)0)
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_STOP_MASK 0xfff00
#define RTL_AX_TX_ENTRIES 256
#define ieee80211_wake_queues(hw) ((void)(hw))
#define ieee80211_stop_queues(hw) ((void)(hw))
#define synchronize_net() ((void)0)
#define read_poll_timeout(op,value,condition,delay,timeout,pre,arg) \
    ((value)=op(arg),(condition)?0:-ETIMEDOUT)
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u64 get_unaligned_le64(const void *p){u64 v;memcpy(&v,p,8);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
#include "power.h"
#include "rx-geometry.h"
static u32 regs[0x20000/4], trace[32][2];static unsigned used;static bool timeout;
static u32 read32(void *ctx,unsigned a){(void)ctx;return regs[a/4];}
static u8 read8(void *ctx,unsigned a){(void)ctx;return regs[a/4]>>((a&3)*8);}
static void write32(void *ctx,unsigned a,u32 v){(void)ctx;regs[a/4]=v;trace[used][0]=a;trace[used++][1]=v;}
static void write8(void *ctx,unsigned a,u8 v){write32(ctx,a,v);}
static int poll32(void *ctx,unsigned a,unsigned mask,unsigned expected,unsigned delay,unsigned limit,unsigned *last){
 (void)delay;(void)limit;*last=read32(ctx,a)|32;return timeout?-ETIMEDOUT:((*last&mask)==expected?0:-EIO);
}
static void delay_us(void *ctx,unsigned us){(void)ctx;(void)us;}
static const struct rtl_ax_io io={.read8=read8,.read32=read32,.write8=write8,.write32=write32,.poll32=poll32,.delay_us=delay_us};
'''
source += (driver / 'dfs.h').read_text().replace('#include <linux/types.h>', '').replace(
    '#include <linux/string.h>', '').replace('#include <linux/unaligned.h>', '')
source += r'''
static struct rtl_ax_dfs state;
void init(unsigned region,unsigned primary,unsigned bw){memset(&state,0,sizeof(state));rtl_ax_dfs_initialize(&state,region);rtl_ax_dfs_channel(&state,region,primary,bw);}
int detect(const void *p,unsigned count,unsigned seconds){return rtl_ax_dfs_detect(&state,p,count,seconds);}
unsigned hardware(unsigned enabled,u32 *out){
 memset(regs,0,sizeof(regs));regs[0x9630/4]=0xd41;regs[0x10000/4]=0x8580801f;used=0;timeout=false;
 assert(!rtl_ax_dfs_hardware(&io,enabled));memcpy(out,trace,used*8);return used;
}
struct rtl_ax_tx {
 const struct rtl_ax_io *io;int lock;bool running,paused,dfs_blocked,checksum_enabled,queues_stopped,queues_paused,pe_active;
 u32 scheduler_saved,pe_producer,pe_command,occupied,queue_wakes;
 struct {u8 *memory;}pe;void *hw;
};
'''
source += ''.join(function('tx.c', name) for name in ('rtl_ax_tx_pause', 'rtl_ax_tx_dfs_block', 'rtl_ax_tx_open'))
source += r'''
#define RTL_AX_H2C_DATA 0
#define WRITE_ONCE(p,v) ((p)=(v))
static unsigned dfs_region;static bool inject_radar;
struct rtl_ax_ap {
 struct rtl_ax_dfs dfs;struct rtl_ax_io io;struct rtl_ax_tx *tx;
 struct {unsigned primary,bandwidth;}channel;
 unsigned dfs_domain,chip;bool radar_required,ap_started,beacon_enabled;
 int command_lock,ap_result;
 struct {unsigned valid;}security_roles[1];
};
struct ieee80211_hw {struct rtl_ax_ap *radio;};
struct ieee80211_vif {u8 addr[6];};
struct ieee80211_bss_conf {unsigned beacon_int,dtim_period;struct {bool enabled;unsigned color;}he_bss_color;};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static int rtl_ax_self_role_prepare(struct rtl_ax_ap *r,u8 *addr,unsigned bi,unsigned dtim){(void)r;(void)addr;(void)bi;(void)dtim;return 0;}
static void rtl_ax_security_dctl(void *p,unsigned id,void *role){(void)p;(void)id;(void)role;}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned a,unsigned b,unsigned c,void *p,unsigned bytes,bool ack){(void)r;(void)q;(void)a;(void)b;(void)c;(void)p;(void)bytes;(void)ack;return 0;}
static int rtl_ax_ap_template(struct ieee80211_hw *hw,struct ieee80211_vif *vif,struct ieee80211_bss_conf *conf){
 (void)vif;(void)conf;if(inject_radar){hw->radio->dfs.detected=true;rtl_ax_tx_dfs_block(hw->radio->tx,true);}return 0;
}
static void rtl_ax_port0_color(const struct rtl_ax_io *p,unsigned v){(void)p;(void)v;}
static void rtl_ax_receive_mode(const struct rtl_ax_io *p,unsigned v){(void)p;(void)v;}
'''
source += ''.join(function('pci.c', name) for name in ('rtl_ax_dfs_stop_locked', 'rtl_ax_dfs_start_locked'))
source += function('monitor.h', 'rtl_ax_ap_start')
source += r'''
void lifecycle(void){
 struct rtl_ax_tx tx={.io=&io,.scheduler_saved=0x3ffff};
 struct rtl_ax_ap radio={.io=io,.tx=&tx,.chip=RTL_AX_8852C,.dfs_domain=1,.radar_required=true,.channel={100,3}};
 struct ieee80211_hw hw={.radio=&radio};struct ieee80211_vif vif={};struct ieee80211_bss_conf conf={};
 memset(regs,0,sizeof(regs));regs[0xc398/4]=0x3ffff;used=0;
 assert(!rtl_ax_tx_pause(&tx));
 assert(!rtl_ax_dfs_start_locked(&radio));
 assert(radio.dfs.enabled&&radio.dfs.hardware_enabled&&radio.dfs.in_cac);
 rtl_ax_tx_open(&tx);assert(!regs[0xc398/4]);
 assert(!rtl_ax_ap_start(&hw,&vif,&conf));
 assert(radio.ap_started&&!radio.dfs.in_cac&&regs[0xc398/4]==0x3ffff);
 used=0;radio.ap_started=false;rtl_ax_tx_dfs_block(&tx,true);inject_radar=true;
 assert(rtl_ax_ap_start(&hw,&vif,&conf)==-EBUSY);
 assert(!radio.ap_started&&!regs[0xc398/4]&&radio.ap_result==-EBUSY);
 inject_radar=false;used=0;timeout=true;
 assert(rtl_ax_dfs_stop_locked(&radio)==-ETIMEDOUT);
 assert(!radio.dfs.enabled&&radio.dfs.hardware_enabled);timeout=false;
 assert(!rtl_ax_dfs_stop_locked(&radio));assert(!radio.dfs.hardware_enabled);
 radio.dfs_domain=0;assert(rtl_ax_dfs_start_locked(&radio)==-EOPNOTSUPP);
}
void ownership(void){
 const u32 queues=0x3ffff;struct rtl_ax_tx tx={.io=&io};
 memset(regs,0,sizeof(regs));regs[0xc398/4]=queues;used=0;
 rtl_ax_tx_dfs_block(&tx,true);assert(!regs[0xc398/4]);
 assert(!rtl_ax_tx_pause(&tx));rtl_ax_tx_open(&tx);assert(!regs[0xc398/4]);
 rtl_ax_tx_dfs_block(&tx,false);assert(regs[0xc398/4]==queues);
 assert(!rtl_ax_tx_pause(&tx));rtl_ax_tx_dfs_block(&tx,true);rtl_ax_tx_dfs_block(&tx,false);
 assert(!regs[0xc398/4]);rtl_ax_tx_open(&tx);assert(regs[0xc398/4]==queues);
 used=0;regs[0x10000/4]=BIT(31);timeout=true;assert(rtl_ax_dfs_hardware(&io,false)==-ETIMEDOUT);
 assert(regs[0x10000/4]&BIT(31));timeout=false;
 for(unsigned start=0;start<256;start+=127){
  unsigned end=(start+7)&255,epoch=end|256;
  for(unsigned i=0;i<7;i++)assert(rtl_ax_rx_epoch_old(&epoch,(start+i)&255));
  assert(!rtl_ax_rx_epoch_old(&epoch,end));
  for(unsigned i=0;i<8192;i++)assert(!rtl_ax_rx_epoch_old(&epoch,i&255));
 }
}
'''
vectors = 0
with tempfile.TemporaryDirectory(prefix='rtl-ax-dfs-') as temporary:
    temporary = Path(temporary)
    (temporary / 'native.c').write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-I' + str(driver), str(temporary / 'native.c'), '-o', str(temporary / 'native.so')], check=True)
    native = ctypes.CDLL(str(temporary / 'native.so'))
    native.detect.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]

    def compare(region, primary, bandwidth, records, seconds=100, reset=True):
        global vectors
        if reset:
            initialize(region, primary, bandwidth)
            native.init(region, primary, bandwidth)
        word(bb + 3116, seconds)
        oracle.uc.mem_write(data, b''.join(records))
        oracle.uc.mem_write(report, struct.pack('<QHB5x', data, len(records), 0))
        expected = oracle.call('halbb_radar_detect', bb, report, instruction_budget=500000)
        actual = native.detect(b''.join(records), len(records), seconds)
        assert actual == expected, (region, primary, bandwidth, records, seconds, expected, actual)
        vectors += 1

    for region, primary in ((1, 100), (2, 52), (2, 100), (3, 100), (4, 100)):
        for bandwidth in (2, 3):
            for segment in ((0, 1) if bandwidth == 3 else (0,)):
                for width, interval, chirp in ((5, 57, 0), (4, 30, 0), (40, 10, 0),
                                               (70, 12, 0), (300, 60, 1), (110, 16, 1), (700, 50, 0)):
                    for count in (4, 8, 12, 20):
                        compare(region, primary, bandwidth, [pulse(width, interval, i, chirp, segment) for i in range(count)])
    for region, primary in ((1, 36), (2, 36), (3, 36), (4, 36)):
        for segment in (0, 1):
            compare(region, primary, 3, [pulse(5, 57, i, segment=segment) for i in range(20)])
    # Sequence wrap, gaps, filtered pulses and an interleaved two-segment batch.
    for bandwidth in (2, 3):
        for sequence in ([i % 128 for i in range(122, 142)], list(range(0, 40, 2))):
            compare(1, 100, bandwidth, [pulse(5, 57, i) for i in sequence])
        compare(1, 100, bandwidth, [pulse(5, 1 if i < 5 else 57, i) for i in range(25)])
    compare(1, 100, 3, [pulse(5, 57, i, segment=s) for i in range(20) for s in (0, 1)])
    # Stateful long-pulse accumulation and12-second expiry; six reports of3 chirps.
    for region in (1, 2):
        for segment in (0, 1):
            for batch, seconds in enumerate((100, 101, 102, 114, 115, 116)):
                compare(region, 100, 3, [pulse(300, 60, i, 1, segment) for i in range(3)],
                        seconds, reset=not batch)

    oracle.word(adapter, bus)
    oracle.word(bus, ops)
    for offset, name in ((0, 'read8'), (8, 'write8'), (32, 'read32'), (40, 'write32')):
        oracle.word(ops + offset, oracle.callback(name))
    oracle.word(adapter + 1496, bus + 0x1000)
    oracle.word(bus + 0x1000 + 224, oracle.functions['get_bbrpt_dle_cfg_8852c'])
    word(bb + 9456, 0)
    word(bb + 9460, 1 << 31)
    for enabled in (True, False):
        registers.clear()
        registers.update({0x9630: 0xd41, 0x10000: 0x8580801f})
        writes.clear()
        oracle.uc.mem_write(config, struct.pack('<I5B', 2, enabled, 0, 1, enabled, 3))
        assert not oracle.call('cfg_dfs', adapter, config)
        oracle.call('halbb_radar_detect_enable' if enabled else 'halbb_radar_detect_disable', bb)
        actual = (ctypes.c_uint32 * 64)()
        count = native.hardware(enabled, actual)
        assert list(zip(actual[:count * 2:2], actual[1:count * 2:2])) == writes
    native.ownership()
    native.lifecycle()

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              classifier_vectors=vectors, classification='actual relocated ARM64 factory functions',
              profiles=['FCC', 'MKK W53', 'MKK W56', 'ETSI', 'KCC'],
              hardware='MAC/BB enable/disable writes equal factory cfg_dfs and radar_detect_enable/disable',
              boundaries=['RFK/DFS queue ownership', 'report engine timeout propagates', 'ring epoch wrap and expiry',
                          'CAC keeps hardware TX blocked', 'AP start releases TX', 'radar during AP commands prevents start'],
              hardware_device=False, full_test_suite=False)
(root / 'manifests/radio-dfs-contract.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
