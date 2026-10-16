#!/usr/bin/env python3
"""Execute Linux BAR head movement, native RX seed, and the linked PE."""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pe_directory', type=Path)
parser.add_argument('--before', action='store_true')
args = parser.parse_args()
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'rx.c').read_text()
if args.before:
    source = subprocess.check_output(['git', 'show', 'f765269:' + str((driver / 'rx.c').relative_to(root))], cwd=root, text=True)
kernel = Path(subprocess.check_output(['python3', 'port/tools/project_paths.py', '--kernel-directory', str(root / 'sources/openwrt')], cwd=root, text=True).strip())
linux_rx = (kernel / 'net/mac80211/rx.c').read_text()


def function(text, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', text, re.M).start()
    at = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define WLAN_CIPHER_SUITE_CCMP 4
#define WLAN_STA_AUTHORIZED 1
#define IEEE80211_RX_DEFERRED_RELEASE 1
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define spin_lock_bh(p) ((void)(p))
#define spin_unlock_bh(p) ((void)(p))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define lockdep_assert_held(p) ((void)(p))
#define rcu_dereference(p) (p)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define local_bh_disable() ((void)0)
#define local_bh_enable() ((void)0)
#define dma_wmb() ((void)0)
struct sk_buff_head {unsigned count;};
struct ieee80211_rx_status {unsigned rx_flags;};
struct sk_buff {struct ieee80211_rx_status status;};
#define IEEE80211_SKB_RXCB(p) (&(p)->status)
static bool skb_queue_empty(struct sk_buff_head *p){assert(!p->count);return true;}
static void __skb_queue_purge(struct sk_buff_head *p){assert(!p->count);}
static struct sk_buff *__skb_dequeue(struct sk_buff_head *p){assert(!p->count);return 0;}
static void __skb_queue_tail(struct sk_buff_head *p,struct sk_buff *skb){(void)p;(void)skb;assert(false);}
struct tid_ampdu_rx {int reorder_lock;u16 head_seq_num,stored_mpdu_num,buf_size;uint64_t reorder_buf_filtered;struct sk_buff_head reorder_buf[64];};
static bool ieee80211_rx_reorder_ready(struct tid_ampdu_rx *ba,int i){assert(!ba->reorder_buf[i].count);return false;}
static bool ieee80211_sn_less(u16 a,u16 b){return ((a-b)&4095)>2048;}
static u16 ieee80211_sn_inc(u16 a){return (a+1)&4095;}
struct ieee80211_local {int rx_path_lock;};
struct ieee80211_sub_if_data {struct ieee80211_local *local;};
struct ieee80211_sta {int unused;};
struct sta_info {struct ieee80211_sta sta;struct ieee80211_sub_if_data *sdata;struct {struct tid_ampdu_rx *tid_rx[16];}ampdu_mlme;};
#define test_sta_flag(p,f) ((void)(p),(void)(f),true)
struct ieee80211_key_conf {unsigned cipher;};
struct ieee80211_key {struct ieee80211_key_conf conf;struct sta_info *sta;u16 rx_shortcut_valid_tids,rx_shortcut_next_seq[16];struct {struct {u8 rx_pn[16][6];}ccmp;}u;};
struct ieee80211_hw {int unused;};
struct rtl_ax_rx_packet {const u8 *payload;unsigned bytes;u8 type;};
struct rtl_ax_rx {void *rpq_memory,*uplink_states;unsigned uplink_wifi_id;int uplink_lock;struct ieee80211_hw *hw;struct ieee80211_key_conf *keys[128];u16 uplink_pending_tids[128];};
static struct sta_info station;
static u16 get_unaligned_le16(const void *p){u16 v;memcpy(&v,p,2);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(struct ieee80211_hw *hw,const u8 *peer,void *vif){(void)hw;(void)peer;(void)vif;return &station.sta;}
'''
wrapper = r'''
unsigned native_seed(void *states,const u8 *bar,unsigned bytes,unsigned old_next,unsigned ssn,unsigned pn){
 u8 memory[0x20000]={};struct ieee80211_local local={};struct ieee80211_sub_if_data sdata={&local};
 struct ieee80211_key key={.conf={.cipher=WLAN_CIPHER_SUITE_CCMP},.sta=&station};
 struct tid_ampdu_rx ba={.head_seq_num=old_next,.buf_size=64};struct sk_buff_head frames={};
 unsigned tid=(bar[0]&0xfc)==0x84?bar[17]>>4:bar[24]&15;
 memset(&station,0,sizeof(station));station.sdata=&sdata;station.ampdu_mlme.tid_rx[tid]=&ba;
 key.rx_shortcut_valid_tids=BIT(tid);key.rx_shortcut_next_seq[tid]=old_next;key.u.ccmp.rx_pn[tid][5]=pn;
 struct rtl_ax_rx rx={.rpq_memory=memory,.uplink_states=states,.uplink_wifi_id=1};
 struct rtl_ax_rx_packet packet={.payload=bar,.bytes=bytes};
 rx.keys[2]=&key.conf;memcpy(memory+0xe410,bar+10,6);
 /* Same release helper and SSN passed by ieee80211_rx_h_ctrl for BAR.
  * Empty reorder slots, locks and station lookup are the modeled boundary. */
 ieee80211_release_reorder_frames(&sdata,&ba,ssn,&frames);
 rtl_ax_rx_uplink_seed(&rx,&packet);
 return ba.head_seq_num;
}
'''
saved_argv = sys.argv[:]
sys.argv = [saved_argv[0], str(args.pe_directory)]
ns = {'__file__': str(root / 'scripts/test_pe_rxq_backpressure.py')}
exec(compile((root / 'scripts/test_pe_rxq_backpressure.py').read_text().split('m=Machine();retained=[]')[0], ns['__file__'], 'exec'), ns)
sys.argv = saved_argv
embedded = b''.join(struct.pack('<I', int(word, 16)) for word in re.findall(r'^0x([0-9a-fA-F]+),', (driver / 'pe-tx-code.h').read_text(), re.M))
assert ns['binary'] == embedded
B, MB, ST, VP, NI = (ns[n] for n in ('B', 'MB', 'ST', 'VP', 'NI'))
peer, ap, dst = (bytes.fromhex(s) for s in ('067afb78b287', '02e04c076805', '0050c2010200'))
functions = ''.join(function(linux_rx, n) for n in ('ieee80211_release_reorder_frame', 'ieee80211_release_reorder_frames', 'ieee80211_rx_shortcut_snapshot'))
functions += ''.join(function(source, n) for n in ('rtl_ax_rx_uplink_publish', 'rtl_ax_rx_uplink_seed'))
rows = []
with tempfile.TemporaryDirectory(prefix='rx-bar-window-', dir=root / 'artifacts') as temp:
    p = Path(temp)
    (p / 'case.c').write_text(common + functions + wrapper)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-shared', '-fPIC', str(p / 'case.c'), '-o', str(p / 'case.so')], check=True)
    lib = ctypes.CDLL(str(p / 'case.so'))
    lib.native_seed.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    lib.native_seed.restype = ctypes.c_uint
    for old_next, requested, tid in ((0x123, 0x126, 3), (4094, 2, 0), (0x123, 0x123, 0), (0x123, 0x122, 0)):
        m = ns['Machine']()
        m.u.mem_write(MB + 64, ap)
        m.u.mem_write(B + 0xe410, peer)
        m.put(B + 0x11a08, 1)
        for i, v in ((88, VP | 0xa0000000), (89, NI), (93, 1), (106, ST)):
            m.put(MB + i * 4, v)
        m.put(ST + 0x10008, 1 << 31 | 1 << 8)
        state_offset = 1024 + tid * 32
        other_offset = 1024 + ((tid + 1) & 15) * 32
        m.u.mem_write(ST + state_offset, struct.pack('<8I', 1 << 31 | old_next, 1, 0, 1, 100, 1, 2, 0))
        m.put(ST + other_offset, 0x87654321)
        m.put(ST + 1536, 0x12345678)
        bar = struct.pack('<HH', 0x84, 0) + ap + peer + struct.pack('<HH', 4 | tid << 12, requested << 4) + bytes(4)
        m.arrive(bar)
        m.call()
        assert m.get(MB + 94 * 4) == 1 and m.get(MB + 175 * 4) == 1
        states = ctypes.create_string_buffer(bytes(m.u.mem_read(ST, 0x20000)))
        head = lib.native_seed(states, bar, len(bar), old_next, requested, 1)
        m.u.mem_write(ST, states.raw[:0x20000])
        assert bytes(m.u.mem_read(ST + state_offset + 4, 28)) == struct.pack('<7I', 1, 0, 1, 100, 1, 2, 0)
        assert m.get(ST + other_offset) == 0x87654321 and m.get(ST + 1536) == 0x12345678
        m.arm_ack()
        m.call()
        header = struct.pack('<HH', 0x4188, 0) + ap + peer + dst + struct.pack('<H', old_next << 4) + bytes([tid, 0])
        iv = bytes.fromhex('0200002000000000')
        ip = bytearray(20)
        ip[0], ip[9] = 0x45, 17
        m.arrive(header + iv + bytes.fromhex('aaaa030000000800') + ip + bytes(8) + bytes(12), True)
        m.call()
        forwarded = m.get(MB + 90 * 4)
        head_moved = head != old_next
        assert forwarded == int(args.before or not head_moved)
        m.inventory()
        old_queued = bool(m.get(MB + 94 * 4) - 1)
        resumed = None
        if head_moved and not args.before:
            # A below-window frame stays on ARM. The next in-window frame
            # must also enter ARM once, then its accepted snapshot can resume PE.
            m.arm_ack()
            m.call()
            in_window = bytearray(header + iv + bytes.fromhex('aaaa030000000800') + ip + bytes(8) + bytes(12))
            struct.pack_into('<H', in_window, 22, head << 4)
            in_window[26] = 10
            m.arrive(bytes(in_window), True)
            m.call()
            assert m.get(MB + 90 * 4) == 0
            states = ctypes.create_string_buffer(bytes(m.u.mem_read(ST, 0x20000)))
            # Upper-stack acceptance is a declared boundary: next SN/head
            # become head+1 and the accepted receive PN becomes10.
            next_sn = (head + 1) & 4095
            lib.native_seed(states, bytes(in_window), len(in_window), next_sn, next_sn, 10)
            m.u.mem_write(ST, states.raw[:0x20000])
            m.arm_ack()
            m.call()
            struct.pack_into('<H', in_window, 22, next_sn << 4)
            in_window[26] = 11
            m.arrive(bytes(in_window), True)
            m.call()
            resumed = m.get(MB + 90 * 4) == 1
            assert resumed
            m.inventory()
        rows.append(dict(TID=tid, old_next_SN=old_next, BAR_SSN=requested, actual_Linux_head=head,
                         old_window_frame_forwarded=bool(forwarded),
                         old_window_frame_queued_to_ARM=old_queued,
                         current_window_PE_resumes_after_accepted_ARM_frame=resumed))
print(json.dumps(dict(native_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                     Linux_rx_source_sha256=hashlib.sha256(linux_rx.encode()).hexdigest(),
                     PE_sha256=hashlib.sha256(ns['binary']).hexdigest(), cases=rows,
                     PN_counters_guard_other_TIDs_and_other_peers_preserved=True,
                     scope='Actual Linux empty-slot BAR release and shortcut snapshot, native publish/seed, and linked PE. Reorder storage is empty; locks, lookup, PCIe, NI and ARM acknowledgement are modeled. Resumption uses a declared accepted ARM frame snapshot. Full mac80211 BAR/decrypt processing and RF are not emulated.'), indent=2))
