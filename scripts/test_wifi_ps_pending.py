#!/usr/bin/env python3
"""Execute TIM publication with packets already submitted to the radio."""
from pathlib import Path
import subprocess
import tempfile
import re

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()


def function(name, text=source):
    start = re.search(r'(?:static )?(?:void|bool) ' + name + r'\([^;]*?\)\s*\{', text).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define RTL_AX_TX_ENTRIES 256
#define PE_PROBE_MAILBOX 0x2000
#define dma_wmb() ((void)0)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define for_each_set_bit(i,b,n) for(i=0;i<n;i++)if((b)[i])
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define local_bh_disable() ((void)0)
#define local_bh_enable() ((void)0)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
struct work_struct {int value;};
struct ieee80211_sta {unsigned id;};
struct rtl_ax_tx;
struct rtl_ax_ni_state {struct rtl_ax_tx *tx;bool route_peers[128];u32 route_tim[128];struct work_struct ps_work;};
struct rtl_ax_tx {int lock;void *hw;unsigned ps_releases;bool pe_active;struct {void *memory;}pe;struct rtl_ax_ni_state ni;
 bool peer_asleep[128],peer_authorized[128],pending[256];struct {unsigned macid,tid;}slots[256];};
static struct ieee80211_sta stations[128];static u16 masks[128];
static void ieee80211_sta_eosp(struct ieee80211_sta *sta){(void)sta;}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,const u8 *addr,void *unused){
 (void)hw;(void)unused;return &stations[addr[0]];
}
static void ieee80211_sta_set_buffered(struct ieee80211_sta *sta,unsigned tid,bool value){
 if(value)masks[sta->id]|=BIT(tid);else masks[sta->id]&=~BIT(tid);
}
'''
cases = r'''
int main(void){
 unsigned char memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 tx.ni.tx=&tx;for(unsigned i=0;i<128;i++){stations[i].id=i;memory[0xe400+i*8]=i;}
 tx.peer_authorized[2]=true;tx.peer_asleep[2]=true;tx.ni.route_peers[2]=true;
 /* No software queue remains; one submitted NI frame still owns its BID. */
 memory[0x18000+2*32]=1;
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==BIT(0));
 /* Reproduce the observed124 NI plus96 ARM outstanding frames. */
 memory[0x18000+2*32]=124;
 for(unsigned i=0;i<96;i++){tx.pending[i]=true;tx.slots[i].macid=2;tx.slots[i].tid=0;}
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==BIT(0));
 memory[0x18000+2*32]=0;
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==BIT(0));
 for(unsigned i=0;i<96;i++)tx.pending[i]=false;
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==0);
 /* The normal ARM path must announce its outstanding frame as well. */
 tx.peer_authorized[3]=true;tx.peer_asleep[3]=true;
 tx.pending[7]=true;tx.slots[7].macid=3;tx.slots[7].tid=6;
 ni_route_ps(&tx.ni.ps_work);assert(masks[3]==BIT(6));
 tx.pending[7]=false;ni_route_ps(&tx.ni.ps_work);assert(masks[3]==0);
 /* Grant a lone frame, then receive another before the first RPQ.
  * Pending ownership stays nonzero throughout 1 -> 2 -> 1; a binary
  * PE TIM event cannot repair a prematurely cleared mac80211 bit. */
 tx.peer_asleep[2]=true;memory[0x18000+2*32]=1;
 put_unaligned_le32(1,memory+PE_PROBE_MAILBOX+96);
 put_unaligned_le32(1,memory+0x11800+2*4);
 put_unaligned_le32(1,memory+0x13000+2*32);
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==BIT(0));
 rtl_ax_tx_ps_release(&tx,&stations[2],2,BIT(0),false,false);
 assert(tx.ps_releases==1);
 put_unaligned_le32(0,memory+0x11800+2*4); /* PE submits the granted frame. */
 put_unaligned_le32(0,memory+0x13000+2*32);
 memory[0x18000+2*32]=2;
 put_unaligned_le32(1,memory+0x11800+2*4); /* A new frame stays queued. */
 put_unaligned_le32(1,memory+0x13000+2*32);
 memory[0x18000+2*32]=1; /* Only the first frame completes. */
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==BIT(0));
 memory[0x18000+2*32]=0;
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==0);
 tx.peer_asleep[2]=false;memory[0x18000+2*32]=1;
 ni_route_ps(&tx.ni.ps_work);assert(masks[2]==0);
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-ps-') as directory:
    c, exe = Path(directory) / 'ps.c', Path(directory) / 'ps'
    tx_source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
    c.write_text(common + function('ni_test_buffered') + function('ni_route_ps') +
                 function('rtl_ax_tx_ni_buffered', tx_source) + function('rtl_ax_tx_ps_release', tx_source) + cases)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('TIM: submitted NI and ARM frames remain announced until completion; PS grant/new-arrival/RPQ preserves TIM; awake peers clear TIM')
