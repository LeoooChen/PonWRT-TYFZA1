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
#define RTL_AX_SECURITY_ENTRIES 128
#define RTL_AX_H2C_DATA 1
#define RTL_AX_CLIENT_MACID 1
#define NL80211_IFTYPE_MESH_POINT 7
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_FE_ACTION_OFFSET 0
#define RTL_FE_ACTION_BYTES 48
#define kzalloc(n,f) calloc(1,n)
#define kfree(p) free(p)
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define ether_addr_copy(a,b) memcpy(a,b,6)
#define spin_lock_irqsave(p,f) do{(f)=0;mutex_lock(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);mutex_unlock(p);}while(0)
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
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_STATION 2
struct wireless_dev;
struct net_device {unsigned references;struct wireless_dev *ieee80211_ptr;};
struct rtl_fe_port {struct list_head list;struct net_device *netdev;unsigned wifi_id,destination;bool retired;};
struct rtl_fe_wifi_peer {struct list_head list;struct rtl_fe_port *port;u8 address[6];unsigned destination;uint64_t policy_generation;};
struct rtl_fe_flow {struct list_head list;struct rtl_fe_port *output;u8 destination[6];unsigned hash_index,crc16,replica_count;bool installed,deleting;};
struct rtl_fe {struct list_head ports,flows,wifi_peers;int lock;void *memory;};
struct rtl_dq_io {struct rtl_fe *fe;};
struct wiphy {int unused;};struct work_struct {int unused;};struct wiphy_work {int unused;};
struct wiphy_delayed_work {struct wiphy_work work;};
struct ieee80211_hw {struct wiphy *wiphy;};
struct rtl_ax_station {unsigned macid;unsigned long ba_retry[8];};struct ieee80211_sta {struct rtl_ax_station drv_priv[1];bool wme;struct {struct {bool ht_supported;}ht_cap;struct {bool vht_supported;}vht_cap;struct {bool has_he;}he_cap;}deflink;};
enum {IEEE80211_AMPDU_TX_STOP_CONT,IEEE80211_AMPDU_TX_STOP_FLUSH,IEEE80211_AMPDU_TX_STOP_FLUSH_CONT};
struct ieee80211_ampdu_params {struct ieee80211_sta *sta;unsigned action,tid;};
static void rtl_ax_store32(u8 *,u32);static u32 rtl_ax_ap_read32(const u8 *);
#include "'''+str(root/base/'wireless/realtek/rtl_ax_ap/role-identity.h')+r'''"
struct wireless_dev {struct net_device *netdev;int iftype;bool use_4addr;};struct ieee80211_vif {struct rtl_ax_vif drv_priv[1];struct wireless_dev wdev;int type;u8 addr[6];};
struct rtl_ax_tx {struct net_device *bcmc_dev[12];unsigned bcmc_domains,bcmc_domain_retiring;bool key_refresh_pending,bcmc_enabled,bcmc_owned,bcmc_prepare_failed,bcmc_retiring;int lock;bool pe_active,peer_offloaded[128],peer_authorized[128];unsigned occupied,ni_reports,ni_failed;
 struct {void *memory;}pe;void *rx,*pdev;
 struct {bool active,retire_quiescing,route_peers[128];u8 retire_macid;unsigned cpu,route_macid,route_tim[128];struct net_device *route_dev;struct work_struct ps_work;}ni;};
struct rtl_ax_ap {u8 mbid_drop_pending;struct rtl_ax_role_identity identities[128];void *rx;bool beacon_enabled;int ap_result;struct {int error;}dfs;struct rtl_ax_tx *tx;struct ieee80211_hw *hw;struct ieee80211_vif *ap_vif,*sta_vif;bool sta_associated,sta_4addr;int scan_offload_result;
 struct wiphy_work offload_work;struct wiphy_delayed_work cleanup_work;bool scanning,ap_started,monitor_started,offload_retry,peer_authorized[128];
 struct rtl_ax_security_role {unsigned valid,tx_keyid,cam[6],keyid[7];}security_roles[128];struct {unsigned aggregation[16],stopping,flushing;u8 address[6];}tx_ba[128];
 struct ieee80211_key_conf *security_keys[128];unsigned security_unlink[128];bool ra_registered[128];
 struct {bool deleting;unsigned macid;}rx_ba[8];struct {bool pending;}retired_stations[128];int offload_result;unsigned offload_started,chip;};
struct ieee80211_key_conf {unsigned id,hw_key_idx,keyidx,cipher;};
#define WLAN_CIPHER_SUITE_GCMP 8
#define RTL_MC_PE_KEY_GCMP (1U<<27)
#define RTL_MC_PE_KEY_256 (1U<<28)
static unsigned rtl_ax_security_type(unsigned c){return c==9?9:c==10?7:c==8?8:6;}
static struct ieee80211_key_conf active_key;
static int key_error;static bool open_key;
static int ieee80211_rtl9617c_unicast_key(struct ieee80211_vif *v,const u8 *a,struct ieee80211_key_conf **key){(void)v;(void)a;*key=open_key?NULL:&active_key;return key_error;}
struct ieee80211_tx_info {struct {struct ieee80211_vif *vif;}control;};
static unsigned bcmc_calls,bcmc_closes;static int bcmc_error;static struct ieee80211_key_conf gtk;
static void rtl_ax_tx_bcmc_close(struct rtl_ax_tx *t){t->bcmc_enabled=false;bcmc_closes++;}
static void rtl_ax_tx_bcmc_close_vif(struct rtl_ax_tx *t,struct ieee80211_vif *v){(void)v;rtl_ax_tx_bcmc_close(t);}
static int rtl_ax_tx_bcmc_drop_all(struct rtl_ax_tx *t){(void)t;return 0;}
static int rtl_ax_tx_bcmc_stop(struct rtl_ax_tx *t){rtl_ax_tx_bcmc_close(t);return bcmc_error;}
static int ieee80211_rtl9617c_bcmc_key(struct ieee80211_vif *v,struct ieee80211_key_conf **key){assert(v->type==3);*key=&gtk;return 0;}
static unsigned rtl_ax_tx_basic_rate(struct rtl_ax_ap *r,struct ieee80211_sta *sta,struct ieee80211_tx_info *i){assert(r&&!sta&&i->control.vif);return 4;}
static int rtl_ax_tx_bcmc(struct rtl_ax_tx *t,struct ieee80211_vif *v,struct ieee80211_key_conf *key,unsigned rate){assert(t&&v&&key==&gtk&&rate==4);bcmc_calls++;return bcmc_error;}
static int rtl_ax_mac_drop_bcmc(void *p){(void)p;return 0;}
static void rtl_ax_tx_mcast_revoke(struct rtl_ax_tx *tx){(void)tx;}

static unsigned long jiffies;
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
static void rcu_read_lock(void){}static void rcu_read_unlock(void){}
static u8 rtl_ax_tx_active_tids(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(struct ieee80211_hw *h,const u8 *a,const u8 *b){(void)h;(void)a;(void)b;return NULL;}
static int ieee80211_start_tx_ba_session(struct ieee80211_sta *s,unsigned tid,unsigned t){(void)s;(void)tid;(void)t;assert(0);return 0;}
static struct rtl_ax_ap *active_radio;
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){assert(active_radio->hw==hw);return active_radio;}
static struct rtl_fe *active_fe;static struct rtl_ax_tx *active_tx;static int ni_test_lock;
static void fe_mcast_invalidate(struct rtl_fe *f){(void)f;}
static int ni_mcast_bind(struct rtl_ax_tx *t,struct net_device *d){(void)t;(void)d;return 0;}
static int ni_mcast_unbind(struct rtl_ax_tx *t){(void)t;return 0;}
static void rtl_ax_rx_open_peer(void *rx,unsigned id,bool open){(void)rx;(void)id;(void)open;}
static int rtl_ax_tx_mcast_policy(struct rtl_ax_tx *t,struct ieee80211_vif *v,unsigned id){(void)t;(void)v;(void)id;return 0;}
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
static int rtl_ax_tx_rx_retire(struct rtl_ax_tx *tx,unsigned id){assert(tx==active_tx);(void)id;return 0;}
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
static int rtl_ax_port0_drop_mbid(struct rtl_ax_ap *r,unsigned id){r->mbid_drop_pending&=~BIT(id);return 0;}
static bool rtl_ax_security_cleanup(struct rtl_ax_ap *r){(void)r;return false;}
static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;assert(0);return 0;}
static int rtl_ax_station_release(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;assert(0);return 0;}
static unsigned ba_callbacks;
static int rtl_ax_drop_station(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;assert(!active_tx->ni.route_peers[1]);return 0;}
static bool rtl_ax_tx_station_pending(struct rtl_ax_tx *tx,unsigned id,unsigned tid){(void)tx;(void)id;(void)tid;assert(!active_tx->ni.route_peers[1]);return 0;}
static void rtl_ax_station_cmac_connected(u8 *data,unsigned id,bool five,unsigned aggregate){(void)id;(void)five;(void)aggregate;memset(data,0,68);}
static void rtl_ax_station_cmac_client(u8 *data){(void)data;}
static void rtl_ax_store32(u8 *p,u32 value){put_unaligned_le32(value,p);}
static u32 rtl_ax_ap_read32(const u8 *p){return get_unaligned_le32(p);}
#define RTL_AX_8852C 0x52
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *data,unsigned len,bool ack){
 (void)r;(void)q;(void)cat;(void)cls;(void)fn;(void)data;(void)ack;assert(len==68&&!active_tx->ni.route_peers[1]);return 0;}
static void ieee80211_stop_tx_ba_cb_irqsafe(struct ieee80211_vif *vif,const u8 *addr,unsigned tid){(void)vif;(void)addr;assert(tid==1);ba_callbacks++;}
static void rtl_ax_tx_peer_ba(struct rtl_ax_tx *tx,unsigned id,unsigned tid,unsigned size){(void)tx;(void)id;assert(tid==1&&!size);}
'''
cases = r'''
int main(void){
 for(unsigned client=0;client<2;client++)for(unsigned initial=0;initial<2;initial++)for(unsigned failure=0;failure<4;failure++)for(unsigned cancel=0;cancel<4;cancel++)for(unsigned key_change=0;key_change<4;key_change++)for(unsigned opened=0;opened<2;opened++)for(unsigned gcmp=0;gcmp<4;gcmp++){
  struct rtl_fe fe={};struct net_device dev={};struct rtl_fe_port port={.netdev=&dev,.wifi_id=1,.destination=16};
  struct rtl_fe_flow flows[2]={};u32 actions[2][12]={};u8 memory[0x20000]={};
  struct rtl_ax_tx tx={.pe_active=true,.pe={memory},.ni={.active=initial,.cpu=3}};
  struct wiphy wiphy={};struct ieee80211_hw hw={&wiphy};struct ieee80211_vif vif={.wdev={.netdev=&dev}};
  struct rtl_ax_ap radio={.tx=&tx,.hw=&hw,.ap_vif=&vif,.ap_started=true,.monitor_started=true};
  if(client){vif.wdev.iftype=2;dev.ieee80211_ptr=&vif.wdev;radio.sta_vif=&vif;radio.ap_vif=NULL;radio.ap_started=false;radio.sta_associated=true;}
  const u8 mac[6]={2,1,2,3,4,5};active_radio=&radio;active_fe=&fe;active_tx=&tx;timers=ba_callbacks=0;queued=NULL;key_error=0;open_key=opened;
  init(&fe.ports);init(&fe.flows);init(&fe.wifi_peers);fe.memory=actions;list_add_tail(&port.list,&fe.ports);
  if(initial){dev_hold(&dev);tx.ni.route_dev=&dev;}
  for(unsigned i=0;i<2;i++){flows[i].output=&port;flows[i].installed=true;flows[i].hash_index=i;flows[i].crc16=i+100;
   memcpy(flows[i].destination,mac,6);list_add_tail(&flows[i].list,&fe.flows);ages[i]=1;rtl_fe_action_set(actions[i],RTL_FE_MCGID,16);}
  memcpy(memory+0xe408,mac,6);put_unaligned_le32((1<<16)|(1<<18),memory+0xd020+16);
  tx.peer_authorized[1]=radio.peer_authorized[1]=true;radio.tx_ba[1].aggregation[0]=64;radio.security_roles[1].valid=1;active_key=(struct ieee80211_key_conf){.cipher=(unsigned[]){4,8,9,10}[gcmp]};put_unaligned_le32(((gcmp==1||gcmp==2)?RTL_MC_PE_KEY_GCMP:0)|(gcmp>=2?RTL_MC_PE_KEY_256:0),memory+0xd020+20);radio.security_keys[0]=&active_key;if(opened){radio.security_roles[1].valid=0;put_unaligned_le32(1<<18,memory+0xd020+16);put_unaligned_le32(0,memory+0xd020+20);}
  tx.peer_authorized[2]=true;put_unaligned_le32((1<<16)|(1<<18),memory+0xd050);
  if(client){
   radio.scanning=true;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!tx.ni.route_peers[1]&&!get_unaligned_le32(memory+0x11a04));radio.scanning=false;
   radio.sta_4addr=true;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!tx.ni.route_peers[1]);radio.sta_4addr=false;
  }
  fail_index=failure==3;fail_stage=failure==3?2:failure+1;
  rtl_ax_offload_work(&wiphy,&radio.offload_work);
  assert(radio.offload_result==-ETIMEDOUT && radio.offload_retry && timers==1);
  assert(tx.ni.route_peers[1] && !tx.peer_offloaded[1] && get_unaligned_le32(memory+0x11a04)==1);
  assert(!tx.ni.route_peers[2]&&!get_unaligned_le32(memory+0x11a08));
  assert(fe_wifi_peer(&fe,&port,mac)->destination==19 && dev.references==1);
  if(failure==3)assert(ages[0]==1 && ((actions[0][0]>>8)&1023)==19);
  if(cancel==2){
   struct ieee80211_sta sta={.drv_priv={{1}}};struct ieee80211_ampdu_params params={.sta=&sta,.tid=1,.action=IEEE80211_AMPDU_TX_STOP_CONT};
   radio.tx_ba[1].aggregation[1]=64;
   assert(!rtl_ax_ampdu_action(&hw,&vif,&params));
   assert(radio.tx_ba[1].stopping==BIT(1) && !get_unaligned_le32(memory+0x11a04));
   rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!get_unaligned_le32(memory+0x11a04));
  }
  if(cancel==3){
   /* DISABLE_KEY records its unlink before attempting NI withdrawal. */
   radio.security_unlink[7]=2;
   assert(rtl_ax_tx_ni_retire_peer(&tx,1)==-ETIMEDOUT);
   rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!get_unaligned_le32(memory+0x11a04));
  }
  fail_stage=0;
  if(cancel==1){
   radio.peer_authorized[1]=false;rtl_ax_tx_peer_authorized(&tx,1,false);
   assert(!rtl_ax_tx_ni_retire_peer(&tx,1));
   assert(!tx.ni.route_peers[1] && !tx.ni.active && !dev.references && !fe_wifi_peer(&fe,&port,mac));
  }
  if(cancel==3){
   open_key=false;radio.security_roles[1].valid=1;
   put_unaligned_le32((1<<18)|(1<<16),memory+0xd020+16);
   assert(!rtl_ax_tx_ni_retire_peer(&tx,1));radio.security_unlink[7]=0;
   /* New DCTL role is committed, but the last TID0 template still names the old CAM. */
   radio.security_roles[1].cam[0]=7;active_key.hw_key_idx=7;radio.security_keys[7]=&active_key;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!get_unaligned_le32(memory+0x11a04) && !radio.offload_retry);
   put_unaligned_le32(7<<16|(((gcmp==1||gcmp==2)?RTL_MC_PE_KEY_GCMP:0)|(gcmp>=2?RTL_MC_PE_KEY_256:0)),memory+0xd020+20);rtl_ax_offload_ready(&radio);
  }
  rtl_ax_cleanup_work(&wiphy,&radio.cleanup_work.work);
  if(cancel==2)assert(!radio.tx_ba[1].stopping && ba_callbacks==1);
  assert(queued==&radio.offload_work);
  rtl_ax_offload_work(&wiphy,queued);
  assert(!radio.offload_retry && timers==(cancel==2?2:1));
  assert(tx.peer_offloaded[1]==(cancel!=1) && get_unaligned_le32(memory+0x11a04)==(cancel!=1));
  if(cancel!=1){
   /* Linux's synchronous key-change callback closes every PE route before
    * publishing a new key. A failed global withdrawal cannot reopen it. */
   rtl_ax_tx_key_changed(&tx);
   assert(tx.key_refresh_pending&&!get_unaligned_le32(memory+0x11a04));
   fail_stage=2;fail_index=0;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(tx.key_refresh_pending&&radio.offload_retry&&!get_unaligned_le32(memory+0x11a04));
   assert(rtl_ax_tx_ni_peer(&tx,&dev,1)==-EAGAIN);
   fail_stage=0;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!tx.key_refresh_pending&&tx.ni.route_peers[1]&&get_unaligned_le32(memory+0x11a04));
   /* A stale CAM role cannot reopen a different Linux key. Force a failed
    * Hash withdrawal first, then retry with the obsolete template present. */
   if(open_key)key_error=-EOPNOTSUPP;
   if(key_change==0)key_error=-EOPNOTSUPP;
   if(key_change==1)active_key.hw_key_idx++;
   if(key_change==2)active_key.keyidx++;
   if(key_change==3)radio.security_keys[radio.security_roles[1].cam[0]]=NULL;
   fail_stage=2;fail_index=0;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!get_unaligned_le32(memory+0x11a04) && radio.offload_retry);
   fail_stage=0;rtl_ax_offload_work(&wiphy,&radio.offload_work);
   assert(!tx.ni.route_peers[1]&&!get_unaligned_le32(memory+0x11a04)&&!radio.offload_retry);
   rtl_ax_tx_peer_authorized(&tx,1,false);assert(!rtl_ax_tx_ni_retire_peer(&tx,1));assert(!dev.references);
  }
 }
 /* Exercise the actual worker's group-only entry and retry gates. */
 {
  struct rtl_ax_tx tx={};struct wiphy w={};struct ieee80211_hw hw={&w};
  struct ieee80211_vif vif={.type=3};struct rtl_ax_ap radio={.tx=&tx,.hw=&hw,.ap_vif=&vif,.ap_started=true,.beacon_enabled=true};
  active_radio=&radio;active_tx=&tx;bcmc_calls=bcmc_closes=timers=0;
  bcmc_error=-ESTALE;rtl_ax_offload_work(&w,&radio.offload_work);
  assert(bcmc_calls==1&&bcmc_closes==1&&radio.offload_retry&&timers==1);
  bcmc_error=0;rtl_ax_offload_work(&w,&radio.offload_work);assert(bcmc_calls==2&&!radio.offload_retry);
  radio.scanning=true;rtl_ax_offload_work(&w,&radio.offload_work);assert(bcmc_calls==2);radio.scanning=false;
  radio.ap_result=-EIO;rtl_ax_offload_work(&w,&radio.offload_work);assert(bcmc_calls==2);radio.ap_result=0;
  radio.scan_offload_result=-ETIMEDOUT;bcmc_error=-ETIMEDOUT;rtl_ax_offload_work(&w,&radio.offload_work);
  assert(bcmc_calls==2&&radio.offload_retry&&radio.scan_offload_result==-ETIMEDOUT);
  bcmc_error=0;rtl_ax_offload_work(&w,&radio.offload_work);assert(bcmc_calls==3&&!radio.scan_offload_result);
  radio.security_unlink[7]=1;unsigned before=bcmc_calls,closed=bcmc_closes;
  rtl_ax_offload_work(&w,&radio.offload_work);
  assert(bcmc_calls==before&&bcmc_closes==closed+1&&radio.offload_retry);
  radio.security_unlink[7]=0;rtl_ax_offload_work(&w,&radio.offload_work);assert(bcmc_calls==before+1);

 }
 puts("FE transaction: initial/existing NI, four partial failures, prepared admission, scheduled retry and cancellation/withdrawal passed.");
}
'''
fe = read('ethernet/realtek/rtl9617c-fe.c')
ni = read('wireless/realtek/rtl_ax_ap/ni-test.h')
monitor = read('wireless/realtek/rtl_ax_ap/monitor.h')
tx = read('wireless/realtek/rtl_ax_ap/tx.c')
code = common + ''.join(function(fe, n) for n in ('fe_port', 'fe_wifi_client', 'fe_wifi_peer', 'rtl_fe_wifi_peer_route'))
code += r'''
static int rtl9617c_wifi_peer_route(struct net_device *dev,const u8 *mac,unsigned route){
 assert(get_unaligned_le32(active_tx->pe.memory+0x11a04)==!!route);
 if(route)assert(active_tx->ni.route_peers[1]);
 return rtl_fe_wifi_peer_route(active_fe,dev,mac,route);}
'''
code += function(ni, 'rtl_ax_tx_ni_route') + function(ni, 'rtl_ax_tx_ni_peer') + function(ni, 'rtl_ax_tx_ni_cleanup') + function(ni, 'ni_retire_peer') + function(ni, 'rtl_ax_tx_ni_retire_peer') + function(ni, 'rtl_ax_tx_ni_retire_ba')
code += function(tx, 'rtl_ax_tx_peer_authorized') + function(tx, 'rtl_ax_tx_key_changed') + function(tx, 'rtl_ax_tx_key_refresh')
if 'bool rtl_ax_tx_peer_security(' in tx:
    code += function(tx, 'rtl_ax_tx_peer_security')
code += function(monitor, 'rtl_ax_offload_ready') + function(monitor, 'rtl_ax_tx_ba_finish')
action = function(monitor, 'rtl_ax_ampdu_action')
prefix = action[:action.index('\tswitch (params->action)')]
stop = action[action.index('\tcase IEEE80211_AMPDU_TX_STOP_CONT:'):action.index('\tcase IEEE80211_AMPDU_RX_START:')]
code += prefix + '\t(void)cmac;(void)ba;(void)i;(void)aggregate;(void)vif;\n\tswitch(params->action){\n' + stop + 'default:return -EOPNOTSUPP;}}\n'
code += function(monitor, 'rtl_ax_offload_work') + function(monitor, 'rtl_ax_cleanup_work') + cases
with tempfile.TemporaryDirectory(prefix='wifi-offload-transaction-') as directory:
    c, exe = Path(directory) / 'test.c', Path(directory) / 'test'
    c.write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-I' + str(root / base / 'ethernet/realtek'),
                    str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
