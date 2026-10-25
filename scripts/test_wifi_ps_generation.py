#!/usr/bin/env python3
"""Run PS release/completion across expiry, wakeup, late RPQ and NI shutdown."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
kernel = root / 'sources/openwrt/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.55'
mac = (kernel / 'net/mac80211/sta_info.c').read_text()
tx = (driver / 'tx.c').read_text()
ni = (driver / 'ni-test.h').read_text()


def function(source, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include <pthread.h>
#include <string.h>
#include <stdio.h>
#include "rx-geometry.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define READ_ONCE(v) (v)
#define dma_wmb() atomic_thread_fence(memory_order_seq_cst)
#define dma_rmb() atomic_thread_fence(memory_order_seq_cst)
#define EXPORT_SYMBOL_GPL(n)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define WLAN_STA_SP 0
#define test_sta_flag(s,f) ((s)->service_period)
#define set_sta_flag(s,f) ((s)->service_period=true)
#define clear_sta_flag(s,f) ((s)->service_period=false)
#define spin_lock_bh(p) assert(!pthread_mutex_lock(p))
#define spin_unlock_bh(p) assert(!pthread_mutex_unlock(p))
#define spin_lock_irqsave(p,f) do{(f)=0;spin_lock_bh(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);spin_unlock_bh(p);}while(0)
#define local_bh_disable() ((void)0)
#define local_bh_enable() ((void)0)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define atomic_inc_return(p) (atomic_fetch_add(p,1)+1)
struct ieee80211_sta {unsigned id;};
struct ieee80211_local {_Atomic u32 ps_generation;};
struct sta_info {struct ieee80211_sta sta;struct ieee80211_local *local;pthread_mutex_t ps_lock;u32 ps_generation;bool service_period;};
struct rtl_ax_tx {bool pe_active;pthread_mutex_t lock;void *hw;unsigned ps_releases,ps_eosp;
 bool peer_qos[128],peer_offloaded[128],peer_asleep[128];u32 peer_rates[128];
 struct {void *memory;}pe;struct {u32 ps_seen[2][128];bool ps_issued[128];}ni;};
static struct sta_info *lookup[128];
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,const u8 *mac,void *vif){
 (void)hw;(void)vif;return lookup[mac[0]]?&lookup[mac[0]]->sta:NULL;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
'''
# Execute the exact MAC start/wakeup publication blocks, not a separate
# implementation of their epoch or lock protocol.
response = function(mac, 'ieee80211_sta_ps_deliver_response')
start = response[response.index('\tspin_lock_bh(&sta->ps_lock);'):response.index('\n\t__skb_queue_head_init')]
wakeup = function(mac, 'ieee80211_sta_ps_deliver_wakeup')
wake = wakeup[wakeup.index('\tspin_lock_bh(&sta->ps_lock);'):wakeup.index('\n\tBUILD_BUG_ON')]
code = common + function(mac, 'ieee80211_sta_ps_generation') + function(mac, 'ieee80211_sta_eosp_generation')
code += function(tx, 'rtl_ax_tx_peer_qos') + function(tx, 'rtl_ax_tx_ni_buffered') + function(tx, 'rtl_ax_tx_ps_release')
code += function(tx, 'ni_ps_complete') + function(ni, 'ni_ps_done') + function(ni, 'ni_ps_retire')
code += 'static u32 start(struct sta_info *sta){struct ieee80211_local *local=sta->local;u32 generation;\n' + start + '\nreturn generation;}\n'
code += 'static void wake(struct sta_info *sta){struct ieee80211_local *local=sta->local;\n' + wake + '\n}\n'
code += r'''
static pthread_barrier_t barrier;static struct sta_info *racing;static u32 old_generation;
static void *new_period(void *p){(void)p;pthread_barrier_wait(&barrier);start(racing);return NULL;}
static void *old_completion(void *p){(void)p;pthread_barrier_wait(&barrier);ieee80211_sta_eosp_generation(&racing->sta,old_generation);return NULL;}
int main(void){
 struct ieee80211_local local={};struct sta_info sta={.sta={1},.local=&local,.ps_lock=PTHREAD_MUTEX_INITIALIZER};
 u8 memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.lock=PTHREAD_MUTEX_INITIALIZER,.pe={memory}};
 lookup[1]=&sta;memory[0xe408]=1;put_unaligned_le32(1,memory+0x2060);
 put_unaligned_le32(1,memory+0x11804);put_unaligned_le32(1,memory+0x13020);
 u32 first=start(&sta);
 rtl_ax_tx_ps_release(&tx,&sta.sta,1,BIT(0),false,false,first);
 assert(get_unaligned_le32(memory+0xe814)==first && tx.ni.ps_issued[1]);
 assert(!(get_unaligned_le32(memory+RTL_AX_NI_PS_VERSION+4)&1));
 /* Last queued packet expires before submission. */
 put_unaligned_le32(0,memory+0x11804);put_unaligned_le32(0,memory+0x13020);
 put_unaligned_le32(first,memory+RTL_AX_NI_PS_CANCEL+8);
 put_unaligned_le32(1,memory+RTL_AX_NI_PS_CANCEL+12);
 ni_ps_done(&tx,1);
 assert(!sta.service_period && tx.ps_eosp==1 && !tx.ni.ps_issued[1]);
 ni_ps_done(&tx,1);assert(tx.ps_eosp==1);
 /* Waking invalidates the old period. A late completion cannot close its successor. */
 wake(&sta);u32 second=start(&sta);
 put_unaligned_le32(1,memory+0x11804);put_unaligned_le32(1,memory+0x13020);
 rtl_ax_tx_ps_release(&tx,&sta.sta,1,BIT(0),true,false,second);
 rtl_ax_tx_ps_release(&tx,&sta.sta,1,BIT(0),false,false,first);
 assert(get_unaligned_le32(memory+0xe814)==second && (get_unaligned_le32(memory+0xe81c)&1));
 put_unaligned_le32(first,memory+RTL_AX_NI_PS_DONE+8);put_unaligned_le32(1,memory+RTL_AX_NI_PS_DONE+12);
 ni_ps_done(&tx,1);assert(sta.service_period && tx.ni.ps_issued[1]);
 put_unaligned_le32(second,memory+RTL_AX_NI_PS_DONE+8);put_unaligned_le32(2,memory+RTL_AX_NI_PS_DONE+12);
 ni_ps_done(&tx,1);assert(!sta.service_period && !tx.ni.ps_issued[1]);
 /* Shutdown finishes a real issued grant; it does not end a new software period. */
 u32 third=start(&sta);rtl_ax_tx_ps_release(&tx,&sta.sta,1,BIT(0),false,false,third);
 ni_ps_retire(&tx);assert(!sta.service_period && !tx.ni.ps_issued[1]);
 start(&sta);ni_ps_retire(&tx);assert(sta.service_period);
 /* Both the MAC epoch and producer counter may legitimately wrap through zero. */
 atomic_store(&local.ps_generation,UINT32_MAX);u32 zero=start(&sta);assert(zero==0);
 rtl_ax_tx_ps_release(&tx,&sta.sta,1,BIT(0),false,false,zero);
 tx.ni.ps_seen[1][1]=UINT32_MAX;
 put_unaligned_le32(zero,memory+RTL_AX_NI_PS_DONE+8);put_unaligned_le32(0,memory+RTL_AX_NI_PS_DONE+12);
 ni_ps_done(&tx,1);assert(!sta.service_period && !tx.ni.ps_issued[1]);
 /* MACID reuse gets a different hardware-local epoch, even with the same address. */
 struct sta_info next={.sta={1},.local=&local,.ps_lock=PTHREAD_MUTEX_INITIALIZER};
 rtl_ax_tx_peer_qos(&tx,1,true,10);memory[0xe408]=1;
 lookup[1]=&next;u32 fresh=start(&next);assert(fresh!=zero);
 put_unaligned_le32(zero,memory+RTL_AX_NI_PS_CANCEL+8);
 put_unaligned_le32(1,memory+RTL_AX_NI_PS_CANCEL+12);
 tx.ni.ps_seen[0][1]=0;ni_ps_done(&tx,1);assert(next.service_period);
 /* Actual locking makes old completion vs new start safe in either order. */
 racing=&next;pthread_barrier_init(&barrier,NULL,2);
 for(unsigned i=0;i<256;i++){
  old_generation=start(racing);pthread_t a,b;
  assert(!pthread_create(&a,NULL,new_period,NULL));assert(!pthread_create(&b,NULL,old_completion,NULL));
  assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));
  assert(racing->service_period && racing->ps_generation!=old_generation);
 }
 pthread_barrier_destroy(&barrier);
 /* A removed STA cannot receive EOSP, but quiesced NI still relinquishes its grant. */
 put_unaligned_le32(1,memory+0x11804);put_unaligned_le32(1,memory+0x13020);
 u32 gone=start(&next);rtl_ax_tx_ps_release(&tx,&next.sta,1,BIT(0),false,false,gone);
 assert(tx.ni.ps_issued[1]);lookup[1]=NULL;ni_ps_retire(&tx);assert(!tx.ni.ps_issued[1]);
 puts("PS generation: expiry -> exact completion, wake/late RPQ, stale publication, shutdown, MACID/counter wrap and concurrent new period passed.");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-ps-generation-') as directory:
    c, exe = Path(directory) / 'test.c', Path(directory) / 'test'
    c.write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-fsanitize=address,undefined', '-I' + str(driver), str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
