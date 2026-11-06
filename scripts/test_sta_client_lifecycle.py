#!/usr/bin/env python3
"""Run production STA role setup with each firmware boundary failing in turn."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
role = (driver / 'role.h').read_text()
monitor = (driver / 'monitor.h').read_text()
# ieee80211_alloc_hw_nm rejects mixed legacy and split BSS callbacks.
ops = monitor.split('static const struct ieee80211_ops rtl_ax_monitor_ops = {', 1)[1].split('};', 1)[0]
callbacks = set(re.findall(r'\.(\w+)\s*=', ops))
assert ('link_info_changed' in callbacks) == ('vif_cfg_changed' in callbacks)
assert not {'link_info_changed', 'bss_info_changed'} <= callbacks
assert re.search(r'\.bss_info_changed\s*=\s*rtl_ax_ap_changed', ops)

def function(source, name):
    match = re.search(r'^static .*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    start, end, depth = match.start(), match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define ETH_ALEN 6
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT(n) BIT(n)
#define RTL_AX_H2C_CMD 0
#define RTL_AX_H2C_DATA 1
#define HZ 100
#define NL80211_IFTYPE_STATION 2
#define BSS_CHANGED_ASSOC 1
#define BSS_CHANGED_BSSID 2
#define BSS_CHANGED_BEACON_INT 4
#define BSS_CHANGED_BEACON_INFO 8
#define BSS_CHANGED_HE_BSS_COLOR 16
#define BSS_CHANGED_ERP_SLOT 32
#define BSS_CHANGED_BEACON 64
#define BSS_CHANGED_BEACON_ENABLED 128
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_MESH_POINT 7
#define BSS_CHANGED_IDLE 256
#define GFP_KERNEL 0
#define usecs_to_jiffies(x) (x)
#define wiphy_delayed_work_cancel(w,p) ((void)(w),(void)(p))
#define IEEE80211_NUM_ACS 4
#define dev_err(...) ((void)0)
#define spin_lock_irqsave(lock,flags) ((void)(lock),(void)(flags))
#define spin_unlock_irqrestore(lock,flags) ((void)(lock),(void)(flags))
#define __set_bit(n,p) ((p)[n]=true)
#define ether_addr_copy(a,b) memcpy(a,b,6)
#define eth_zero_addr(a) memset(a,0,6)
static void rtl_ax_store32(u8 *p,u32 x){memcpy(p,&x,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 x;memcpy(&x,p,4);return x;}
static bool bitmap_empty(const bool *p,unsigned n){for(unsigned i=0;i<n;i++)if(p[i])return false;return true;}
struct ieee80211_bss_conf {unsigned beacon_int,dtim_period;u8 bssid[6];
 struct {bool enabled;unsigned color;}he_bss_color;bool enable_beacon;};
struct ieee80211_vif {int type;u8 addr[6];struct {unsigned aid;bool assoc,idle;} cfg;
 struct ieee80211_bss_conf bss_conf;};
struct rtl_ax_ap;
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
struct rtl_ax_io {void(*write8)(void *,unsigned,u8);};
struct rtl_ax_ap {struct ieee80211_hw *hw;struct rtl_ax_io io;int command_mutex,cleanup_work,tx;
 bool sta_role,sta_associated,scanning,station_ids[128],peer_authorized[128];
 bool ap_started,beacon_enabled;unsigned edca_valid;int ap_result,command_lock;
 struct {bool in_cac,detected;int error;}dfs;
 struct {bool pending,client;u8 self[6],peer[6];} retired_stations[128];
 u8 sta_bssid[6],role_cam[60];unsigned role_stage,role_type;int mesh_sync_work;u32 sta_join;int role_result,offload_result;};
static unsigned calls,fail_at,queued,losses,regs[65536],writes,ap_deletes;
static bool admission;
static u32 last_join,last_cam,last_remove;
static int boundary(void){return ++calls==fail_at?-ETIMEDOUT:0;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void rtl_ax_port0_field(struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){
 (void)io;assert(!(value&~mask));regs[reg]=(regs[reg]&~mask)|value;writes++;}
static void rtl_ax_update16(struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){rtl_ax_port0_field(io,reg,mask,value);}
static void rtl_ax_port0_color(struct rtl_ax_io *io,unsigned color){rtl_ax_port0_field(io,0xc6a0,63,color);}
static void rtl_ax_update8(struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){(void)io;regs[reg]=(regs[reg]&~mask)|value;writes++;}
static void write8(void *ctx,unsigned reg,u8 value){(void)ctx;regs[reg]=value;writes++;}
static void rtl_ax_receive_mode(struct rtl_ax_io *io,unsigned mode){(void)io;assert(mode==0||mode==2);}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *data,unsigned bytes,bool ack){
 (void)r;(void)q;assert(cat==1&&ack);
 if(cls==8){assert(bytes==4);u32 v=rtl_ax_ap_read32(data);
  assert(fn==4?(v==0x4500||v==0xc500||v==0x2001):(fn==0&&(v==r->sta_join||v==0x04040101)));}
 else {assert(cls==6&&fn==0&&bytes==60);last_cam=rtl_ax_ap_read32(data+32);}
 if(cls==8&&fn==4&&(rtl_ax_ap_read32(data)&0x500)==0x500)last_remove=rtl_ax_ap_read32(data);
 if(cls==8&&fn==0)last_join=rtl_ax_ap_read32(data);
 if(cls==8&&fn==4&&rtl_ax_ap_read32(data)==0x4500)ap_deletes++;
 return boundary();}
static int rtl_ax_command_flags(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *data,unsigned bytes,bool receive,bool done){
 (void)r;assert(q==0&&cat==1&&cls==9&&fn==8&&bytes==32&&receive&&!done);assert(rtl_ax_ap_read32(data+16)==2);return boundary();}
static int rtl_ax_station_tables(struct rtl_ax_ap *r,unsigned id){(void)r;assert(id==1);return boundary();}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static void wiphy_delayed_work_queue(void *w,int *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);queued++;}
static void rtl_ax_tx_peer_authorized(int tx,unsigned id,bool enabled){(void)tx;assert(id==1);admission=enabled;}
static int rtl_ax_tx_ni_retire_peer(int tx,unsigned id){(void)tx;assert(id==1&&!admission);return 0;}
static void ieee80211_connection_loss(struct ieee80211_vif *v){assert(v->type==2);losses++;}
'''
common += r'''
static int rtl_ax_ap_start(struct ieee80211_hw*h,struct ieee80211_vif*v,struct ieee80211_bss_conf*c){(void)h;(void)v;(void)c;assert(0);return 0;}
static void rtl_ax_ap_stop(struct ieee80211_hw*h,struct ieee80211_vif*v,struct ieee80211_bss_conf*c){(void)h;(void)v;(void)c;assert(0);}
static void *ieee80211_vif_to_wdev(struct ieee80211_vif*v){return v;}
static void cfg80211_stop_iface(void*w,void*v,int g){(void)w;(void)v;(void)g;assert(0);}
static void rtl_ax_edca_apply(struct rtl_ax_ap *r,struct ieee80211_vif *v,unsigned ac){(void)r;(void)v;(void)ac;assert(0);}
static int rtl_ax_ap_template(struct ieee80211_hw *h,struct ieee80211_vif *v,struct ieee80211_bss_conf *c){(void)h;(void)v;(void)c;assert(0);return 0;}
static void rtl_ax_update32(struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){rtl_ax_port0_field(io,reg,mask,value);}
'''
test = r'''
int main(void){
 struct ieee80211_vif vif={.type=2,.addr={2,0,0,0,0,1},.cfg={.aid=42},.bss_conf={.beacon_int=100,.dtim_period=2}};
 struct rtl_ax_ap held={.station_ids={[7]=true}};
 assert(rtl_ax_client_prepare(&held,&vif)==-EBUSY&&!calls);
 for(unsigned fault=0;fault<=8;fault++){
  struct rtl_ax_ap r={.role_stage=5,.role_type=2,.io={write8}};struct ieee80211_hw hw={.radio=&r};r.hw=&hw;
  calls=queued=losses=writes=ap_deletes=0;fail_at=fault;memset(regs,0xa5,sizeof(regs));
  int result=rtl_ax_client_prepare(&r,&vif);
  assert(result==(fault?-ETIMEDOUT:0));
  if(fault>=4){assert(r.sta_role&&r.station_ids[1]&&r.retired_stations[1].pending&&queued);
   unsigned old=calls;assert(rtl_ax_client_prepare(&r,&vif)==-EBUSY&&calls==old);}
  else if(fault){
   assert(!r.sta_role&&!r.station_ids[1]);
   if(fault==2){
    assert(!r.role_stage&&ap_deletes==1);fail_at=0;
    assert(!rtl_ax_client_prepare(&r,&vif)&&ap_deletes==1);
   }
  }
  else {
   assert(r.sta_role&&!r.retired_stations[1].pending&&!r.role_stage);
   assert((regs[0xc400]&0x13c1f)==4);
   memcpy(r.sta_bssid,"\2\0\0\0\0\2",6);vif.cfg.assoc=true;fail_at=0;
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_ASSOC);
   assert(r.sta_associated && (regs[0xc400]&0x13c1f)==0x1281c);
   assert(!(regs[0xc400]&BIT(12)) && regs[0xc427]==2 && (regs[0xc404]&0xfff0000)==400U<<16);
   r.sta_join=0x0606f001;
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_ASSOC);
   assert(last_join==r.sta_join && last_cam==(1|BIT(14)));
   assert(!rtl_ax_client_update(&r,&vif,true));
   assert(last_join==0x0606f001 && last_cam==(1|BIT(14)));
   memcpy(vif.bss_conf.bssid,"\2\0\0\0\0\7",6);
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_BSSID);
   assert(!memcmp(r.sta_bssid,vif.bss_conf.bssid,6));
   assert(!memcmp(r.retired_stations[1].peer,vif.bss_conf.bssid,6));
   vif.bss_conf.dtim_period=3;
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_BEACON_INFO);
   assert(regs[0xc427]==3);
   for(unsigned change=BSS_CHANGED_BSSID;change<=BSS_CHANGED_BEACON_INFO;change<<=1){
    r.peer_authorized[1]=admission=true;fail_at=calls+1;unsigned before=losses;
    rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,change);
    assert(losses==before+1&&!r.sta_associated&&!r.peer_authorized[1]&&!admission);
    fail_at=0;rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,change);assert(r.sta_associated);
   }
   losses=0;
   r.peer_authorized[1]=admission=true;fail_at=calls+1;
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_ASSOC);
   assert(losses==1&&!r.sta_associated&&!r.peer_authorized[1]&&!admission);
   vif.cfg.assoc=false;fail_at=0;
   rtl_ax_ap_changed(&hw,&vif,&vif.bss_conf,BSS_CHANGED_ASSOC);assert(!r.sta_associated);
  }
 }
 for(unsigned role_type=2;role_type<=6;role_type+=4){
  struct rtl_ax_ap r={.role_stage=5,.role_type=role_type,.io={write8}};struct ieee80211_hw hw={.radio=&r};r.hw=&hw;fail_at=0;
  assert(!rtl_ax_client_prepare(&r,&vif));assert(last_remove==(0x500|(role_type<<13)));
 }
 puts("STA lifecycle: callback registration, BSSID/DTIM failure withdrawal, stock hold400, eight setup faults, AP removal/CAM failure retry, MACID quarantine and beacon-TX disabled passed");
}
'''
with tempfile.TemporaryDirectory(prefix='sta-client-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + '\n'.join(function(role, name) for name in
        ('rtl_ax_role_unpause', 'rtl_ax_client_cam')) + '\n' +
        (driver / 'station.h').read_text() + function(monitor, 'rtl_ax_ap_changed') + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
