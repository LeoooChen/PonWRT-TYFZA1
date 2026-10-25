#!/usr/bin/env python3
"""Execute native key installation/deletion with TX, DMAC and CAM faults."""
from pathlib import Path
import argparse
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
#define IEEE80211_KEY_FLAG_PAIRWISE BIT(0)
#define IEEE80211_KEY_FLAG_SW_MGMT_TX BIT(4)
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
struct ieee80211_key_conf {unsigned cipher,flags,keyidx,hw_key_idx;u8 key[16];u64 tx_pn;};
struct rtl_ax_station {unsigned macid;};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];};
struct ieee80211_vif {int dummy;};
struct rtl_ax_ap {struct rtl_ax_security_role security_roles[128];
 bool security_ids[128],security_deleting[128],security_aborting[128];u8 security_unlink[128];
 int tx,rx,cleanup_work;unsigned keys_installed,keys_removed,keys_aborted;int key_result;
 struct {bool valid;unsigned macid;} rx_ba[8];
 struct {u8 self[6],peer[6];unsigned aid;bool pending;} retired_stations[128];
 bool ra_registered[128],ra_pending[128],station_ids[128];unsigned station_removed;
};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
enum fault {NONE,RETIRE,FLUSH,DCTL,CAM,CAM_INSTALL};
static enum fault fault;
static unsigned failures,queued,clears,dctls,offload_ready;
static bool apply_failed_dctl,apply_failed_cam,hardware_cam[128];
static u8 hardware_key_tag[128];
static u64 hardware_pn[128];
static struct rtl_ax_security_role hardware_roles[128];
static int fail(enum fault point){if(fault!=point||!failures)return 0;failures--;return -ETIMEDOUT;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static unsigned find_first_zero_bit(bool *p,unsigned n){unsigned i;for(i=0;i<n&&p[i];i++);return i;}
static int rtl_ax_tx_flush_key(int tx,unsigned index){(void)tx;assert(index<128);return fail(FLUSH);}
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
  hardware_cam[index]=false;
 } else {
  int ret=fail(CAM_INSTALL);
  if(!ret||apply_failed_cam){hardware_cam[index]=true;hardware_key_tag[index]=data[8];}
  return ret;
 }
 return 0;
}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,
 const u8 *data,unsigned len,bool done){return rtl_ax_command_flags(r,q,cat,cls,fn,data,len,false,done);}
static int rtl_ax_drop_station(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;return 0;}
static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *r,unsigned i){r->rx_ba[i].valid=false;return 0;}
static void rtl_ax_peer_cam(u8 *out,unsigned id,const u8 *self,const u8 *peer,unsigned aid,bool valid){
 (void)out;(void)id;(void)self;(void)peer;(void)aid;(void)valid;}
static void rtl_ax_tx_peer_qos(int tx,unsigned id,bool qos,unsigned timeout){(void)tx;(void)id;(void)qos;(void)timeout;}
static void reset(void){fault=NONE;failures=queued=clears=dctls=offload_ready=0;apply_failed_dctl=apply_failed_cam=false;
 memset(hardware_pn,0,sizeof(hardware_pn));memset(hardware_key_tag,0,sizeof(hardware_key_tag));
 memset(hardware_cam,0,sizeof(hardware_cam));memset(hardware_roles,0,sizeof(hardware_roles));}
static struct ieee80211_key_conf new_key(unsigned idx,bool pairwise){
 struct ieee80211_key_conf k={.cipher=WLAN_CIPHER_SUITE_CCMP,.keyidx=idx,.key={1},.tx_pn=1234};
 if(pairwise)k.flags=IEEE80211_KEY_FLAG_PAIRWISE;return k;}
'''
reproduce = r'''
int main(void){
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_key_conf key=new_key(0,true);
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&key));fault=DCTL;failures=1;
 assert(rtl_ax_set_key(&hw,DISABLE_KEY,0,&sta,&key)==-ETIMEDOUT);
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
 assert(rtl_ax_set_key(&hw,SET_KEY,0,&first,&failed)==-ETIMEDOUT);
 bool retained=r.security_ids[0];fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&second,&next));
 bool foreign=(hardware_roles[7].valid&1)&&hardware_key_tag[hardware_roles[7].cam[0]]==2;
 assert(!rtl_ax_security_cleanup(&r));
 printf("{\"failed_CAM_retained\":%s,\"new_peer_CAM\":%u,\"failed_peer_points_to_foreign_key\":%s,\"failed_peer_valid_after_cleanup\":%u,\"new_peer_key_tag\":%u,\"aborted_install_reclaimed\":%u}\n",
 retained?"true":"false",next.hw_key_idx,foreign?"true":"false",hardware_roles[7].valid,
 hardware_key_tag[hardware_roles[8].cam[0]],r.keys_aborted);
}
'''
install_tests = r'''
static void install_tests(void){
 for(unsigned n=0;n<4;n++){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
  struct ieee80211_sta first={{{7}}},second={{{8}}};
  struct ieee80211_key_conf failed=new_key(0,true),next=new_key(0,true);
  failed.hw_key_idx=99;next.key[0]=9;fault=n<2?CAM_INSTALL:DCTL;failures=1;
  apply_failed_cam=n==1;apply_failed_dctl=n==3;
  assert(rtl_ax_set_key(&hw,SET_KEY,0,&first,&failed)==-ETIMEDOUT);
  assert(r.key_result==-ETIMEDOUT&&failed.hw_key_idx==99&&!(failed.flags&IEEE80211_KEY_FLAG_SW_MGMT_TX));
  assert(!r.security_roles[7].valid&&r.security_ids[0]&&r.security_deleting[0]&&r.security_aborting[0]&&queued==1);
  fault=NONE;assert(!rtl_ax_set_key(&hw,SET_KEY,0,&second,&next)&&next.hw_key_idx==1);
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
 assert(rtl_ax_set_key(&hw,SET_KEY,0,&sta,&failed)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&next)&&next.hw_key_idx==1);
 assert(hardware_pn[7]==next.tx_pn);assert(!rtl_ax_security_cleanup(&r));
 assert(hardware_roles[7].valid==1&&hardware_roles[7].cam[0]==1&&hardware_pn[7]==next.tx_pn&&offload_ready==1);
 /* Other GTKs and their PN remain intact when a failed GTK is reclaimed. */
 reset();memset(&r,0,sizeof(r));struct ieee80211_key_conf a=new_key(1,false),b=new_key(0,false),c=new_key(2,false);
 hardware_pn[0]=0x123456789abcULL;
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,0,&a));fault=DCTL;failures=1;apply_failed_dctl=true;
 assert(rtl_ax_set_key(&hw,SET_KEY,0,0,&b)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,0,&c)&&c.hw_key_idx==2);
 assert(!rtl_ax_security_cleanup(&r)&&hardware_roles[0].valid==(BIT(3)|BIT(4)));
 assert(hardware_roles[0].cam[3]==0&&hardware_roles[0].cam[4]==2&&!hardware_cam[1]);
 assert(hardware_pn[0]==0x123456789abcULL&&r.keys_installed==2&&r.keys_removed==0&&r.keys_aborted==1);
 puts("key install: CAM/DMAC ACK both outcomes, reserved CAM ownership, failed retry, PTK/GTK replacement PN and abort accounting passed");
}
'''
test = r'''
int main(void){
 install_tests();
 for(unsigned n=0;n<5;n++){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
  struct ieee80211_key_conf key=new_key(0,true);
  assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&key));
  fault=n==0?RETIRE:n==1?FLUSH:n==4?CAM:DCTL;failures=1;apply_failed_dctl=n==3;
  assert(rtl_ax_set_key(&hw,DISABLE_KEY,0,&sta,&key)==-ETIMEDOUT);
  assert(r.key_result==-ETIMEDOUT&&!r.security_roles[7].valid&&r.security_ids[0]&&r.security_deleting[0]&&queued==1);
  unsigned cleared=clears;failures=1;assert(rtl_ax_security_cleanup(&r));
  assert(r.security_ids[0]&&r.security_deleting[0]&&hardware_cam[0]);
  if(fault!=CAM)assert(clears==cleared);
  fault=NONE;assert(!rtl_ax_security_cleanup(&r));
  assert(!r.security_ids[0]&&!r.security_deleting[0]&&!r.security_unlink[0]&&!hardware_cam[0]&&!hardware_roles[7].valid);
 }
 /* New keys in the same MACID survive an older failed unlink. */
 for(unsigned id=0;id<=127;id+=127){
  reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{id}}};
  struct ieee80211_sta *peer=id?&sta:0;
  struct ieee80211_key_conf old=new_key(0,id!=0),next=new_key(1,id!=0);
  assert(!rtl_ax_set_key(&hw,SET_KEY,0,peer,&old));fault=DCTL;failures=1;
  assert(rtl_ax_set_key(&hw,DISABLE_KEY,0,peer,&old)==-ETIMEDOUT);
  assert(r.security_unlink[0]==id+1);fault=NONE;
  assert(!rtl_ax_set_key(&hw,SET_KEY,0,peer,&next)&&next.hw_key_idx==1);
  assert(!rtl_ax_security_cleanup(&r));unsigned slot=id?1:3;
  assert(hardware_roles[id].valid==BIT(slot)&&hardware_roles[id].cam[slot]==1&&hardware_cam[1]);
  assert(offload_ready==(id?1U:0U));
 }
 /* Station release resolves pending unlinks before MACID reuse. */
 reset();struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_key_conf old=new_key(0,true),next=new_key(0,true);
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&old));fault=DCTL;failures=1;
 assert(rtl_ax_set_key(&hw,DISABLE_KEY,0,&sta,&old)==-ETIMEDOUT);fault=NONE;
 assert(!rtl_ax_station_release(&r,7)&&!r.security_unlink[0]&&!hardware_roles[7].valid);
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&next)&&next.hw_key_idx==1);
 unsigned before=dctls;assert(!rtl_ax_security_cleanup(&r)&&dctls==before);
 assert(hardware_roles[7].valid==1&&hardware_roles[7].cam[0]==1&&hardware_cam[1]);
 assert(!rtl_ax_set_key(&hw,DISABLE_KEY,0,&sta,&next)&&!r.security_ids[1]);
 puts("key cleanup: TX retirement/drain, DMAC ACK both outcomes, CAM failure, newer PTK/GTK, MACID127 and station reuse passed");
}
'''
with tempfile.TemporaryDirectory(prefix='radio-key-cleanup-') as directory:
    path = Path(directory)
    source = (args.monitor or driver / 'monitor.h').read_text()
    functions = ''
    for prefix, name in (('static int ', 'rtl_ax_station_release'),
                         ('static bool ', 'rtl_ax_security_cleanup'), ('static int ', 'rtl_ax_set_key')):
        start = source.index(prefix + name + '(')
        end = source.index('\n}\n', start) + 3
        functions += source[start:end] + '\n'
    before, after = common.split('enum set_key_cmd', 1)
    c, binary = path / 'key.c', path / 'key'
    c.write_text(before + '#include "' + str(driver / 'security.h') + '"\n' +
                 'enum set_key_cmd' + after + functions + install_tests + (reproduce_install if args.reproduce_install else reproduce if args.reproduce else test))
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
