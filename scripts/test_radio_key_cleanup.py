#!/usr/bin/env python3
"""Execute native key installation/deletion with TX, DMAC and CAM faults."""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--monitor', type=Path)
parser.add_argument('--reproduce', action='store_true')
parser.add_argument('--reproduce-install', action='store_true')
args = parser.parse_args()
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define WRITE_ONCE(x,v) ((x)=(v))
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT(n) BIT(n)
#define RTL_AX_AP_TRANSPORT_H
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);void(*write32)(void*,unsigned,u32);};
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
#define RTL_AX_H2C_CMD 0
#define RTL_AX_H2C_DATA 1
#define HZ 100
#define WLAN_CIPHER_SUITE_CCMP 4
#define WLAN_CIPHER_SUITE_GCMP 8
#define WLAN_CIPHER_SUITE_CCMP_256 10
#define WLAN_CIPHER_SUITE_GCMP_256 9
#define IEEE80211_KEY_FLAG_PAIRWISE BIT(0)
#define IEEE80211_KEY_FLAG_SW_MGMT_TX BIT(4)
#define IEEE80211_KEY_FLAG_SPP_AMSDU BIT(5)
#define IEEE80211_NUM_TIDS 16
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define dev_info(...) ((void)0)
#define for_each_set_bit(i,p,n) for(i=0;i<n;i++)if(p[i])
#define __set_bit(i,p) ((p)[i]=true)
#define __clear_bit(i,p) ((p)[i]=false)
#define clear_bit(i,p) __clear_bit(i,p)
#define test_bit(i,p) ((p)[i])
#define atomic64_read(p) (*(p))
enum set_key_cmd {SET_KEY,DISABLE_KEY};
struct ieee80211_key_conf {unsigned cipher,flags,keyidx,hw_key_idx;u8 key[32];u64 tx_pn;};
struct rtl_ax_station {unsigned macid;};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];};
#define NL80211_IFTYPE_STATION 2
#define NL80211_IFTYPE_MESH_POINT 7
#define RTL_AX_CLIENT_MACID 1
#include "''' + str(driver / 'role-identity.h') + r'''"
struct ieee80211_vif {int type;struct rtl_ax_vif drv_priv[1];};
struct rtl_ax_ap {u32 peer_vif_generation[128];struct rtl_ax_key_owner security_owner[128];struct rtl_ax_role_identity identities[128];u8 security_ext[128];unsigned traffic_peer;struct rtl_ax_security_role security_roles[128];
 struct ieee80211_key_conf *security_keys[128];
 bool sta_role,sta_associated;int io;
 bool security_ids[128],security_deleting[128],security_aborting[128];u8 security_unlink[128];
 int tx,rx,cleanup_work;unsigned keys_installed,keys_removed,keys_aborted;int key_result;
 struct {bool valid;unsigned macid;} rx_ba[8];
 struct {u8 self[6],peer[6];unsigned aid;bool pending,client,mesh;} retired_stations[128];
 bool ra_registered[128],ra_pending[128],station_ids[128],preload_peers[128];unsigned station_removed;
};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
enum fault {NONE,RETIRE,FLUSH,DCTL,CAM,CAM_INSTALL,PN_BIND,PN_UNBIND,HIQ_STOP};
static enum fault fault;
static bool hiq_pending;static unsigned hiq_stops;
static unsigned failures,queued,clears,dctls,offload_ready,fault_skip;
static bool apply_failed_dctl,apply_failed_cam,hardware_cam[128];
static u8 hardware_key_tag[128],hardware_cipher[128];
static unsigned key_cipher=4,key_spp;
static u64 hardware_pn[128];
static struct rtl_ax_security_role hardware_roles[128];
static int fail(enum fault point){if(fault!=point||!failures)return 0;if(fault_skip){fault_skip--;return 0;}failures--;return -ETIMEDOUT;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static unsigned find_first_zero_bit(bool *p,unsigned n){unsigned i;for(i=0;i<n&&p[i];i++);return i;}
static bool rtl_ax_tx_key_pending(int tx,unsigned index){(void)tx;assert(index<128);return hiq_pending||fail(FLUSH)!=0;}
static int rtl_ax_tx_flush_key(int tx,unsigned index){(void)tx;assert(index<128);return hiq_pending?-ETIMEDOUT:fail(FLUSH);}
static int rtl_ax_tx_key_pn_bind(int tx,struct ieee80211_key_conf *k){(void)tx;(void)k;return fail(PN_BIND);}
static int rtl_ax_tx_key_pn_unbind(int tx,struct ieee80211_key_conf *k){(void)tx;(void)k;return fail(PN_UNBIND);}
static int rtl_ax_tx_bcmc_stop(int tx){
 (void)tx;hiq_stops++;int ret=fail(HIQ_STOP);if(!ret)hiq_pending=false;return ret;
}
static unsigned group_macid,group_port,group_mbid;
static int rtl_ax_tx_group_stop(int tx,unsigned macid,unsigned port,unsigned mbid){group_macid=macid;group_port=port;group_mbid=mbid;return rtl_ax_tx_bcmc_stop(tx);}
struct key_wdev {void *netdev;};static struct key_wdev key_wdev;
#define ieee80211_vif_to_wdev(v) ((void)(v),&key_wdev)
static int rtl_ax_tx_ni_route(int tx,void *dev,unsigned id){(void)tx;(void)dev;assert(!id);return fail(RETIRE);}
static int rtl_ax_tx_ni_retire_peer(int tx,unsigned id){(void)tx;assert(id<128);return fail(RETIRE);}
static void rtl_ax_rx_key(int rx,unsigned id,struct ieee80211_key_conf *key){(void)rx;(void)id;(void)key;}
static void rtl_ax_offload_ready(void *r){(void)r;offload_ready++;}
static void wiphy_delayed_work_queue(void *w,int *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);queued++;}
static int rtl_ax_command_flags(struct rtl_ax_ap *r,unsigned queue,unsigned cat,unsigned cls,unsigned func,
 const u8 *data,unsigned bytes,bool receive,bool done){
 (void)r;assert(cat==1 && !receive && done);
 if(cls==5){
  unsigned id=data[0]&127;int ret=fail(DCTL);assert(queue==1&&func==9&&bytes==68);dctls++;
  if(!ret||apply_failed_dctl){
   hardware_roles[id].valid=data[24];memcpy(hardware_roles[id].cam,data+25,3);memcpy(hardware_roles[id].cam+3,data+28,4);
   u32 low=(u32)(hardware_pn[id]&0xffff)<<16,high=hardware_pn[id]>>16;
   low=(low&~rtl_ax_ap_read32(data+36))|(rtl_ax_ap_read32(data+4)&rtl_ax_ap_read32(data+36));
   high=(high&~rtl_ax_ap_read32(data+40))|(rtl_ax_ap_read32(data+8)&rtl_ax_ap_read32(data+40));
   hardware_pn[id]=(u64)high<<16|low>>16;
  }
  return ret;
 }
 if(cls==8||cls==6)return 0; /* Station role/address release is outside the key model. */
 assert(queue==0&&cls==10&&func==1&&bytes==24);
 unsigned index=data[0];
 if(!rtl_ax_ap_read32(data+4)) {
  clears++;int ret=fail(CAM);if(ret)return ret;
  for(unsigned id=0;id<128;id++)for(unsigned slot=0;slot<7;slot++)
   assert(!(hardware_roles[id].valid&BIT(slot))||hardware_roles[id].cam[slot]!=index);
  hardware_cam[index]=false;hardware_cipher[index]=0;
 } else {
  int ret=fail(CAM_INSTALL);
  if(!ret||apply_failed_cam){hardware_cam[index]=true;hardware_key_tag[index]=data[8];hardware_cipher[index]=data[4];assert(data[4]==(rtl_ax_security_type(key_cipher)|((key_cipher==9||key_cipher==10)?16:0)|(key_spp?32:0)));}
  return ret;
 }
 return 0;
}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,
 const u8 *data,unsigned len,bool done){return rtl_ax_command_flags(r,q,cat,cls,fn,data,len,false,done);}
static int rtl_ax_cam_write(struct rtl_ax_ap *r,u8 cam[60]){return rtl_ax_command(r,1,1,6,0,cam,60,true);}
static int rtl_ax_drop_station(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;return 0;}
static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *r,unsigned i){r->rx_ba[i].valid=false;return 0;}
static void rtl_ax_peer_cam(u8 *out,unsigned id,const u8 *self,const u8 *peer,unsigned aid,bool valid){
 (void)out;(void)id;(void)self;(void)peer;(void)aid;(void)valid;}
static void rtl_ax_tx_peer_preload(int tx,unsigned id,bool enable){(void)tx;(void)id;(void)enable;}
static void rtl_ax_tx_peer_qos(int tx,unsigned id,bool qos,unsigned timeout){(void)tx;(void)id;(void)qos;(void)timeout;}
static void rtl_ax_client_cam(u8 *out,unsigned id,const u8 *self,const u8 *peer,unsigned aid,bool assoc,bool valid){
 (void)assoc;rtl_ax_peer_cam(out,id,self,peer,aid,valid);}
static void rtl_ax_tx_peer_client(int tx,unsigned id,bool client,const u8 *bssid){(void)tx;(void)id;(void)client;(void)bssid;}
static void rtl_ax_port0_field(int *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;}
static void reset(void){hiq_pending=false;hiq_stops=0;fault=NONE;fault_skip=0;failures=queued=clears=dctls=offload_ready=0;apply_failed_dctl=apply_failed_cam=false;
 memset(hardware_pn,0,sizeof(hardware_pn));memset(hardware_key_tag,0,sizeof(hardware_key_tag));
 memset(hardware_cam,0,sizeof(hardware_cam));memset(hardware_roles,0,sizeof(hardware_roles));}
static struct ieee80211_key_conf new_key(unsigned idx,bool pairwise){
 struct ieee80211_key_conf k={.cipher=key_cipher,.keyidx=idx,.key={1},.tx_pn=1234};
 if(pairwise)k.flags=IEEE80211_KEY_FLAG_PAIRWISE;
 if(key_spp)k.flags|=IEEE80211_KEY_FLAG_SPP_AMSDU;return k;}
'''
role=(driver/'role.h').read_text()
a=role.index('static inline void rtl_ax_station_preload');b=role.index('static inline ',a+1)
common += role[a:b]

reproduce = r'''
int main(void){
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_key_conf key=new_key(0,true);
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&key));fault=DCTL;failures=1;
 assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&key)==-ETIMEDOUT);
 bool retry=rtl_ax_security_cleanup(&r);
 printf("{\"queued\":%u,\"retry\":%s,\"software_role_valid\":%u,\"hardware_role_valid\":%u,\"CAM_owned\":%s,\"CAM_present\":%s,\"pending_unlink\":%u}\n",
 queued,retry?"true":"false",r.security_roles[7].valid,hardware_roles[7].valid,
 r.security_ids[0]?"true":"false",hardware_cam[0]?"true":"false",r.security_unlink[0]);
}
'''

reproduce_install = r'''
int main(void){
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
 struct ieee80211_sta first={{{7}}},second={{{8}}};
 struct ieee80211_key_conf failed=new_key(0,true),next=new_key(0,true);next.key[0]=2;
 fault=DCTL;failures=1;apply_failed_dctl=true;
 assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&first,&failed)==-ETIMEDOUT);
 bool retained=r.security_ids[0];fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&second,&next));
 bool foreign=(hardware_roles[7].valid&1)&&hardware_key_tag[hardware_roles[7].cam[0]]==2;
 assert(!rtl_ax_security_cleanup(&r));
 printf("{\"failed_CAM_retained\":%s,\"new_peer_CAM\":%u,\"failed_peer_points_to_foreign_key\":%s,\"failed_peer_valid_after_cleanup\":%u,\"new_peer_key_tag\":%u,\"aborted_install_reclaimed\":%u}\n",
 retained?"true":"false",next.hw_key_idx,foreign?"true":"false",hardware_roles[7].valid,
 hardware_key_tag[hardware_roles[8].cam[0]],r.keys_aborted);
}
'''
install_tests = r'''
static void install_tests(void){
 for(unsigned spp=0;spp<2;spp++)for(unsigned alg=0;alg<2;alg++){key_cipher=alg?8:4;key_spp=spp;
 /* An unsupported software PTK still retires the previous NI template. */
 for(unsigned failure=0;failure<2;failure++)for(unsigned pairwise=0;pairwise<2;pairwise++) {
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  struct ieee80211_key_conf key=new_key(0,pairwise);key.cipher=12;
  fault=RETIRE;failures=failure?2:1;
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},pairwise?&sta:NULL,&key)==-ETIMEDOUT);
  assert(failures==failure&&!r.security_ids[0]);
  fault=NONE;
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},pairwise?&sta:NULL,&key)==-EOPNOTSUPP&&!r.security_ids[0]);
 }
 /* A PN binding error follows a successful DMAC installation. Keep the
  * failed CAM owned until cleanup restores the prior desired role. */
 for(unsigned removal=0;removal<2;removal++){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
  struct ieee80211_key_conf key=new_key(1,false);key.hw_key_idx=99;
  if(removal)assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},0,&key));
  fault=removal?PN_UNBIND:PN_BIND;failures=1;
  assert(rtl_ax_set_key(&hw,removal?DISABLE_KEY:SET_KEY,&(struct ieee80211_vif){},0,&key)==-ETIMEDOUT);
  assert(r.security_ids[0]&&r.security_deleting[0]&&r.security_unlink[0]&&!r.security_keys[0]);
  assert(!r.security_roles[0].valid&&key.hw_key_idx==(removal?0U:99U));
  assert(hardware_roles[0].valid&&hardware_cam[0]);
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(!hardware_roles[0].valid&&!hardware_cam[0]&&!r.security_ids[0]);
 }
 for(unsigned n=0;n<4;n++){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
  struct ieee80211_sta first={{{7}}},second={{{8}}};
  struct ieee80211_key_conf failed=new_key(0,true),next=new_key(0,true);
  failed.hw_key_idx=99;next.key[0]=9;fault=n<2?CAM_INSTALL:DCTL;failures=1;
  apply_failed_cam=n==1;apply_failed_dctl=n==3;
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&first,&failed)==-ETIMEDOUT);
  assert(r.key_result==-ETIMEDOUT&&failed.hw_key_idx==99&&!(failed.flags&IEEE80211_KEY_FLAG_SW_MGMT_TX));
  assert(!r.security_roles[7].valid&&r.security_ids[0]&&r.security_deleting[0]&&r.security_aborting[0]&&queued==1);
  fault=NONE;assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&second,&next)&&next.hw_key_idx==1);
  assert(!r.security_aborting[1]&&!r.security_deleting[1]&&!r.security_unlink[1]);
  u64 pn=hardware_pn[7];fault=n<2?CAM:DCTL;failures=1;
  assert(rtl_ax_security_cleanup(&r)&&r.security_ids[0]&&r.security_aborting[0]);
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(!r.security_ids[0]&&!r.security_aborting[0]&&!r.security_deleting[0]&&!r.security_unlink[0]);
  assert(!hardware_roles[7].valid&&!hardware_cam[0]&&hardware_pn[7]==pn);
  assert(hardware_roles[8].valid==1&&hardware_roles[8].cam[0]==1&&hardware_key_tag[1]==9);
  assert(r.keys_installed==1&&r.keys_removed==0&&r.keys_aborted==1);
 }
 /* A later PTK on the same station keeps its current hardware PN on cleanup. */
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_key_conf failed=new_key(0,true),next=new_key(0,true);next.key[0]=9;next.tx_pn=98765;
 fault=DCTL;failures=1;apply_failed_dctl=true;
 assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&failed)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&next)&&next.hw_key_idx==1);
 assert(hardware_pn[7]==next.tx_pn);assert(!rtl_ax_security_cleanup(&r));
 assert(hardware_roles[7].valid==1&&hardware_roles[7].cam[0]==1&&hardware_pn[7]==next.tx_pn&&offload_ready==1);
 /* Other GTKs and their PN remain intact when a failed GTK is reclaimed. */
 reset();memset(&r,0,sizeof(r));struct ieee80211_key_conf a=new_key(1,false),b=new_key(0,false),c=new_key(2,false);
 hardware_pn[0]=0x123456789abcULL;
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},0,&a));fault=DCTL;failures=1;apply_failed_dctl=true;
 assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},0,&b)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},0,&c)&&c.hw_key_idx==2);
 assert(!rtl_ax_security_cleanup(&r)&&hardware_roles[0].valid==(BIT(3)|BIT(4)));
 assert(hardware_roles[0].cam[3]==0&&hardware_roles[0].cam[4]==2&&!hardware_cam[1]);
 assert(hardware_pn[0]==0x123456789abcULL&&r.keys_installed==2&&r.keys_removed==0&&r.keys_aborted==1);
 }key_cipher=4;key_spp=0;
 puts("key install: CAM/DMAC ACK both outcomes, reserved CAM ownership, failed retry, PTK/GTK replacement PN and abort accounting passed");
}
'''
extended_tests = r'''
static void extended_tests(void){
 for(unsigned spp=0;spp<2;spp++)for(unsigned alg=9;alg<=10;alg++)for(unsigned pairwise=0;pairwise<2;pairwise++)
 for(unsigned keyid=0;keyid<(pairwise?2U:4U);keyid++)for(unsigned failure=0;failure<6;failure++) {
  reset();key_cipher=alg;key_spp=spp;struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  unsigned macid=pairwise?7:0;
  /* Force two nonadjacent free CAMs. */
  for(unsigned i=0;i<128;i++)r.security_ids[i]=true;
  r.security_ids[7]=r.security_ids[63]=false;
  struct ieee80211_key_conf key=new_key(keyid,pairwise);key.hw_key_idx=99;
  for(unsigned i=0;i<32;i++)key.key[i]=i+1;
  if(failure==1||failure==2){fault=CAM_INSTALL;failures=1;fault_skip=failure-1;apply_failed_cam=true;}
  if(failure==3){fault=DCTL;failures=1;apply_failed_dctl=true;}
  if(failure==4){fault=PN_BIND;failures=1;}
  int ret=rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},pairwise?&sta:NULL,&key);
  assert(r.security_ids[7]&&r.security_ids[63]&&r.security_ext[7]==64);
  if(failure>=1&&failure<=4) {
   assert(ret==-ETIMEDOUT&&key.hw_key_idx==99&&r.security_deleting[7]&&!r.security_keys[7]);
   fault=CAM;failures=1;fault_skip=1;
   assert(rtl_ax_security_cleanup(&r)&&r.security_ids[7]&&r.security_ids[63]&&r.security_ext[7]==64);
   fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  } else {
   assert(!ret&&key.hw_key_idx==7&&r.security_keys[7]==&key);
   unsigned seen=0;
   for(unsigned slot=0;slot<7;slot++)if(r.security_roles[macid].valid&BIT(slot)) {
    assert(r.security_roles[macid].keyid[slot]==keyid);
    seen|=r.security_roles[macid].cam[slot]==7?1:r.security_roles[macid].cam[slot]==63?2:0;
   }
   assert(seen==3&&hardware_key_tag[7]==1&&hardware_key_tag[63]==17);
   if(failure==5) {
    r.retired_stations[macid].pending=true;
    assert(!rtl_ax_station_release(&r,macid));
   } else {
    fault=CAM;failures=1;fault_skip=1;
    assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},pairwise?&sta:NULL,&key)==-ETIMEDOUT);
    assert(r.security_ids[7]&&r.security_ids[63]&&r.security_ext[7]==64);
    fault=NONE;assert(!rtl_ax_security_cleanup(&r));
   }
  }
  assert(!r.security_ids[7]&&!r.security_ids[63]&&!r.security_ext[7]&&!hardware_cam[7]&&!hardware_cam[63]);
  for(unsigned i=0;i<128;i++)if(i!=7&&i!=63)assert(r.security_ids[i]);
 }
 for(unsigned alg=9;alg<=10;alg++) {
  reset();key_cipher=alg;struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  struct ieee80211_key_conf first=new_key(0,true),same=new_key(0,true),second=new_key(1,true);
  for(unsigned i=0;i<128;i++)r.security_ids[i]=true;r.security_ids[127]=false;
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&first)==-ENOSPC);
  assert(!r.security_ids[127]&&!r.security_deleting[127]&&!r.security_ext[127]);
  memset(r.security_ids,0,sizeof(r.security_ids));
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&first));
  unsigned before=dctls;
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&same)==-ENOSPC);
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&second)==-ENOSPC&&dctls==before);
  assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&first));
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&second));
  assert(r.security_roles[7].valid==3&&r.security_roles[7].keyid[0]==1&&r.security_roles[7].keyid[1]==1);
  assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&second));
  struct ieee80211_key_conf a=new_key(0,false),b=new_key(1,false),c=new_key(2,false);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&a));
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&b));
  assert(r.security_roles[0].valid==0x3c);
  assert(rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&c)==-ENOSPC);
  assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},NULL,&a));
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&c));
  assert(r.security_keys[b.hw_key_idx]==&b&&r.security_roles[0].valid==0x3c);
 }
 key_cipher=4;key_spp=0;
 for(unsigned alg=0;alg<4;alg++)for(unsigned initial=0;initial<2;initial++) {
  reset();key_cipher=(unsigned[]){4,8,9,10}[alg];key_spp=initial;
  struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  struct ieee80211_key_conf old=new_key(0,true);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&old));
  unsigned primary=old.hw_key_idx,ext=r.security_ext[primary];
  assert(!!(hardware_cipher[primary]&32)==initial);
  if(ext)assert(!!(hardware_cipher[ext-1]&32)==initial);
  fault=CAM;failures=1;fault_skip=!!ext;
  assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&old)==-ETIMEDOUT);
  assert(r.security_ids[primary]&&(!ext||r.security_ids[ext-1]));
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(!hardware_cipher[primary]&&(!ext||!hardware_cipher[ext-1]));
  key_spp=!initial;struct ieee80211_key_conf next=new_key(0,true);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&next));
  assert(next.hw_key_idx==primary&&r.security_ext[primary]==ext);
  assert(!!(hardware_cipher[primary]&32)==!initial);
  if(ext)assert(!!(hardware_cipher[ext-1]&32)==!initial);
 }
 key_cipher=4;key_spp=0;
 puts("Extended keys:144 nonadjacent dual-CAM install/first-half/second-half/DCTL/PN/erase/station-release groups passed");
}
'''
test = r'''
int main(void){
 install_tests();extended_tests();
 for(unsigned n=0;n<5;n++){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  struct ieee80211_key_conf key=new_key(0,true);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&key));
  fault=n==0?RETIRE:n==1?FLUSH:n==4?CAM:DCTL;failures=1;apply_failed_dctl=n==3;
  assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&key)==-ETIMEDOUT);
  assert(r.key_result==-ETIMEDOUT&&!r.security_roles[7].valid&&r.security_ids[0]&&r.security_deleting[0]&&queued==1);
  unsigned cleared=clears;failures=1;assert(rtl_ax_security_cleanup(&r));
  assert(r.security_ids[0]&&r.security_deleting[0]&&hardware_cam[0]);
  if(fault!=CAM)assert(clears==cleared);
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(!r.security_ids[0]&&!r.security_deleting[0]&&!r.security_unlink[0]&&!hardware_cam[0]&&!hardware_roles[7].valid);
 }
 /* An AP GTK cannot unlink while its old packets are retained in HIQ.
  * Linux may destroy the key after DISABLE_KEY fails: cleanup owns retry. */
 for(unsigned failed=0;failed<2;failed++) {
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
  struct ieee80211_key_conf old=new_key(1,false),next=new_key(2,false);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&old));
  hiq_pending=true;if(failed){fault=HIQ_STOP;failures=1;}
  int ret=rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},NULL,&old);
  assert(ret==(failed?-ETIMEDOUT:0)&&hiq_stops==1);
  if(failed) {
   assert(hiq_pending&&r.security_unlink[0]==1&&r.security_ids[0]&&hardware_cam[0]&&!r.security_keys[0]);
   fault=NONE;assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},NULL,&next)&&next.hw_key_idx==1);
   fault=HIQ_STOP;failures=1;assert(rtl_ax_security_cleanup(&r)&&hiq_pending&&hardware_cam[0]);
   fault=NONE;assert(!rtl_ax_security_cleanup(&r)&&!hiq_pending&&!hardware_cam[0]);
   assert(hardware_roles[0].cam[4]==1&&hardware_cam[1]);
  } else assert(!hiq_pending&&!hardware_cam[0]&&!r.security_ids[0]&&offload_ready==1);
 }
 /* New keys in the same MACID survive an older failed unlink. */
 for(unsigned id=0;id<=127;id+=127){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{id}}};
  struct ieee80211_sta *peer=id?&sta:0;
  struct ieee80211_key_conf old=new_key(0,id!=0),next=new_key(1,id!=0);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},peer,&old));fault=DCTL;failures=1;
  assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},peer,&old)==-ETIMEDOUT);
  assert(r.security_unlink[0]==id+1);fault=NONE;
  assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},peer,&next)&&next.hw_key_idx==1);
  assert(!rtl_ax_security_cleanup(&r));unsigned slot=id?1:3;
  assert(hardware_roles[id].valid==BIT(slot)&&hardware_roles[id].cam[slot]==1&&hardware_cam[1]);
  assert(offload_ready==1);
 }
 /* Station release resolves pending unlinks before MACID reuse. */
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_key_conf old=new_key(0,true),next=new_key(0,true);
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&old));fault=DCTL;failures=1;
 assert(rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&old)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_station_release(&r,7)&&!r.security_unlink[0]&&!hardware_roles[7].valid);
 assert(!rtl_ax_set_key(&hw,SET_KEY,&(struct ieee80211_vif){},&sta,&next)&&next.hw_key_idx==1);
 unsigned before=dctls;assert(!rtl_ax_security_cleanup(&r)&&dctls==before);
 assert(hardware_roles[7].valid==1&&hardware_roles[7].cam[0]==1&&hardware_cam[1]);
 assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&(struct ieee80211_vif){},&sta,&next)&&!r.security_ids[1]);
 /* A late interface GTK deletion cannot touch a reused hardware CAM. */
 reset();memset(&r,0,sizeof(r));struct ieee80211_vif vif={.type=NL80211_IFTYPE_STATION,.drv_priv={{{.macid=1,.type=1}}}};
 struct ieee80211_key_conf gtk=new_key(1,false),fresh=new_key(0,true);
 sta.drv_priv[0].macid=1;r.retired_stations[1].client=true;
 assert(!rtl_ax_set_key(&hw,SET_KEY,&vif,0,&gtk));assert(hardware_roles[1].valid==BIT(3));
 assert(!rtl_ax_station_release(&r,1));assert(!r.security_keys[gtk.hw_key_idx]);
 assert(!rtl_ax_set_key(&hw,SET_KEY,&vif,&sta,&fresh));assert(fresh.hw_key_idx==gtk.hw_key_idx);
 before=dctls;assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&vif,0,&gtk));
 assert(dctls==before && hardware_roles[1].valid==1 && hardware_cam[fresh.hw_key_idx]);
 assert(r.security_keys[fresh.hw_key_idx]==&fresh);
 /* Two VAP GTKs with identical key IDs must never share DMAC/PN ownership. */
 for(unsigned alg=0;alg<4;alg++)for(unsigned point=0;point<5;point++){
  reset();key_cipher=(unsigned[]){4,8,10,9}[alg];struct rtl_ax_ap multi={};struct ieee80211_hw mh={&multi,0};
  struct ieee80211_vif a={.drv_priv={{{.macid=41,.mbid=3,.type=3}}}},b={.drv_priv={{{.macid=63,.mbid=5,.type=3}}}};
  struct ieee80211_key_conf ka=new_key(1,false),kb=new_key(1,false),next=new_key(2,false);kb.key[0]=11;
  assert(!rtl_ax_set_key(&mh,SET_KEY,&a,NULL,&ka));assert(!rtl_ax_set_key(&mh,SET_KEY,&b,NULL,&kb));
  unsigned ca=ka.hw_key_idx,cb=kb.hw_key_idx;assert(ca!=cb&&multi.security_owner[ca].macid==41&&multi.security_owner[cb].macid==63);
  struct rtl_ax_security_role keep=hardware_roles[63];u64 pn=hardware_pn[63];
  a.drv_priv[0].identity=b.drv_priv[0].identity; /* delayed callback after identity changed */
  fault=(enum fault[]){PN_UNBIND,HIQ_STOP,FLUSH,DCTL,CAM}[point];failures=1;apply_failed_dctl=true;
  assert(rtl_ax_set_key(&mh,DISABLE_KEY,&a,NULL,&ka)==-ETIMEDOUT);
  assert(multi.security_ids[ca]&&!multi.security_keys[ca]&&multi.security_owner[ca].macid==41);
  assert(!memcmp(&keep,&hardware_roles[63],sizeof(keep))&&hardware_pn[63]==pn&&hardware_cam[cb]);
  fault=NONE;assert(!rtl_ax_set_key(&mh,SET_KEY,&b,NULL,&next));
  keep=hardware_roles[63];assert(!rtl_ax_security_cleanup(&multi));
  assert(!hardware_cam[ca]&&!multi.security_ids[ca]&&!multi.security_owner[ca].group);
  assert(!memcmp(&keep,&hardware_roles[63],sizeof(keep))&&hardware_cam[cb]);
  if(point<4)assert(group_macid==41&&group_port==0&&group_mbid==3);
 }
 /* Partial group installation must restore only the original self role. */
 for(unsigned alg=0;alg<4;alg++)for(unsigned stage=0;stage<2;stage++){
  reset();key_cipher=(unsigned[]){4,8,10,9}[alg];struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
  struct ieee80211_vif va={.drv_priv={{{.macid=41,.mbid=3,.type=3}}}},vb={.drv_priv={{{.macid=63,.mbid=5,.type=3}}}};
  struct ieee80211_key_conf first=new_key(1,false),bad=new_key(2,false),other=new_key(1,false);
  assert(!rtl_ax_set_key(&hw,SET_KEY,&va,NULL,&first));assert(!rtl_ax_set_key(&hw,SET_KEY,&vb,NULL,&other));
  struct rtl_ax_security_role saved=hardware_roles[63];
  fault=stage?PN_BIND:DCTL;failures=1;apply_failed_dctl=true;
  assert(rtl_ax_set_key(&hw,SET_KEY,&va,NULL,&bad)==-ETIMEDOUT);
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(group_macid==41&&group_mbid==3&&!memcmp(&saved,&hardware_roles[63],sizeof(saved)));
  assert(r.security_keys[first.hw_key_idx]==&first&&r.security_keys[other.hw_key_idx]==&other);
  /* Old deletion works even when the later interface is invalid/unassigned. */
  va.drv_priv[0].identity.macid=255;
  assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&va,NULL,&first));
  assert(!memcmp(&saved,&hardware_roles[63],sizeof(saved)));
 }
 puts("Per-VIF GTK: 28 dual-interface same-key-ID and stale-callback/install/delete/fault/retry scenarios passed");
 puts("key cleanup: TX retirement/drain, DMAC ACK both outcomes, CAM failure, newer PTK/GTK, MACID127 and station reuse passed");
}
'''
with tempfile.TemporaryDirectory(prefix='radio-key-cleanup-') as directory:
    path = Path(directory)
    source = (args.monitor or driver / 'monitor.h').read_text()
    functions = ''
    for prefix, name in (('static int ', 'rtl_ax_security_clear'), ('static int ', 'rtl_ax_station_release'),
                         ('static bool ', 'rtl_ax_security_cleanup'), ('static int ', 'rtl_ax_set_key')):
        start = re.search(r'^' + prefix + name + r'\([^;]*?\)\s*\{', source, re.M).start()
        end = source.index('\n}\n', start) + 3
        functions += source[start:end] + '\n'
    before, after = common.split('enum set_key_cmd', 1)
    c, binary = path / 'key.c', path / 'key'
    c.write_text(before + '#include "' + str(driver / 'security.h') + '"\n' +
                 'enum set_key_cmd' + after + functions + install_tests + extended_tests + (reproduce_install if args.reproduce_install else reproduce if args.reproduce else test))
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
