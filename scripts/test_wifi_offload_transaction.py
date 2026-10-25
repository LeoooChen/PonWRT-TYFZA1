#!/usr/bin/env python3
"""Inject FE publication failures through real NI enable, retry and withdrawal."""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
base = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/'


def read(name):
    return (subprocess.check_output(['git', 'show', args.revision + ':' + base + name], cwd=root, text=True)
            if args.revision else (root / base / name).read_text())


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
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include "rtl9617c-fe-format.h"
typedef uint8_t u8;typedef uint32_t u32;
#define ETH_ALEN 6
#define GFP_KERNEL 0
#define BIT(n) (1U<<(n))
#define IEEE80211_NUM_TIDS 16
#define HZ 100
#define PE_PROBE_MAILBOX 0x2000
#define RTL_FE_ACTION_OFFSET 0
#define RTL_FE_ACTION_BYTES 48
#define kzalloc(n,f) calloc(1,n)
#define kfree(p) free(p)
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define ether_addr_copy(a,b) memcpy(a,b,6)
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define dma_wmb() ((void)0)
#define dma_rmb() ((void)0)
#define read_poll_timeout(op,v,cond,d,t,s,args...) ({(void)(d);(void)(t);(void)(s);v=op(args);(cond)?0:-ETIMEDOUT;})
#define wait_event_timeout(w,c,t) ((void)(t),(c)?1:0)
#define msecs_to_jiffies(v) (v)
struct list_head {struct list_head *next,*prev;};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(struct list_head *c=(h)->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m));c=c->next)
#define list_for_each_entry_safe(p,n,h,m) for(struct list_head *c=(h)->next,*nc=c->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m),(n)=container_of(nc,__typeof__(*(n)),m),1);c=nc,nc=c->next)
static void init(struct list_head *h){h->next=h->prev=h;}
static void list_add_tail(struct list_head *n,struct list_head *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void list_del(struct list_head *n){n->prev->next=n->next;n->next->prev=n->prev;}
struct net_device {unsigned references;};
struct rtl_fe_port {struct list_head list;struct net_device *netdev;unsigned wifi_id,destination;bool retired;};
struct rtl_fe_wifi_peer {struct list_head list;struct rtl_fe_port *port;u8 address[6];unsigned destination;};
struct rtl_fe_flow {struct list_head list;struct rtl_fe_port *output;u8 destination[6];unsigned hash_index,crc16;bool installed,deleting;};
struct rtl_fe {struct list_head ports,flows,wifi_peers;int lock;void *memory;};
struct rtl_dq_io {struct rtl_fe *fe;};
struct wiphy {int unused;};struct work_struct {int unused;};struct wiphy_work {int unused;};
struct wiphy_delayed_work {struct wiphy_work work;};
struct ieee80211_hw {struct wiphy *wiphy;};
struct wireless_dev {struct net_device *netdev;};struct ieee80211_vif {struct wireless_dev wdev;};
struct rtl_ax_tx {bool pe_active,peer_offloaded[128],peer_authorized[128];unsigned occupied,ni_reports,ni_failed;
 struct {void *memory;}pe;void *rx,*pdev;
 struct {bool active,route_peers[128];unsigned cpu,route_macid,route_tim[128];struct net_device *route_dev;struct work_struct ps_work;}ni;};
struct rtl_ax_ap {struct rtl_ax_tx *tx;struct ieee80211_hw *hw;struct ieee80211_vif *ap_vif;
 struct wiphy_work offload_work;struct wiphy_delayed_work cleanup_work;bool ap_started,monitor_started,offload_retry,peer_authorized[128];
 struct {unsigned valid;}security_roles[128];struct {unsigned aggregation[16],stopping;}tx_ba[128];
 struct {bool deleting;}rx_ba[8];struct {bool pending;}retired_stations[128];int offload_result;unsigned offload_started;};
static struct rtl_fe *active_fe;static struct rtl_ax_tx *active_tx;static int ni_test_lock;
static unsigned fail_stage,fail_index,ages[2],timers;static struct wiphy_work *queued;
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe};}
static void mutex_lock(int *p){assert(!*p);*p=1;}static void mutex_unlock(int *p){assert(*p);*p=0;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static int rtl_fe_main_age(struct rtl_dq_io *io,unsigned i,unsigned age){
 assert(io->fe->lock);if(i==fail_index&&fail_stage==(age?3:1))return -ETIMEDOUT;ages[i]=age;return 0;}
static int rtl_fe_cache_command(struct rtl_dq_io *io,unsigned i,unsigned crc,unsigned op){
 assert(io->fe->lock&&crc==i+100&&op==1);return i==fail_index&&fail_stage==2?-ETIMEDOUT:0;}
static int rtl9617c_wifi_id(struct net_device *dev){(void)dev;return 1;}
static int ni_uplink_start(struct rtl_ax_tx *tx,int id){assert(tx==active_tx&&id==1);return 0;}
static void rtl_ax_rx_uplink_enable(void *rx,unsigned id){(void)rx;assert(id==1);}
static void rtl_ax_rx_uplink_disable(void *rx,unsigned id){(void)rx;assert(id==1);}
static void rtl_ax_rx_uplink_retire(void *rx,unsigned id){(void)rx;assert(id==1);}
static bool rtl_ax_rx_uplink_active(void *rx,unsigned id){(void)rx;(void)id;return false;}
static int rtl_ax_tx_pause(struct rtl_ax_tx *tx){assert(tx==active_tx);return 0;}
static void rtl_ax_tx_open(struct rtl_ax_tx *tx){assert(tx==active_tx);}
static int ni_test_prepare(struct rtl_ax_tx *tx,unsigned id){assert(id==1);tx->ni.active=true;return 0;}
static int ni_test_release(struct rtl_ax_tx *tx){tx->ni.active=false;return 0;}
static bool ni_epps_empty(struct rtl_ax_tx *tx){(void)tx;return true;}
static void ni_test_buffered(struct rtl_ax_tx *tx,unsigned id,unsigned mask){(void)tx;(void)id;(void)mask;}
static int rtl_ax_mac_drop_peer(void *dev,unsigned id){(void)dev;(void)id;return 0;}
static void ni_peer_fence(struct rtl_ax_tx *tx){(void)tx;}
static bool ni_peer_idle(struct rtl_ax_tx *tx,unsigned id){(void)tx;(void)id;return true;}
static void cancel_work_sync(void *work){(void)work;}
static void schedule_work(void *work){(void)work;}
static void dev_hold(struct net_device *dev){dev->references++;}
static void dev_put(struct net_device *dev){assert(dev->references);dev->references--;}
static struct wireless_dev *ieee80211_vif_to_wdev(struct ieee80211_vif *vif){return &vif->wdev;}
static void wiphy_delayed_work_queue(struct wiphy *w,struct wiphy_delayed_work *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);timers++;}
static void wiphy_work_queue(struct wiphy *w,struct wiphy_work *work){(void)w;queued=work;}
static bool rtl_ax_security_cleanup(struct rtl_ax_ap *r){(void)r;return false;}
static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;assert(0);return 0;}
static int rtl_ax_station_release(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;assert(0);return 0;}
static int rtl_ax_tx_ba_finish(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;assert(0);return 0;}
'''
cases = r'''
int main(void){
 for(unsigned initial=0;initial<2;initial++)for(unsigned failure=0;failure<4;failure++)for(unsigned cancel=0;cancel<2;cancel++){
  struct rtl_fe fe={};struct net_device dev={};struct rtl_fe_port port={.netdev=&dev,.wifi_id=1,.destination=16};
  struct rtl_fe_flow flows[2]={};u32 actions[2][12]={};u8 memory[0x20000]={};
  struct rtl_ax_tx tx={.pe_active=true,.pe={memory},.ni={.active=initial,.cpu=3}};
  struct wiphy wiphy={};struct ieee80211_hw hw={&wiphy};struct ieee80211_vif vif={.wdev={&dev}};
  struct rtl_ax_ap radio={.tx=&tx,.hw=&hw,.ap_vif=&vif,.ap_started=true,.monitor_started=true};
  const u8 mac[6]={2,1,2,3,4,5};active_fe=&fe;active_tx=&tx;timers=0;queued=NULL;
  init(&fe.ports);init(&fe.flows);init(&fe.wifi_peers);fe.memory=actions;list_add_tail(&port.list,&fe.ports);
  if(initial){dev_hold(&dev);tx.ni.route_dev=&dev;}
  for(unsigned i=0;i<2;i++){flows[i].output=&port;flows[i].installed=true;flows[i].hash_index=i;flows[i].crc16=i+100;
   memcpy(flows[i].destination,mac,6);list_add_tail(&flows[i].list,&fe.flows);ages[i]=1;rtl_fe_action_set(actions[i],RTL_FE_MCGID,16);}
  memcpy(memory+0xe408,mac,6);put_unaligned_le32(1<<16,memory+0xd020+16);
  tx.peer_authorized[1]=radio.peer_authorized[1]=true;radio.tx_ba[1].aggregation[0]=64;radio.security_roles[1].valid=1;
  fail_index=failure==3;fail_stage=failure==3?2:failure+1;
  rtl_ax_offload_work(&wiphy,&radio.offload_work);
  assert(radio.offload_result==-ETIMEDOUT && radio.offload_retry && timers==1);
  assert(tx.ni.route_peers[1] && !tx.peer_offloaded[1] && get_unaligned_le32(memory+0x11a04)==1);
  assert(fe_wifi_peer(&fe,&port,mac)->destination==19 && dev.references==1);
  if(failure==3)assert(ages[0]==1 && ((actions[0][0]>>8)&1023)==19);
  fail_stage=0;
  if(cancel){
   radio.peer_authorized[1]=false;rtl_ax_tx_peer_authorized(&tx,1,false);
   assert(!rtl_ax_tx_ni_retire_peer(&tx,1));
   assert(!tx.ni.route_peers[1] && !tx.ni.active && !dev.references && !fe_wifi_peer(&fe,&port,mac));
  }
  rtl_ax_cleanup_work(&wiphy,&radio.cleanup_work.work);
  assert(queued==&radio.offload_work);
  rtl_ax_offload_work(&wiphy,queued);
  assert(!radio.offload_retry && timers==1);
  assert(tx.peer_offloaded[1]==!cancel && get_unaligned_le32(memory+0x11a04)==!cancel);
  if(!cancel){rtl_ax_tx_peer_authorized(&tx,1,false);assert(!rtl_ax_tx_ni_retire_peer(&tx,1));assert(!dev.references);}
 }
 puts("FE transaction: initial/existing NI, four partial failures, prepared admission, scheduled retry and cancellation/withdrawal passed.");
}
'''
fe = read('ethernet/realtek/rtl9617c-fe.c')
ni = read('wireless/realtek/rtl_ax_ap/ni-test.h')
monitor = read('wireless/realtek/rtl_ax_ap/monitor.h')
tx = read('wireless/realtek/rtl_ax_ap/tx.c')
code = common + ''.join(function(fe, n) for n in ('fe_port', 'fe_wifi_peer', 'rtl_fe_wifi_peer_route'))
code += r'''
static int rtl9617c_wifi_peer_route(struct net_device *dev,const u8 *mac,unsigned route){
 assert(get_unaligned_le32(active_tx->pe.memory+0x11a04)==!!route);
 if(route)assert(active_tx->ni.route_peers[1]);
 return rtl_fe_wifi_peer_route(active_fe,dev,mac,route);}
'''
code += function(ni, 'rtl_ax_tx_ni_route') + function(ni, 'rtl_ax_tx_ni_peer') + function(ni, 'rtl_ax_tx_ni_retire_peer')
code += function(tx, 'rtl_ax_tx_peer_authorized')
code += function(monitor, 'rtl_ax_offload_work') + function(monitor, 'rtl_ax_cleanup_work') + cases
with tempfile.TemporaryDirectory(prefix='wifi-offload-transaction-') as directory:
    c, exe = Path(directory) / 'test.c', Path(directory) / 'test'
    c.write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-I' + str(root / base / 'ethernet/realtek'),
                    str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
