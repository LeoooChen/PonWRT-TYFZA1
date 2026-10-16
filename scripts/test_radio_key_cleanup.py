#!/usr/bin/env python3
"""Execute native key deletion with TX, DMAC and CAM completion faults."""
from pathlib import Path
import argparse
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--monitor', type=Path)
parser.add_argument('--reproduce', action='store_true')
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
 bool security_ids[128],security_deleting[128];u8 security_unlink[128];
 int tx,rx,cleanup_work;unsigned keys_installed,keys_removed;int key_result;
 struct {bool valid;unsigned macid;} rx_ba[8];
 struct {u8 self[6],peer[6];unsigned aid;bool pending;} retired_stations[128];
 bool ra_registered[128],ra_pending[128],station_ids[128];unsigned station_removed;
};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
enum fault {NONE,RETIRE,FLUSH,DCTL,CAM};
static enum fault fault;
static unsigned failures,queued,clears,dctls,offload_ready;
static bool apply_failed_dctl,hardware_cam[128];
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
  if(!ret||apply_failed_dctl){hardware_roles[id].valid=data[24];memcpy(hardware_roles[id].cam,data+25,3);memcpy(hardware_roles[id].cam+3,data+28,4);}
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
 } else hardware_cam[index]=true;
 return 0;
}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,
 const u8 *data,unsigned len,bool done){return rtl_ax_command_flags(r,q,cat,cls,fn,data,len,false,done);}
static int rtl_ax_drop_station(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;return 0;}
static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *r,unsigned i){r->rx_ba[i].valid=false;return 0;}
static void rtl_ax_peer_cam(u8 *out,unsigned id,const u8 *self,const u8 *peer,unsigned aid,bool valid){
 (void)out;(void)id;(void)self;(void)peer;(void)aid;(void)valid;}
static void rtl_ax_tx_peer_qos(int tx,unsigned id,bool qos){(void)tx;(void)id;(void)qos;}
static void reset(void){fault=NONE;failures=queued=clears=dctls=offload_ready=0;apply_failed_dctl=false;
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
test = r'''
int main(void){
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
                 'enum set_key_cmd' + after + functions + (reproduce if args.reproduce else test))
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
