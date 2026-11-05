#!/usr/bin/env python3
"""Compare actual RX BA markers; stub skb delivery/timers, retain window advance."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'port/overlay/target/linux/rtl9617c/patches-6.18/220-mac80211-rx-ba-unfiltered-sync.patch'
OPENWRT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'sources/openwrt'
KERNEL = Path(subprocess.check_output([sys.executable, str(ROOT / 'port/tools/project_paths.py'),
                                     '--kernel-directory', str(OPENWRT)], text=True).strip())


def extract(source, name):
    start = source.index('static void ' + name + '(') if 'static void ' + name + '(' in source else source.index('void ' + name + '(')
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


COMMON = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint64_t u64;
#define IEEE80211_NUM_TIDS 16
#define IEEE80211_SN_MODULO 4096
#define IEEE80211_SN_MASK 4095
#define BIT_ULL(n) (1ULL << (n))
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define rcu_dereference(p) (p)
struct event { int kind, a; u64 b; };
struct result { u16 head; u64 bitmap; int n, final, warnings; struct event log[8192]; };
static struct result result;
static int locked, rcu, cases;
static void record(int kind,int a,u64 b) {
 assert(result.n < 8192); result.log[result.n++] = (struct event){kind,a,b};
}
static bool warn(bool condition) { if(condition) {result.warnings++; record(1,0,0);} return condition; }
#define WARN_ON(x) warn(!!(x))
#define WARN_ONCE(x,...) warn(!!(x))
struct sk_buff_head { int count; };
struct tid_ampdu_rx { u16 head_seq_num,buf_size; u64 reorder_buf_filtered; int reorder_lock; };
struct ieee80211_local { struct { int max_rx_aggregation_subframes; } hw; };
struct ieee80211_sub_if_data { struct ieee80211_local *local; };
struct ieee80211_sta { int unused; };
struct sta_info { struct ieee80211_sta sta; struct ieee80211_sub_if_data *sdata;
 struct { struct tid_ampdu_rx *tid_rx[16]; } ampdu_mlme; };
struct ieee80211_rx_data { int security_idx,seqno_idx; };
static bool ieee80211_sn_less(u16 a,u16 b) { return ((a-b)&4095)>2048; }
static void __skb_queue_head_init(struct sk_buff_head *f) { f->count=0; }
static bool ieee80211_rx_data_set_sta(struct ieee80211_rx_data *rx,struct sta_info *sta,int link) {
 (void)rx;(void)sta;(void)link;return true;
}
static void rcu_read_lock(void) { assert(!rcu);rcu=1;record(2,0,0); }
static void rcu_read_unlock(void) { assert(rcu && !locked);rcu=0;record(3,0,0); }
static void spin_lock_bh(int *lock) { (void)lock;assert(rcu && !locked);locked=1;record(4,0,0); }
static void spin_unlock_bh(int *lock) { (void)lock;assert(locked);locked=0;record(5,0,0); }
static void lockdep_assert_held(int *lock) { (void)lock;assert(locked); }
/* Boundary stub: record each released slot, without simulating skb/A-MSDU data. */
static void ieee80211_release_reorder_frame(struct ieee80211_sub_if_data *s,
 struct tid_ampdu_rx *ba,int index,struct sk_buff_head *frames) {
 (void)s;assert(locked);record(6,index,ba->head_seq_num);frames->count++;
 if(ba->reorder_buf_filtered) ba->reorder_buf_filtered &= ~BIT_ULL(index);
 ba->head_seq_num=(ba->head_seq_num+1)&4095;
}
/* Boundary stub: observe invocation and incoming state; no timeout simulation. */
static void ieee80211_sta_reorder_release(struct ieee80211_sub_if_data *s,
 struct tid_ampdu_rx *ba,struct sk_buff_head *frames) {
 (void)s;(void)frames;assert(locked);result.final++;
 record(7,ba->head_seq_num,ba->reorder_buf_filtered);
}
static void ieee80211_rx_handlers(struct ieee80211_rx_data *rx,struct sk_buff_head *frames) {
 (void)rx;assert(rcu && !locked);record(8,frames->count,0);
}
'''

TEST = r'''
typedef void (*marker)(struct ieee80211_sta *,u8,u16,u64,u16);
static struct result run(marker fn,int size,int head,int ssn,int received,u64 prior,u64 filtered) {
 struct ieee80211_local local={.hw.max_rx_aggregation_subframes=size};
 struct ieee80211_sub_if_data sdata={.local=&local};
 struct tid_ampdu_rx ba={.head_seq_num=head,.buf_size=size,.reorder_buf_filtered=prior};
 struct sta_info sta={.sdata=&sdata,.ampdu_mlme.tid_rx={[3]=&ba}};
 memset(&result,0,sizeof(result));assert(!locked && !rcu);
 fn(&sta.sta,3,ssn,filtered,received);
 assert(!locked && !rcu);result.head=ba.head_seq_num;result.bitmap=ba.reorder_buf_filtered;
 return result;
}
static void zero_case(marker fn,int size,int head,int ssn,int received) {
 struct result r=run(fn,size,head,ssn,received,0,0);
 int expected=received>=2048 || ieee80211_sn_less(head,ssn)?ssn:head;
 assert(r.head==expected && !r.bitmap && !r.warnings);
 assert(r.final==(((expected-ssn)&4095)<size));
}
int main(int argc,char **argv) {
 assert(argc>=2);
 if(!strcmp(argv[1],"original-large")) {
  int size=atoi(argv[2]),behind=atoi(argv[3]);
  run(original,size,behind,0,0,0,0);return 0;
 }
 if(!strcmp(argv[1],"early-zero-mutant")) {
  zero_case(early_zero,64,64,0,0);return 0;
 }
 bool large=!strcmp(argv[1],"large");
 int sizes[66],heads[]={0,1,63,4095},received[]={0,2047,2048};
 for(int i=0;i<64;i++)sizes[i]=i+1;sizes[64]=128;sizes[65]=256;
 for(unsigned z=0;z<66;z++) {
  int size=sizes[z];if((size>64)!=large)continue;
  int offsets[]={0,1,-1,size-1,1-size,-size,-size-1,63,64,255,256,2047,2048};
  for(unsigned h=0;h<4;h++)for(unsigned d=0;d<13;d++)for(unsigned r=0;r<3;r++) {
   int head=heads[h],ssn=(head+offsets[d])&4095;
   if(large) { zero_case(fixed,size,head,ssn,received[r]);cases++;continue; }
   u64 mask=size==64?UINT64_MAX:(1ULL<<size)-1;
   u64 patterns[]={0,1,mask,0xaaaaaaaaaaaaaaaaULL&mask};
   for(unsigned p=0;p<4;p++)for(unsigned f=0;f<4;f++) {
    struct result a=run(original,size,head,ssn,received[r],patterns[p],patterns[f]);
    struct result b=run(fixed,size,head,ssn,received[r],patterns[p],patterns[f]);
    assert(!memcmp(&a,&b,sizeof(a)));cases++;
   }
  }
 }
 printf("%s: %d cases passed\n",large?"BA128/256 zero bitmap":"BA1..64 original/fixed state and callback comparison",cases);
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='rx-ba-sync-') as directory:
    temp = Path(directory)
    for relative in ('net/mac80211/rx.c', 'include/net/mac80211.h'):
        target = temp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((KERNEL / relative).read_bytes())
    patched_source = (temp / 'net/mac80211/rx.c').read_text()
    name = 'ieee80211_mark_rx_ba_filtered_frames'
    assert 'bitmap_done:' in extract(patched_source, name), 'Kernel must already include patch220'
    subprocess.run(['patch', '--batch', '-R', '-p1', '-i', str(PATCH)], cwd=temp,
                   check=True, capture_output=True, text=True)
    original_source = (temp / 'net/mac80211/rx.c').read_text()
    subprocess.run(['patch', '--batch', '-p1', '-i', str(PATCH)], cwd=temp,
                   check=True, capture_output=True, text=True)
    fixed_source = (temp / 'net/mac80211/rx.c').read_text()
    assert fixed_source == patched_source
    original = extract(original_source, name)
    fixed = extract(fixed_source, name)
    assert 'bitmap_done:' not in original and 'bitmap_done:' in fixed
    fast_start = fixed.index('\t/* PE shortcut synchronization')
    fast_end = fixed.index('\tfiltered = filtered >> diff;', fast_start)
    fast = fixed[fast_start:fast_end]
    mutant = fixed[:fast_start] + fixed[fast_end:]
    mutant = mutant.replace('\tdiff = ', fast + '\tdiff = ', 1)
    functions = '\n'.join(function.replace(name + '(', replacement + '(', 1)
                          for function, replacement in [(original, 'original'),
                                                         (fixed, 'fixed'), (mutant, 'early_zero')])
    harness = COMMON + extract(original_source, 'ieee80211_release_reorder_frames') + functions + TEST
    (temp / 'test.c').write_text(harness)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    str(temp / 'test.c'), '-o', str(temp / 'test')], check=True)
    output = []
    for mode in ('compare', 'large'):
        result = subprocess.run([str(temp / 'test'), mode], check=True, capture_output=True, text=True)
        output.append(result.stdout.strip())
    for size, behind in [(128, 0), (256, 0), (128, 64), (256, 255)]:
        result = subprocess.run([str(temp / 'test'), 'original-large', str(size), str(behind)],
                                capture_output=True, text=True)
        assert result.returncode and 'shift exponent' in result.stderr, result.stderr
        output.append(f'Original BA{size}, behind{behind}: UBSan rejected invalid shift')
    result = subprocess.run([str(temp / 'test'), 'early-zero-mutant'], capture_output=True, text=True)
    assert result.returncode and 'r.final' in result.stderr, result.stderr
    output.append('Rejected early-zero-before-diff mutant: stale SSN must not call final release')
    print(json.dumps({'kernel': str(KERNEL),
                      'baseline': 'Actual kernel rx.c with patch220 reversed in a temporary copy',
                      'baseline_sha256': hashlib.sha256(original_source.encode()).hexdigest(),
                      'patch_sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(),
                      'scope': 'Actual marker and window-advance helper; skb delivery and timer release are recorded stubs.',
                      'results': output}, indent=2))
