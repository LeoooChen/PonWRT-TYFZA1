#!/usr/bin/env python3
"""Execute actual monitor/TX functions with an exactly 20-byte BAR allocation."""
from pathlib import Path
import argparse
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
tx = (driver / 'tx.c').read_text()
monitor = (driver / 'monitor.h').read_text()
kernel = root / 'sources/openwrt/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.55'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pe', type=Path, help='linked PE artifact directory')
parser.add_argument('--stock', type=Path, help='original rtk_wifi6.ko for actual C/XB encoder comparison')
args = parser.parse_args()


def function(source, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


code = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
typedef u16 __le16;typedef u32 dma_addr_t;
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT BIT
#define READ_ONCE(x) (x)
#define le16_to_cpu(x) (x)
#define cpu_to_le16(x) (x)
#define lower_32_bits(x) ((u32)(x))
#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_WP_BYTES 128
#define RTL_AX_TX_WP_ADDRESS_MAX 10
#define RTL_AX_TX_WP_SEQUENCE_BYTES 8
#define RTL_AX_TX_WP_ADDRESS_BYTES 6
#define RTL_AX_TX_WD_PER_RING 264
#define PE_PROBE_MAILBOX 0x2000
#define ETH_HLEN 14
#define ETH_ALEN 6
#define IEEE80211_NUM_TIDS 16
#define IEEE80211_QOS_CTL_LEN 2
#define IEEE80211_HT_CTL_LEN 4
#define IEEE80211_QOS_CTL_TID_MASK 15
#define IEEE80211_QOS_CTL_A_MSDU_PRESENT 128
#define IEEE80211_KEY_FLAG_PAIRWISE BIT(0)
#define IEEE80211_TX_CTL_HW_80211_ENCAP BIT(0)
#define IEEE80211_TX_CTL_AMPDU BIT(1)
#define IEEE80211_TX_CTL_NO_ACK BIT(2)
#define IEEE80211_TX_CTL_SEND_AFTER_DTIM BIT(3)
#define IEEE80211_TX_CTL_NO_PS_BUFFER BIT(4)
#define IEEE80211_TX_CTL_INJECTED BIT(5)
#define IEEE80211_TX_CTL_RATE_CTRL_PROBE BIT(6)
#define IEEE80211_TX_CTL_USE_MINRATE BIT(7)
#define IEEE80211_TX_CTL_TX_OFFCHAN BIT(8)
#define IEEE80211_TX_CTRL_RATE_INJECT BIT(0)
#define IEEE80211_TX_CTRL_PORT_CTRL_PROTO BIT(1)
#define IEEE80211_TX_CTRL_DONT_USE_RATE_MASK BIT(2)
#define IEEE80211_TX_CTRL_PS_RESPONSE BIT(3)
#define IEEE80211_TX_CTRL_FAST_XMIT BIT(4)
#define IEEE80211_TX_RC_VHT_MCS BIT(0)
#define IEEE80211_TX_RC_MCS BIT(1)
#define IEEE80211_TX_RC_SHORT_GI BIT(2)
#define IEEE80211_TX_RC_160_MHZ_WIDTH BIT(3)
#define IEEE80211_TX_RC_80_MHZ_WIDTH BIT(4)
#define IEEE80211_TX_RC_40_MHZ_WIDTH BIT(5)
#define RTL_AX_8852C 1
#define NL80211_IFTYPE_AP 3
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define dma_wmb() ((void)0)
#define atomic64_inc(p) (++*(p))
#define atomic64_inc_return(p) (++*(p))
struct ieee80211_hdr {u16 frame_control,duration_id;u8 addr1[6],addr2[6],addr3[6];u16 seq_ctrl;} __attribute__((packed));
struct ieee80211_bar {u16 frame_control,duration;u8 ra[6],ta[6];u16 control,start_seq_num;} __attribute__((packed));
struct ieee80211_tx_rate {int idx;unsigned flags,count;};
struct key {unsigned flags,hw_key_idx,keyidx;u64 tx_pn;};
struct vif {unsigned type;};
struct ieee80211_tx_info {unsigned flags;struct {unsigned flags;struct key *hw_key;struct vif *vif;struct ieee80211_tx_rate rates[1];}control;};
struct sk_buff {u8 *data;unsigned len,priority;struct ieee80211_tx_info info;};
#define IEEE80211_SKB_CB(s) (&(s)->info)
struct rtl_ax_station {unsigned macid;};
struct ieee80211_sta {void *drv_priv;struct {struct {unsigned ampdu_density;}ht_cap;}deflink;};
struct ieee80211_tx_control {struct ieee80211_sta *sta;};
struct scatterlist {u32 dma,len;};
struct rtl_ax_tx_mapping {struct scatterlist sg[10];unsigned count,dma_count;};
struct rtl_ax_tx_slot {struct sk_buff *skb;dma_addr_t dma;unsigned bytes,ring,key_index,macid,tid;bool encrypted,pe_sequence,firmware_rate;struct rtl_ax_tx_mapping *mapping;};
struct rtl_ax_io {void *ctx;void (*write16)(void *,unsigned,unsigned);};
struct rtl_ax_tx {bool running,dfs_blocked,pe_active,checksum_enabled,queues_stopped;
 bool peer_qos[128],peer_authorized[128],peer_asleep[128],peer_offloaded[128];u32 peer_rates[128];
 int lock;struct {void *memory;}pe;u8 *pages,*headers,*ring;u32 pages_dma;
 unsigned pe_producer,producer[6],wd_producer[6],pending[256],occupied,last_sequence,last_words[14];
 struct rtl_ax_tx_slot slots[256];const struct rtl_ax_io *io;
 void (*peer_ready)(void *);void *peer_ready_context;
 u64 stopped,full,hardware_pn,encrypted,pe_ethernet,scatter_packets,scatter_segments,submitted,traffic_bytes;
 u64 linearized_packets,amsdu_packets,fast_xmit,prepare_ns,ampdu_submitted,submitted_ring[6],queue_stops;
 u32 last_eth_dma;u8 last_eth[14];};
struct rtl_ax_ap {struct rtl_ax_tx *tx;unsigned chip,tx_unimplemented;struct {unsigned aggregation[16];}tx_ba[128];struct {int n_bitrates;struct {unsigned hw_value;}bitrates[12];}band;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};
static unsigned freed,maps,doorbells;
static bool ieee80211_is_ctl(u16 f){return (f&12)==4;}
static bool ieee80211_is_data(u16 f){return (f&12)==8;}
static bool ieee80211_is_data_present(u16 f){return ieee80211_is_data(f)&&!(f&0x40);}
static bool ieee80211_is_mgmt(u16 f){return !(f&12);}
static bool ieee80211_is_data_qos(u16 f){return ieee80211_is_data(f)&&(f&0x80);}
static bool ieee80211_is_back_req(u16 f){return (f&0xfc)==0x84;}
static bool ieee80211_is_ext(u16 f){return (f&12)==12;}
static bool ieee80211_has_a4(u16 f){return (f&0x300)==0x300;}
static bool ieee80211_has_order(u16 f){return f&0x8000;}
static bool ieee80211_has_morefrags(u16 f){return f&0x400;}
static bool ieee80211_has_fromds(u16 f){return f&0x200;}
static bool ieee80211_has_tods(u16 f){return f&0x100;}
static u8 *ieee80211_get_qos_ctl(struct ieee80211_hdr *h){return (u8 *)h+24;}
static unsigned ieee80211_rate_get_vht_nss(const struct ieee80211_tx_rate *r){return ((unsigned)r->idx>>4)+1;}
static unsigned ieee80211_rate_get_vht_mcs(const struct ieee80211_tx_rate *r){return r->idx&15;}
static bool is_multicast_ether_addr(const u8 *p){return *p&1;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void rtl_ax_store32(void *p,u32 v){memcpy(p,&v,4);}
#define rtl_ax_ap_read32 get_unaligned_le32
static u64 ktime_get_ns(void){return 0;}
static bool rtl_ax_datapath_profile;
static void ieee80211_stop_queues(struct ieee80211_hw *hw){(void)hw;assert(0);}
static void ieee80211_free_txskb(struct ieee80211_hw *hw,struct sk_buff *s){(void)hw;(void)s;freed++;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static unsigned rtl_ax_tx_basic_rate(struct rtl_ax_ap *r,struct ieee80211_sta *s,struct ieee80211_tx_info *i){(void)r;(void)s;(void)i;return 4;}
static unsigned find_first_zero_bit(const unsigned *p,unsigned n){(void)p;(void)n;return 0;}
static void __set_bit(unsigned n,unsigned *p){p[n]=1;}
static void ni_route_sleep(struct rtl_ax_tx *tx,unsigned id){(void)tx;(void)id;assert(0);}
static int rtl_ax_tx_map(struct rtl_ax_tx *tx,struct sk_buff *s,struct rtl_ax_tx_slot *slot,bool *linearized,bool separate){
 (void)tx;(void)linearized;assert(!separate);assert(s->len==20);slot->dma=0x09000000;maps++;return 1;}
static void rtl_ax_tx_unmap(struct rtl_ax_tx *tx,const struct rtl_ax_tx_slot *slot){(void)tx;(void)slot;assert(0);}
static void rtl_ax_security_tx(u8 *page,unsigned id,unsigned key,u64 pn){(void)page;(void)id;(void)key;(void)pn;assert(0);}
static void write16(void *ctx,unsigned reg,unsigned value){(void)ctx;assert(reg==0x1078&&value==1);doorbells++;}
#define for_each_sg(list,sg,n,i) for((i)=0;(i)<(n)&&((sg)=&(list)[i],1);(i)++)
#define sg_dma_address(s) ((s)->dma)
#define sg_dma_len(s) ((s)->len)
'''
code += function((kernel / 'net/wireless/util.c').read_text(), 'ieee80211_hdrlen')
wp = (driver / 'tx-format.h').read_text()
for name in ('rtl_ax_tx_dma_channel', 'rtl_ax_tx_wp_sequence', 'rtl_ax_tx_wp_address', 'rtl_ax_tx_wp_addresses'):
    code += function(wp, name)
# Only wp_addresses needs these constants/helpers from tx-format.h.
code = '#define RTL_AX_TX_WP_FRAGMENT_BYTES 2047\n#define min(a,b) ((a)<(b)?(a):(b))\n' + code
arrays = tx[tx.index('static const u8 rtl_ax_tx_channels'):tx.index('\nstruct rtl_ax_tx_mapping')]
code += '#define RTL_AX_TX_RINGS 6\n' + arrays
code += function((driver / 'transport.h').read_text(), 'rtl_ax_txbd')
for name in ('rtl_ax_tx_wd', 'rtl_ax_tx_checksum', 'rtl_ax_tx_raw_fw_rate', 'rtl_ax_tx_submit'):
    code += function(tx, name)
code += function(monitor, 'rtl_ax_monitor_tx')
code += r'''
int main(void){
 static u8 memory[0x20000],pages[6*264*128],headers[384*128],ring[6*256*8];
 const u8 original[20]={0x84,0,0,0,2,1,2,3,4,5,2,6,7,8,9,10,4,0x60,0xc0,0xab};
 struct rtl_ax_station private={7};struct ieee80211_sta sta={.drv_priv=&private,.deflink={.ht_cap={7}}};
 const struct rtl_ax_io io={.write16=write16};
 for(unsigned pe=0;pe<2;pe++)for(unsigned ampdu=0;ampdu<2;ampdu++){
  memset(memory,0,sizeof(memory));memset(pages,0,sizeof(pages));
  struct rtl_ax_tx t={.running=true,.pe_active=pe,.pe={memory},.pages=pages,.headers=headers,.ring=ring,.pages_dma=0x08000000,.io=&io};
  t.peer_authorized[7]=true;t.peer_rates[7]=BIT(31)|BIT(22)|BIT(20)|0x180;
  struct rtl_ax_ap r={.tx=&t,.chip=RTL_AX_8852C};r.tx_ba[7].aggregation[0]=64;
  struct ieee80211_hw hw={&r};struct ieee80211_tx_control control={&sta};
  struct sk_buff skb={.data=malloc(20),.len=20,.priority=7,.info={.flags=ampdu?IEEE80211_TX_CTL_AMPDU:0}};
  assert(skb.data);memcpy(skb.data,original,20);
  assert(ieee80211_hdrlen(0x84)==16 && sizeof(struct ieee80211_bar)==20);
  unsigned before=freed;rtl_ax_monitor_tx(&hw,&control,&skb);
  assert(freed==before && t.submitted==1 && t.submitted_ring[4]==1);
  assert(t.slots[0].ring==4 && t.slots[0].macid==7 && t.slots[0].tid==16);
  assert(!t.slots[0].pe_sequence&&!t.slots[0].firmware_rate&&!t.ampdu_submitted);
  assert(!memcmp(skb.data,original,20));
  if(pe){
   u32 *q=(void *)(memory+0x4000);
   assert(q[1]==(4|1<<16)&&q[2]==(20|16<<16|1<<24));
   assert(q[3]==4&&q[4]==7U<<24&&!q[5]&&!q[6]&&!q[7]);
   assert(q[8]==0x09000000&&q[9]==20);
   assert(fwrite(q,1,128,stdout)==128);
  }else{
   u8 *wd=pages+4*264*128;
   assert(((get_unaligned_le32(wd)>>11)&31)==8);
   assert(get_unaligned_le32(wd+8)==(7U<<24|0x12<<17|20));
   assert(!(get_unaligned_le32(wd+12)&~BIT(13))&&!get_unaligned_le32(wd+36));
   assert(!get_unaligned_le32(wd+48));
   assert((wd[64]|wd[65]<<8)==(20|0x8000)&&get_unaligned_le32(wd+66)==0x09000000);
   assert(fwrite(wd,1,128,stdout)==128);
  }
  skb.len=19;before=freed;rtl_ax_monitor_tx(&hw,&control,&skb);
  assert(freed==before+1&&t.submitted==1);free(skb.data);
 }
 assert(maps==4&&doorbells==2);
}
'''
with tempfile.TemporaryDirectory(prefix='radio-tx-bar-') as directory:
    c, exe = Path(directory) / 'bar.c', Path(directory) / 'bar'
    c.write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    output = subprocess.check_output([str(exe)])
assert len(output) == 512
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_READ
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA)

symbols = {fields[2]: int(fields[0], 16)
           for line in subprocess.check_output(['llvm-nm-18', str(args.pe / 'pe-wd.elf')], text=True).splitlines()
           if len(fields := line.split()) == 3}
native = []
for index in range(2):
    arm = output[index * 128:(index + 1) * 128]
    request = output[(index + 2) * 128:(index + 3) * 128]
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    base, mailbox, ring, pages, data = 0x04000000, 0x04002000, 0x05000000, 0x05100000, 0x09000000
    for address, size in ((0x1fc00000, 0x10000), (base, 0x20000), (ring, 0x10000), (pages, 0x32000), (data, 0x1000)):
        u.mem_map(address, size)
    u.mem_write(0x1fc00000, (args.pe / 'pe-tx.bin').read_bytes())
    u.mem_write(base + 0x4000, request)
    original = bytes.fromhex('8400000002010203040502060708090a0460c0ab')
    u.mem_write(data, original)

    def put(address, value):
        u.mem_write(address, struct.pack('<I', value))

    def read_payload(uc, access, address, size, value, unused):
        raise AssertionError(('PE unexpectedly parsed BAR payload', hex(address)))

    u.hook_add(UC_HOOK_MEM_READ, read_payload, begin=data, end=data + 0xfff)
    for field, value in ((11, ring), (12, pages)):
        put(mailbox + field * 4, value)
    for register, value in ((UC_MIPS_REG_A0, mailbox), (UC_MIPS_REG_A1, base),
                            (UC_MIPS_REG_A2, base + 0x4000), (UC_MIPS_REG_A3, base + 0x7000),
                            (UC_MIPS_REG_SP, base + 0x1fff0)):
        u.reg_write(register, value | 0xa0000000)
    u.reg_write(UC_MIPS_REG_RA, 0x9fc01ff0)
    u.emu_start(symbols['pe_build_tx'], 0x9fc01ff0, count=100000)
    wd = bytes(u.mem_read(pages + 4 * 264 * 128, 128))
    assert wd[64:70] == arm[64:70]
    for offset in (0, 4, 8, 28, 32, 36, 40, 48):
        assert struct.unpack_from('<I', wd, offset) == struct.unpack_from('<I', arm, offset), offset
    assert struct.unpack_from('<I', wd, 12)[0] & 0x1fff == 0
    assert bytes(u.mem_read(data, 20)) == original
    native.append(arm)

if args.stock:
    sys.path.insert(0, str(root / 'tools'))
    from arm64_module_oracle import ModuleOracle
    names = ['_phl_fill_tx_meta_data'] + [base + '_' + chip for chip in ('8852c', '8192xb')
                                       for base in ('txdes_proc_mgnt', 'mac_txdesc_len')]

    def callback(name, values):
        if name == 'platform_memcpy':
            oracle.uc.mem_write(values[1], bytes(oracle.uc.mem_read(values[2], values[3])))
            return 0
        assert name == 'printk', name
        return 0

    oracle = ModuleOracle(args.stock, names, callback)
    adapter, ops, hwinfo, metadata, result, request = [0x06000000 + n * 0x10000 for n in range(6)]
    for address in (adapter, ops, hwinfo, metadata, result, request):
        oracle.uc.mem_map(address, 0x10000)
    oracle.word(adapter + 24, ops)
    oracle.word(adapter + 88, hwinfo)
    oracle.word(ops + 88, oracle.callback('platform_memcpy'))
    oracle.uc.mem_write(hwinfo + 8, struct.pack('<I', 2))
    for chip, rate in (('8852c', 4), ('8192xb', 0)):
        data = bytearray(96)
        struct.pack_into('<IIH', data, 0, 1, 8, 20)
        struct.pack_into('<H', data, 14, 7)
        data[20], data[33], data[34] = 8, 0x28, 1
        struct.pack_into('<Q', data, 32, int.from_bytes(data[32:40], 'little') | rate << 51)
        struct.pack_into('<Q', data, 40, 0x1840)
        oracle.uc.mem_write(request, bytes(256))
        oracle.uc.mem_write(request + 32, bytes(data))
        oracle.uc.mem_write(request + 108, b'\x01')
        oracle.call('_phl_fill_tx_meta_data', request, 20)
        oracle.uc.mem_write(metadata, bytes(oracle.uc.mem_read(request + 32, 96)))
        assert oracle.call('mac_txdesc_len_' + chip, adapter, metadata) == 56
        assert oracle.call('txdes_proc_mgnt_' + chip, adapter, metadata, result, 56) == 0
        stock = bytes(oracle.uc.mem_read(result, 56))
        # Factory PHL page/WP-offset bits differ from native truncated WP.
        # Compare header length, total length, queue/MACID, sequence and aggregation.
        assert (struct.unpack_from('<I', stock)[0] >> 11 & 31) == 8
        assert struct.unpack_from('<I', stock, 8)[0] == struct.unpack_from('<I', native[0], 8)[0]
        assert struct.unpack_from('<I', stock, 12)[0] == 0
        assert struct.unpack_from('<I', stock, 36)[0] == 0
        assert struct.unpack_from('<I', stock, 28)[0] == (1 << 31 | rate << 16)
    print('Original C/XB encoders: header16, packet20, MGNT queue/MACID7, no sequence/AMPDU match.')
print('BAR: monitor+ARM+linked PE, exact20/short19, no seq_ctrl read/data aggregation, opaque DMA and unchanged BAR SSN passed.')
