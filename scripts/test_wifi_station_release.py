#!/usr/bin/env python3
"""Exercise actual STA/BA callbacks with delayed RPQ and failed firmware ACKs."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'monitor.h').read_text()

def function(name):
    prefix = 'static void ' if name == 'rtl_ax_cleanup_work' else 'static bool ' if name == 'rtl_ax_security_cleanup' else 'static int '
    start = source.index(prefix + name + '(')
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define RTL_AX_BIT(n) BIT(n)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define IEEE80211_NUM_TIDS 16
#define RTL_AX_8852C 0x52
#define RTL_AX_H2C_CMD 0
#define RTL_AX_H2C_DATA 1
#define HZ 100
#define WRITE_ONCE(x,v) ((x)=(v))
#define max_t(type,a,b) ((type)(a)>(type)(b)?(type)(a):(type)(b))
#define min_t(type,a,b) ((type)(a)<(type)(b)?(type)(a):(type)(b))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define RTL_AX_AP_TRANSPORT_H
static void rtl_ax_store32(u8 *p,unsigned int v){memcpy(p,&v,4);}
static unsigned int rtl_ax_ap_read32(const u8 *p){unsigned int v;memcpy(&v,p,4);return v;}
'''
common += '#include "' + str(driver / 'role.h') + '"\n'
common += '#include "' + str(driver / 'security.h') + '"\n'
common += r'''
struct wiphy {int unused;};
struct wiphy_work {int unused;};
struct wiphy_delayed_work {struct wiphy_work work;};
struct ieee80211_vif {u8 addr[6];};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];u8 addr[6];unsigned int aid;};
struct rtl_ax_tx {bool pending[128][17];};
struct rtl_ax_ap {
 struct rtl_ax_tx *tx;struct ieee80211_vif *ap_vif;unsigned int chip;bool monitor_started;
 unsigned long station_ids[2],security_ids[2],security_deleting[2];
 struct {u8 self[6],peer[6];u16 aid;bool pending;} retired_stations[128];
 struct {u8 address[6],aggregation[16];u16 stopping,flushing;} tx_ba[128];
 struct {u8 macid,tid;bool valid,deleting;} rx_ba[8];
 struct rtl_ax_security_role security_roles[128];
 struct wiphy_delayed_work cleanup_work;
 unsigned int station_removed,keys_removed,rx_ba_started,rx_ba_stopped;int rx_ba_result;
 struct {int unused;} io;
};
struct ieee80211_hw {struct rtl_ax_ap *radio;struct wiphy *wiphy;};
enum {IEEE80211_AMPDU_RX_START,IEEE80211_AMPDU_RX_STOP,IEEE80211_AMPDU_TX_START,
 IEEE80211_AMPDU_TX_STOP_CONT,IEEE80211_AMPDU_TX_STOP_FLUSH,
 IEEE80211_AMPDU_TX_STOP_FLUSH_CONT,IEEE80211_AMPDU_TX_OPERATIONAL};
#define IEEE80211_AMPDU_TX_START_IMMEDIATE 1
struct ieee80211_ampdu_params {struct ieee80211_sta *sta;unsigned int tid,action,buf_size,ssn;};
static unsigned int queued,callbacks,drop_commands,role_commands,cam_commands,security_commands;
static unsigned int fail_class,fail_function,fail_count;
static bool rpq_complete;
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *h){return h->radio;}
static void ether_addr_copy(u8 *a,const u8 *b){memcpy(a,b,6);}
static void __clear_bit(unsigned int bit,unsigned long *ids){ids[bit/64]&=~(1UL<<(bit%64));}
static void set_bit(unsigned int bit,unsigned long *ids){ids[bit/64]|=1UL<<(bit%64);}
static bool test_bit(unsigned int bit,unsigned long *ids){return !!(ids[bit/64]&(1UL<<(bit%64)));}
#define for_each_set_bit(i,p,n) for(i=0;i<n;i++)if(test_bit(i,p))
static void wiphy_delayed_work_queue(struct wiphy *w,struct wiphy_delayed_work *d,unsigned int delay){(void)w;(void)d;assert(delay==HZ);queued++;}
static unsigned int rtl_ax_tx_dma_channel(unsigned int tid,unsigned int band){
 static const u8 channel[]={0,1,1,0,2,2,3,3};assert(!band && tid<8);return channel[tid];
}
static bool rtl_ax_tx_station_pending(struct rtl_ax_tx *tx,unsigned int id,unsigned int tid){
 for(unsigned int i=0;i<17;i++)if(tx->pending[id][i] && (tid==16 || i==tid))return true;
 return false;
}
static int rtl_ax_tx_wait_station(struct rtl_ax_tx *tx,unsigned int id,unsigned int tid){
 if(rpq_complete)for(unsigned int i=0;i<17;i++)if(tid==16 || i==tid)tx->pending[id][i]=false;
 return rtl_ax_tx_station_pending(tx,id,tid)?-ETIMEDOUT:0;
}
static void ieee80211_stop_tx_ba_cb_irqsafe(struct ieee80211_vif *v,u8 *addr,unsigned int tid){
 assert(v && addr[0]==2 && tid<16);callbacks++;
}
static void rtl_ax_update8(void *io,unsigned int reg,unsigned int mask,unsigned int value){(void)io;(void)reg;(void)mask;(void)value;}
static void rtl_ax_update32(void *io,unsigned int reg,unsigned int mask,unsigned int value){(void)io;(void)reg;(void)mask;(void)value;}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned int queue,unsigned int cat,unsigned int cls,
 unsigned int func,const u8 *p,unsigned int len,bool ack){
 assert(cat==1 && queue<=1);
 unsigned int id=rtl_ax_ap_read32(p)&127;
 if(cls==9){
  assert(func==27 && !ack && len==24);drop_commands++;
  id=(rtl_ax_ap_read32(p)>>8)&127;assert(rtl_ax_tx_station_pending(r->tx,id,16));
 }else if(cls==8){assert(ack && len==4);role_commands++;assert(!rtl_ax_tx_station_pending(r->tx,id,16));}
 else if(cls==6){assert(ack && len==60);cam_commands++;assert(!rtl_ax_tx_station_pending(r->tx,id,16));}
 else if(cls==10){assert(ack && len==24);security_commands++;}
 else {assert((cls==12 && len==8) || (cls==5 && len==68));}
 if(fail_count && cls==fail_class && func==fail_function){fail_count--;return -ETIMEDOUT;}
 return 0;
}
'''
test = r'''
int main(void){
 struct wiphy w={};struct ieee80211_vif vif={.addr={2,1,2,3,4,5}};
 struct rtl_ax_tx tx={};struct rtl_ax_ap r={.tx=&tx,.ap_vif=&vif,.chip=0x52,.monitor_started=true};
 struct ieee80211_hw hw={&r,&w};
 struct ieee80211_sta *sta=calloc(1,sizeof(*sta));assert(sta);
 sta->drv_priv[0].macid=7;sta->addr[0]=2;sta->addr[5]=77;sta->aid=27;set_bit(7,r.station_ids);
 memcpy(r.tx_ba[7].address,sta->addr,6);
 struct ieee80211_ampdu_params p={.sta=sta,.tid=3,.action=IEEE80211_AMPDU_RX_START,.ssn=101};
 assert(!rtl_ax_ampdu_action(&hw,&vif,&p));assert(r.rx_ba[0].valid);
 p.action=IEEE80211_AMPDU_RX_STOP;fail_class=12;fail_function=0;fail_count=1;
 assert(rtl_ax_ampdu_action(&hw,&vif,&p)==-ETIMEDOUT && r.rx_ba[0].deleting && queued);
 p.action=IEEE80211_AMPDU_RX_START;p.ssn=203;
 assert(!rtl_ax_ampdu_action(&hw,&vif,&p));assert(!r.rx_ba[0].deleting && !r.rx_ba[1].valid);
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(r.rx_ba[0].valid);
 for(unsigned int i=0;i<100;i++){
  p.action=IEEE80211_AMPDU_RX_STOP;fail_count=1;
  assert(rtl_ax_ampdu_action(&hw,&vif,&p)==-ETIMEDOUT);
  p.action=IEEE80211_AMPDU_RX_START;assert(!rtl_ax_ampdu_action(&hw,&vif,&p));
  assert(r.rx_ba[0].valid && !r.rx_ba[1].valid);
 }
 p.action=IEEE80211_AMPDU_TX_STOP_CONT;tx.pending[7][3]=true;rpq_complete=false;
 assert(!rtl_ax_ampdu_action(&hw,&vif,&p));assert(!callbacks && (r.tx_ba[7].stopping&BIT(3)));
 rpq_complete=true;rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(callbacks==1 && !r.tx_ba[7].stopping && !tx.pending[7][3]);
 for(unsigned int action=IEEE80211_AMPDU_TX_STOP_FLUSH;action<=IEEE80211_AMPDU_TX_STOP_FLUSH_CONT;action++){
  p.action=action;tx.pending[7][3]=true;rpq_complete=false;unsigned int drops=drop_commands;
  assert(!rtl_ax_ampdu_action(&hw,&vif,&p));assert(callbacks==1 && drop_commands>drops);
  rpq_complete=true;rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(callbacks==1 && !r.tx_ba[7].stopping);
 }
 r.security_roles[7].valid=1;r.security_roles[7].cam[0]=42;set_bit(42,r.security_ids);
 tx.pending[7][16]=true;rpq_complete=false;
 assert(!rtl_ax_station_remove(&hw,&vif,sta));assert(!sta->drv_priv[0].macid && r.retired_stations[7].pending);
 assert(test_bit(7,r.station_ids) && !role_commands && !cam_commands);
 free(sta);sta=NULL;
 rpq_complete=true;fail_class=6;fail_function=0;fail_count=1;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(r.retired_stations[7].pending && test_bit(7,r.station_ids));
 assert(!r.rx_ba[0].valid && !r.security_roles[7].valid && !test_bit(42,r.security_ids));
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(!r.retired_stations[7].pending && !test_bit(7,r.station_ids) && r.station_removed==1);
 assert(r.retired_stations[7].peer[5]==77 && r.retired_stations[7].aid==27);
 puts("STA/BA lifetime: delayed RPQ gates completion and MACID reuse; firmware failures retry after STA free; 100 RX BA restarts reuse one slot");
}
'''
names = ('rtl_ax_drop_station', 'rtl_ax_rx_ba_remove', 'rtl_ax_station_release',
         'rtl_ax_tx_ba_finish', 'rtl_ax_security_cleanup', 'rtl_ax_cleanup_work', 'rtl_ax_station_remove', 'rtl_ax_ampdu_action')
with tempfile.TemporaryDirectory(prefix='wifi-sta-release-', dir=root / 'artifacts') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(common + ''.join(function(n) for n in names) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-g', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
