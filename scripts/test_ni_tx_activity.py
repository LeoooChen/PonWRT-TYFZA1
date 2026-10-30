#!/usr/bin/env python3
"""Run the actual ARM activity consumer: independent peers/TIDs and idle expiry."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
start = source.index('static void ni_tx_activity(')
end = source.index('\nstatic void ni_route_ps(', start)
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define READ_ONCE(x) (x)
#define dma_rmb() ((void)0)
#define RTL_AX_NI_TX_ACTIVITY 0x1c000
struct ieee80211_sta {unsigned id;};
struct rtl_ax_tx {bool peer_authorized[128];void *hw;
 struct {u8 *memory;} pe;struct {u32 tx_activity_seen[128][8];u64 ba_refreshes;} ni;};
static struct ieee80211_sta stations[3]={{0},{1},{2}};
static unsigned refreshed[3][8],locked,bh_disabled;
static bool absent;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void local_bh_disable(void){assert(!bh_disabled++);}
static void local_bh_enable(void){assert(bh_disabled--==1);}
static void rcu_read_lock(void){assert(!locked++);}
static void rcu_read_unlock(void){assert(locked--==1);}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,const u8 *mac,void *bssid){
 assert(locked&&bh_disabled&&hw&&!bssid&&mac[0]<3);return absent?0:&stations[mac[0]];
}
static void ieee80211_refresh_tx_agg_session_timer(struct ieee80211_sta *sta,unsigned tid){
 assert(locked&&bh_disabled&&tid<8);refreshed[sta->id][tid]++;
}
'''
checks = r'''
int main(void){
 u32 memory[0x20000/4]={0};struct rtl_ax_tx tx={.hw=&tx,.pe.memory=(void *)memory};
 tx.peer_authorized[1]=tx.peer_authorized[2]=true;
 tx.pe.memory[0xe408]=1;tx.pe.memory[0xe410]=2;
 u32 *counts=memory+RTL_AX_NI_TX_ACTIVITY/4;
 counts[8]=7;counts[16+3]=4;
 ni_tx_activity(&tx,1);ni_tx_activity(&tx,2);
 assert(refreshed[1][0]==1&&refreshed[2][3]==1&&tx.ni.ba_refreshes==2);
 ni_tx_activity(&tx,1);ni_tx_activity(&tx,2);
 assert(tx.ni.ba_refreshes==2); /* No new traffic: timers can expire. */
 counts[8]+=7;counts[8+7]++;
 ni_tx_activity(&tx,1);
 assert(refreshed[1][0]==2&&refreshed[1][7]==1&&refreshed[2][3]==1);
 tx.ni.tx_activity_seen[1][0]=UINT32_MAX;counts[8]=0;
 ni_tx_activity(&tx,1);assert(refreshed[1][0]==3);
 tx.peer_authorized[2]=false;counts[16+3]++;
 ni_tx_activity(&tx,2);assert(refreshed[2][3]==1);
 absent=true;counts[8]++;
 ni_tx_activity(&tx,1);assert(refreshed[1][0]==3);
 for(unsigned tid=1;tid<7;tid++)assert(!refreshed[1][tid]);
 assert(!locked&&!bh_disabled);
}
'''
with tempfile.TemporaryDirectory(prefix='ni-activity-') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(fixture + source[start:end] + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('NI activity: only active peer/TIDs refreshed; idle TIDs, wrap, authorization and RCU lifetime PASS')
