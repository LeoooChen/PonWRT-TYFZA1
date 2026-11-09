#!/usr/bin/env python3
"""Exercise actual STA/BA callbacks with delayed RPQ and failed firmware ACKs."""
from pathlib import Path
import subprocess
import re
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'monitor.h').read_text()

def function(name):
    prefix = 'static struct ieee80211_vif *' if name == 'rtl_ax_peer_vif' else 'static void ' if name == 'rtl_ax_cleanup_work' else 'static bool ' if name == 'rtl_ax_security_cleanup' else 'static int '
    start = re.search(r'^' + re.escape(prefix) + name + r'\([^;]*?\)\s*\{', source, re.M).start()
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
#define ETH_ALEN 6
#define IEEE80211_HE_PHY_CAP6_PPE_THRESHOLD_PRESENT 128
#define IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_POS 6
#define IEEE80211_HE_MAC_CAP1_TF_MAC_PAD_DUR_MASK 0x0c
struct ieee80211_sta_he_cap {bool has_he;struct {u8 mac_cap_info[6],phy_cap_info[11];} he_cap_elem;u8 ppe_thres[25];};
#define NL80211_IFTYPE_STATION 2
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_MONITOR 6
#define RTL_AX_CLIENT_MACID 1
#define NL80211_IFTYPE_MESH_POINT 7
#define clear_bit __clear_bit
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);void(*write32)(void*,unsigned,u32);};
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
common += '#include "' + str(driver / 'cam-owner.h') + '"\n'
common += '#include "' + str(driver / 'role-identity.h') + '"\n'
common += r'''
struct wiphy {int unused;};
struct wiphy_work {int unused;};
struct wiphy_delayed_work {struct wiphy_work work;};
struct ieee80211_vif {struct rtl_ax_vif drv_priv[1];u8 addr[6];int type;bool use_4addr;struct {unsigned aid;} cfg;};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];u8 addr[6];unsigned int aid;};
struct rtl_ax_tx {bool pending[128][17];};
struct ieee80211_key_conf;
static int rtl_ax_tx_bcmc_stop(struct rtl_ax_tx *tx){(void)tx;return 0;}
static unsigned group_id,group_calls;static int group_error;
static int rtl_ax_tx_group_stop(struct rtl_ax_tx *tx,unsigned id,unsigned port,unsigned mbid){group_id=id;group_calls++;(void)port;(void)mbid;return group_error?group_error:rtl_ax_tx_bcmc_stop(tx);}
static int rtl_ax_tx_key_pn_unbind(struct rtl_ax_tx *tx,struct ieee80211_key_conf *key){(void)tx;(void)key;return 0;}
struct rtl_ax_ap {u32 vif_generation,peer_vif_generation[128];struct ieee80211_vif *vif_owner[128];struct rtl_ax_key_owner security_owner[128];u8 mbid_drop_pending;struct rtl_ax_role_identity identities[128];struct rtl_ax_cam_owners cam_owners;struct rtl_ax_self_role self_roles[128];u8 role_owner[128];unsigned traffic_peer;u8 bss_color;
 struct rtl_ax_tx *tx;struct ieee80211_vif *ap_vif,*sta_vif;int rx;bool sta_role,sta_associated,sta_4addr,peer_authorized[128],ra_registered[128],offload_retry;unsigned long ra_pending[2],security_aborting[2];u8 security_unlink[128],security_ext[128];void *security_keys[128];struct wiphy_work offload_work;unsigned keys_aborted;unsigned int chip;bool monitor_started;
 unsigned long station_ids[2],security_ids[2],security_deleting[2],preload_peers[2];
 struct {u8 self[6],peer[6];u16 aid;bool pending,client,mesh;} retired_stations[128];
 struct {u8 address[6],aggregation[16];u16 stopping,flushing;} tx_ba[128];
 struct {u8 macid,tid,cam;bool valid,deleting;} rx_ba[32];
 struct rtl_ax_security_role security_roles[128];
 struct wiphy_delayed_work cleanup_work;
 unsigned int station_removed,keys_removed,rx_ba_started,rx_ba_stopped;int rx_ba_result;
 struct {int unused;} io;
};
struct ieee80211_hw {struct rtl_ax_ap *radio;struct wiphy *wiphy;unsigned max_tx_aggregation_subframes;};
enum {IEEE80211_AMPDU_RX_START,IEEE80211_AMPDU_RX_STOP,IEEE80211_AMPDU_TX_START,
 IEEE80211_AMPDU_TX_STOP_CONT,IEEE80211_AMPDU_TX_STOP_FLUSH,
 IEEE80211_AMPDU_TX_STOP_FLUSH_CONT,IEEE80211_AMPDU_TX_OPERATIONAL};
#define IEEE80211_AMPDU_TX_START_IMMEDIATE 1
struct ieee80211_ampdu_params {struct ieee80211_sta *sta;unsigned int tid,action,buf_size,ssn;};
static unsigned int queued,callbacks,drop_commands,role_commands,cam_commands,security_commands;
static unsigned int fail_class,fail_function,fail_count;
static bool rpq_complete;
static void rtl_ax_offload_ready(struct rtl_ax_ap *r){(void)r;}
static int rtl_ax_tx_rx_retire(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static int rtl_ax_tx_ni_retire_ba(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static bool rtl_ax_tx_key_pending(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static unsigned mbid_retries;static bool mbid_fail;
static int rtl_ax_port0_drop_mbid(struct rtl_ax_ap *r,unsigned id){mbid_retries++;if(mbid_fail)return -EIO;r->mbid_drop_pending&=~BIT(id);return 0;}
static int rtl_ax_tx_ni_cleanup(struct rtl_ax_tx *t){(void)t;return 0;}
static void rtl_ax_rx_ba_window(int rx,unsigned id,unsigned tid,unsigned window){(void)rx;(void)id;(void)tid;(void)window;}
static void rtl_ax_rx_key(int rx,unsigned id,void *key){(void)rx;(void)id;(void)key;}
static void rtl_ax_tx_peer_preload(struct rtl_ax_tx *t,unsigned id,bool enable){(void)t;(void)id;(void)enable;}
static void rtl_ax_tx_peer_qos(struct rtl_ax_tx *t,unsigned id,bool qos,unsigned timeout){(void)t;(void)id;(void)qos;(void)timeout;}
static void rtl_ax_tx_peer_client(struct rtl_ax_tx *t,unsigned id,bool client,const u8 *bssid){(void)t;(void)id;(void)client;(void)bssid;}
static void rtl_ax_tx_peer_authorized(struct rtl_ax_tx *t,unsigned id,bool authorized){(void)t;(void)id;(void)authorized;}
static void rtl_ax_tx_peer_ba(struct rtl_ax_tx *t,unsigned id,unsigned tid,unsigned size){(void)t;(void)id;(void)tid;(void)size;}
static int rtl_ax_tx_peer_ssn(struct rtl_ax_tx *t,unsigned id,unsigned tid){(void)t;(void)id;(void)tid;return 0;}
static void rtl_ax_port0_field(void *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;}
static void wiphy_work_queue(struct wiphy *w,struct wiphy_work *work){(void)w;(void)work;}

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
 if(rpq_complete)for(unsigned int i=0;i<17;i++)if(tid==16 || i==tid)tx->pending[id][i]=false;
 for(unsigned int i=0;i<17;i++)if(tx->pending[id][i] && (tid==16 || i==tid))return true;
 return false;
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
 }else if(cls==8){assert(ack && len==4);if(id==7)assert((rtl_ax_ap_read32(p)&0x1e000)==0x6000);role_commands++;assert(!rtl_ax_tx_station_pending(r->tx,id,16));}
 else if(cls==6){assert(ack && len==60);cam_commands++;assert(p[53]==r->self_roles[r->role_owner[id]].color);assert(!rtl_ax_tx_station_pending(r->tx,id,16));}
 else if(cls==10){assert(ack && len==24);security_commands++;}
 else {assert((cls==12 && len==8) || (cls==5 && len==68));
  if(cls==5&&func==2&&id==7&&(rtl_ax_ap_read32(p+52)&0xf7))assert((rtl_ax_ap_read32(p+20)&0xf7)==0x50);
 }
 if(fail_count && cls==fail_class && func==fail_function){fail_count--;return -ETIMEDOUT;}
 return 0;
}
'''
common += r'''
static int rtl_ax_command_flags(struct rtl_ax_ap *r,unsigned q,unsigned c,unsigned cls,unsigned f,const u8 *p,unsigned n,bool recv,bool done){assert(!recv);return rtl_ax_command(r,q,c,cls,f,p,n,done);}

static int rtl_ax_self_role_release(struct rtl_ax_ap *r,unsigned id){r->self_roles[id].retiring=false;return 0;}
static int prepared;
static unsigned sequence_binds,sequence_unbinds;static int sequence_error,prepare_error;
static int rtl_ax_tx_sequence_bind(struct rtl_ax_tx *tx,struct ieee80211_vif *v){(void)tx;(void)v;sequence_binds++;return sequence_error;}
static int rtl_ax_tx_sequence_unbind(struct rtl_ax_tx *tx,struct ieee80211_vif *v){(void)tx;(void)v;sequence_unbinds++;return 0;}
static int rtl_ax_client_prepare(struct rtl_ax_ap *r,struct ieee80211_vif *v){(void)v;prepared++;r->sta_role=true;return prepare_error;}
static void rtl_ax_client_release(struct rtl_ax_ap *r){r->sta_role=false;}
static struct ieee80211_vif *ieee80211_vif_to_wdev(struct ieee80211_vif *v){return v;}
static void rtl_ax_tx_ap_address(struct rtl_ax_tx *tx,const u8 *addr){(void)tx;(void)addr;}
'''
pci_source=(driver/'pci.c').read_text()
a=pci_source.index('static int rtl_ax_cam_write(');b=pci_source.index('\n}\n',a)+3
common+=pci_source[a:b]
test = r'''
int main(void){
 struct wiphy w={};struct ieee80211_vif vif={.addr={2,1,2,3,4,5}};
 struct rtl_ax_tx tx={};struct rtl_ax_ap r={.tx=&tx,.ap_vif=&vif,.chip=0x52,.monitor_started=true};
 struct ieee80211_hw hw={&r,&w,128};
 r.self_roles[41].stopping=true;group_error=-EIO;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(r.self_roles[41].stopping&&group_calls==1&&group_id==41);
 group_error=0;rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(!r.self_roles[41].stopping&&group_calls==2&&group_id==41);
 r.mbid_drop_pending=BIT(3)|BIT(7);mbid_fail=true;unsigned old_queue=queued;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(r.mbid_drop_pending==(BIT(3)|BIT(7))&&mbid_retries==2&&queued==old_queue+1);
 mbid_fail=false;rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(!r.mbid_drop_pending&&mbid_retries==4);
 struct ieee80211_sta *sta=calloc(1,sizeof(*sta));assert(sta);
 r.identities[0].type=2;r.identities[7]=(struct rtl_ax_role_identity){.macid=7,.type=3,.mbid=5};
 vif.drv_priv[0].identity=r.identities[7];vif.drv_priv[0].identity.macid=0;vif.drv_priv[0].generation=1;r.peer_vif_generation[7]=1;r.vif_owner[0]=&vif;
 sta->drv_priv[0].macid=7;sta->addr[0]=2;sta->addr[5]=77;sta->aid=27;set_bit(7,r.station_ids);
 memcpy(r.tx_ba[7].address,sta->addr,6);
 /* Real non-MACID address slots and shared BSSID references survive RPQ/ACK failure. */
 r.cam_owners.role[0].reserved=true;r.cam_owners.role[7].reserved=true;
 r.cam_owners.role[7].address=5;r.cam_owners.bssid[0].references=2;
 memcpy(r.cam_owners.bssid[0].address,vif.addr,6);
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
 r.self_roles[0].color=37;r.security_roles[7].valid=1;r.security_roles[7].cam[0]=42;set_bit(42,r.security_ids);
 tx.pending[7][16]=true;rpq_complete=false;
 assert(!rtl_ax_station_remove(&hw,&vif,sta));assert(!sta->drv_priv[0].macid && r.retired_stations[7].pending);
 assert(test_bit(7,r.station_ids) && !role_commands && !cam_commands);
 free(sta);sta=NULL;
 rpq_complete=true;fail_class=6;fail_function=0;fail_count=1;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(r.retired_stations[7].pending && test_bit(7,r.station_ids));
 assert(r.cam_owners.role[7].reserved&&r.cam_owners.role[7].address==5&&r.cam_owners.bssid[0].references==2);
 assert(!r.rx_ba[0].valid && !r.security_roles[7].valid && !test_bit(42,r.security_ids));
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(!r.retired_stations[7].pending && !test_bit(7,r.station_ids) && r.station_removed==1);
 assert(!r.cam_owners.role[7].reserved&&r.cam_owners.bssid[0].references==1);
 struct ieee80211_sta client={.drv_priv={{1}},.addr={2,0,0,0,0,8}};
 vif.type=NL80211_IFTYPE_STATION;vif.cfg.aid=42;r.sta_vif=&vif;r.ap_vif=NULL;r.sta_role=r.sta_associated=true;r.identities[1]=(struct rtl_ax_role_identity){.macid=1,.type=1};
 r.cam_owners.role[0].reserved=false;r.cam_owners.role[1].reserved=true;r.cam_owners.role[1].address=0;
 set_bit(1,r.station_ids);tx.pending[1][0]=true;rpq_complete=false;
 assert(!rtl_ax_station_remove(&hw,&vif,&client)&&r.retired_stations[1].pending&&r.sta_role);
 rpq_complete=true;fail_class=6;fail_function=0;fail_count=1;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(r.sta_role&&test_bit(1,r.station_ids));
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(!r.sta_role&&!r.sta_associated&&!test_bit(1,r.station_ids));
 /* mac80211 .stop canceled cleanup after a failed STA open. Every new
  * open must finish retirement before either creating STA or selecting AP. */
 for(unsigned type=2;type<=7;type++){
  if(type!=2&&type!=3&&type!=7)continue;
  r.ap_vif=r.sta_vif=NULL;r.sta_role=true;r.retired_stations[1].pending=true;
  r.retired_stations[1].client=true;set_bit(1,r.station_ids);vif.type=type;
  r.cam_owners.role[1].reserved=true;r.cam_owners.role[1].address=0;r.cam_owners.bssid[0].references=1;
  fail_class=6;fail_function=0;fail_count=1;prepared=0;
  assert(rtl_ax_monitor_add_interface(&hw,&vif)==-ETIMEDOUT);
  assert(r.sta_role&&r.retired_stations[1].pending&&!r.ap_vif&&!r.sta_vif&&!prepared);
  /* The failure remains explicit; once firmware responds the same open recovers. */
  assert(!rtl_ax_monitor_add_interface(&hw,&vif));
  assert(!r.retired_stations[1].pending&&!test_bit(1,r.station_ids));
  assert(type==2?(r.sta_vif==&vif&&prepared==1):(r.ap_vif==&vif&&!prepared&&!r.sta_role));
  unsigned old=role_commands;
  assert(rtl_ax_monitor_add_interface(&hw,&vif)==-EBUSY&&role_commands==old);
 }
 /* Bind failure must not create a role; a later role failure must detach
  * the borrowed sequence before mac80211 can destroy its interface. */
 r.ap_vif=r.sta_vif=NULL;r.vif_owner[0]=NULL;r.sta_role=false;vif.type=NL80211_IFTYPE_STATION;
 sequence_error=-ETIMEDOUT;unsigned binds=sequence_binds,unbinds=sequence_unbinds;int tries=prepared;
 assert(rtl_ax_monitor_add_interface(&hw,&vif)==-ETIMEDOUT);
 assert(sequence_binds==binds+1&&sequence_unbinds==unbinds&&prepared==tries&&!r.sta_vif);
 sequence_error=0;prepare_error=-ETIMEDOUT;
 assert(rtl_ax_monitor_add_interface(&hw,&vif)==-ETIMEDOUT);
 assert(sequence_unbinds==unbinds+1&&!r.sta_vif&&!r.vif_owner[0]);
 prepare_error=0;
 r.vif_generation=~0U;assert(rtl_ax_monitor_add_interface(&hw,&vif)==-EOVERFLOW&&!r.vif_owner[0]);
 assert(r.retired_stations[7].peer[5]==77 && r.retired_stations[7].aid==27);
 puts("STA/BA lifetime: delayed RPQ gates completion and MACID reuse; firmware failures retry after STA free; 100 RX BA restarts reuse one slot");
}
'''
names = ('rtl_ax_peer_vif', 'rtl_ax_security_clear', 'rtl_ax_drop_station', 'rtl_ax_rx_ba_remove', 'rtl_ax_station_release',
         'rtl_ax_monitor_add_interface', 'rtl_ax_tx_ba_finish', 'rtl_ax_security_cleanup', 'rtl_ax_cleanup_work', 'rtl_ax_station_remove', 'rtl_ax_ampdu_action')
with tempfile.TemporaryDirectory(prefix='wifi-sta-release-', dir=root / 'artifacts') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(common + ''.join(function(n) for n in names) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-g', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
