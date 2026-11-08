#!/usr/bin/env python3
"""Execute native AP/mesh interface, beacon and firmware-role transitions."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap"

def function(file, name):
    text = (driver / file).read_text()
    start = re.search(r"^static[^;]*?\b" + name + r"\([^;]*?\)\s*\{", text, re.M).start()
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"

code = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef int64_t s64; typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define RTL_AX_BIT(n) BIT(n)
#define RTL_AX_H2C_CMD 0
#define RTL_AX_H2C_DATA 1
#define GFP_KERNEL 0
#define RTL_AX_8852C 3
#define NL80211_IFTYPE_MONITOR 6
#define NL80211_IFTYPE_STATION 2
#define RTL_AX_CLIENT_MACID 1
#define __ffs(x) __builtin_ctz(x)
#define READ_ONCE(x) (x)
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_MESH_POINT 7
#define IEEE80211_NUM_ACS 4
#define BSS_CHANGED_ERP_SLOT BIT(0)
#define BSS_CHANGED_HE_BSS_COLOR BIT(1)
#define BSS_CHANGED_BEACON BIT(2)
#define BSS_CHANGED_BEACON_ENABLED BIT(3)
#define BSS_CHANGED_IDLE BIT(4)
#define BSS_CHANGED_BEACON_INT BIT(5)
#define WRITE_ONCE(x,v) ((x)=(v))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define mutex_lock(l) ((void)(l))
#define mutex_unlock(l) ((void)(l))
#define usecs_to_jiffies(x) (x)
#define wiphy_delayed_work_queue(w,p,d) ((void)(w),(void)(d),(p)->queued=true)
#define wiphy_delayed_work_cancel(w,p) ((void)(w),(p)->queued=false)
#define wiphy_work_cancel(w,p) ((void)(w),(void)(p),cancellations++)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
struct ieee80211_bss_conf {unsigned beacon_int,dtim_period; bool enable_beacon;
 struct {bool enabled; unsigned color;} he_bss_color;};
struct ieee80211_vif {int type;bool use_4addr;struct {bool idle;}cfg; u8 addr[6]; struct ieee80211_bss_conf bss_conf;};
struct port {int result;};
struct io {int unused;}; struct tx {bool blocked;};
struct rtl_ax_ap {
 struct {u8 nhm_noise_ratio;} environment;s64 mesh_tsf_remainder;struct {bool queued;}mesh_sync_work;struct ieee80211_vif *ap_vif,*sta_vif;bool sta_role,sta_4addr,sta_associated;u8 sta_bssid[6];struct {bool pending;}retired_stations[128]; struct io io; struct tx *tx; void *pdev,*rx;
 bool monitor_started,ap_started,mesh_started,beacon_enabled,radar_required;
 struct {int error;bool enabled,detected,in_cac;}dfs;
 struct {unsigned valid;}security_roles[1];
 unsigned chip,role_stage,role_type,edca_valid;struct {unsigned bandwidth;}channel;struct {unsigned band;}band;
 int role_result,ap_result,command_mutex,command_lock,offload_work,beacon_work,ra_update_work;
 struct port port0;u8 role_cam[60];unsigned receive_acks,done_acks,unmatched_acks;
 struct sk_buff *scan_pending;struct ieee80211_hw *hw;u64 scan_received_ns;
 unsigned scan_word0,scan_word1,scan_last_rpl,scan_matched,scan_unmatched;u8 scan_last_bssid[6];
};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
static unsigned cancellations,stop_requests;
static unsigned bcn_enable,rx_mode,templates,fail_template,fail_command,commands;
static unsigned command_class[32],command_function[32],command_word[32];
static void rtl_ax_store32(u8 *p,u32 value){memcpy(p,&value,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
static void pci_set_master(void *p){(void)p;}
static void pci_clear_master(void *p){(void)p;}
static void rtl_ax_h2c_start(struct io *io){(void)io;}
static void rtl_ax_hci_pause(struct io *io){(void)io;}
static void rtl_ax_rx_open(void *rx){(void)rx;}
static void rtl_ax_rx_close(void *rx){(void)rx;}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned a,unsigned c,unsigned f,const u8 *p,unsigned len,bool ack){
 (void)r;(void)q;(void)a;(void)len;(void)ack; assert(commands<32);
 command_class[commands]=c;command_function[commands]=f;command_word[commands++]=rtl_ax_ap_read32(p);
 return fail_command && commands==fail_command ? -EIO : 0;
}
static int rtl_ax_command_flags(struct rtl_ax_ap *r,unsigned q,unsigned a,unsigned c,unsigned f,const u8 *p,unsigned len,bool ack,bool done){
 (void)done;return rtl_ax_command(r,q,a,c,f,p,len,ack);
}
static int rtl_ax_station_tables(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;return 0;}
static int rtl_ax_port0_drop_mbid(void *p,unsigned id){(void)p;(void)id;return 0;}
static int rtl_ax_port0_start(struct io *io,struct port *port,void *r,int(*drop)(void*,unsigned),unsigned interval,unsigned dtim){
 (void)io;(void)port;(void)r;(void)drop;assert(interval==100&&dtim==2);return 0;
}
static struct ieee80211_vif *ieee80211_vif_to_wdev(struct ieee80211_vif *vif){return vif;}
static void cfg80211_stop_iface(void *wiphy,void *wdev,int gfp){(void)wiphy;(void)wdev;(void)gfp;stop_requests++;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static void rtl_ax_tx_ap_address(struct tx *tx,u8 *addr){(void)tx;(void)addr;}
static void rtl_ax_update32(struct io *io,unsigned reg,unsigned mask,unsigned value){(void)io;assert(reg==0xc400&&mask==BIT(12));bcn_enable=value;}
static void rtl_ax_security_dctl(u8 *p,unsigned id,void *role){(void)id;(void)role;memset(p,0,68);}
static int rtl_ax_ap_template(struct ieee80211_hw *hw,struct ieee80211_vif *vif,struct ieee80211_bss_conf *conf){
 (void)hw;(void)vif;(void)conf;templates++;return fail_template ? -ENOMEM : 0;
}
static void rtl_ax_port0_color(struct io *io,unsigned color){(void)io;(void)color;}
static void rtl_ax_receive_mode(struct io *io,unsigned mode){(void)io;rx_mode=mode;}
static void rtl_ax_tx_dfs_block(struct tx *tx,bool block){tx->blocked=block;}
static void rtl_ax_edca_apply(struct rtl_ax_ap *r,struct ieee80211_vif *v,unsigned ac){(void)r;(void)v;(void)ac;}
"""
code += r"""
#define RX_FLAG_NO_SIGNAL_VAL BIT(0)
#define RX_FLAG_ONLY_MONITOR BIT(1)
struct ieee80211_rx_status {unsigned flag;int signal;};
struct sk_buff {u8 data[32];struct ieee80211_rx_status status;};
struct rtl_ax_rx_packet {bool crc_error;u8 descriptor[8],payload[4];unsigned bytes;};
#define IEEE80211_SKB_RXCB(skb) (&(skb)->status)
static u64 ktime_get_ns(void){return 100;}
static bool rtl_ax_scan_ppdu_match(unsigned a,unsigned b,unsigned c,unsigned d){(void)a;(void)b;(void)c;(void)d;return true;}
static int rtl_ax_scan_rpl(const u8 *p,unsigned bytes){(void)p;(void)bytes;return 100;}
static unsigned delivered_flags;
static void ieee80211_rx_ni(struct ieee80211_hw *hw,struct sk_buff *skb){(void)hw;delivered_flags=skb->status.flag;}
"""
code += r"""
struct net_device {};struct ieee80211_sta;
enum tc_setup_type { TC_SETUP_BLOCK };
struct net_device_path_ctx {struct net_device *dev;};
struct net_device_path {unsigned type;struct net_device *dev;};
#define DEV_PATH_ETHERNET 1
static unsigned tc_calls;
static int rtl9617c_wifi_setup_tc(struct net_device *dev,enum tc_setup_type type,void *data){(void)dev;(void)type;(void)data;tc_calls++;return 0;}
"""

code += r"""
#define BIT_ULL(n) (1ULL<<(n))
#define min(a,b) ((a)<(b)?(a):(b))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define le16_to_cpu(v) (v)
#define NL80211_BAND_5GHZ 1
#define IEEE80211_VHT_CAP_SHORT_GI_80 BIT(5)
#define IEEE80211_VHT_CAP_SHORT_GI_160 BIT(6)
#define IEEE80211_HT_CAP_SGI_20 BIT(5)
#define IEEE80211_HT_CAP_SGI_40 BIT(6)
#define IEEE80211_SMPS_STATIC 2
#define IEEE80211_SMPS_DYNAMIC 3
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u64 get_unaligned_le64(const void *p){u64 v;memcpy(&v,p,8);return v;}
static void put_unaligned_le64(u64 v,void *p){memcpy(p,&v,8);}
#include "rate.h"
struct ieee80211_sta_he_cap {bool has_he;struct {u16 rx_mcs_80,rx_mcs_160;}he_mcs_nss_supp;struct {u8 phy_cap_info[11];}he_cap_elem;};
struct ieee80211_link_sta {unsigned bandwidth,rx_nss,smps_mode,supp_rates[2];
 struct {struct {u8 rx_mask[2];}mcs;u32 cap;bool ht_supported;}ht_cap;
 struct {u32 cap;bool vht_supported;struct {u16 rx_mcs_map;}vht_mcs;}vht_cap;
 struct ieee80211_sta_he_cap he_cap;};
struct ieee80211_sta {struct ieee80211_link_sta deflink;struct {u32 max_amsdu_len;}*cur;};
"""
code += r"""
static int rtl_ax_client_prepare(struct rtl_ax_ap*r,struct ieee80211_vif*v){(void)v;r->sta_role=true;return 0;}
static void rtl_ax_client_release(struct rtl_ax_ap*r){r->sta_role=false;}
static int rtl_ax_station_release(struct rtl_ax_ap*r,unsigned id){assert(id==1);r->sta_role=false;return 0;}
static void rtl_ax_client_cfg_changed(struct ieee80211_hw*h,struct ieee80211_vif*v,u64 c){(void)h;(void)v;(void)c;assert(0);}
"""
code += function("monitor.h", "rtl_ax_ra_peer")
code += function("monitor.h", "rtl_ax_net_setup_tc")
code += function("monitor.h", "rtl_ax_net_fill_forward_path")
code += function("monitor.h", "rtl_ax_scan_complete_frame")

for name in ("rtl_ax_role_unpause", "rtl_ax_role_create", "rtl_ax_role_join", "rtl_ax_role_cam"):
    code += function("role.h", name)
code += function("pci.c", "rtl_ax_self_role_prepare")
for name in ("rtl_ax_monitor_add_interface", "rtl_ax_ap_start", "rtl_ax_ap_stop", "rtl_ax_monitor_remove_interface", "rtl_ax_ap_changed"):
    code += function("monitor.h", name)
code += r"""
static void reset_commands(void){commands=fail_command=0;}
static void check_join(unsigned word){assert(commands==2);assert(command_class[0]==8&&command_function[0]==0&&command_word[0]==word);assert(command_class[1]==6&&command_function[1]==0);}
int main(void){
 for(unsigned chip=3;chip<=4;chip++){
  struct tx tx={};struct rtl_ax_ap r={.tx=&tx,.chip=chip,.monitor_started=true};
  struct ieee80211_hw hw={&r};struct ieee80211_vif ap={.type=NL80211_IFTYPE_AP},mesh={.type=NL80211_IFTYPE_MESH_POINT},monitor={.type=NL80211_IFTYPE_MONITOR},sta={.type=2};
  struct ieee80211_bss_conf conf={.beacon_int=100,.dtim_period=2,.enable_beacon=true};
  unsigned bw=(chip==3?3:1)<<14;
  reset_commands();assert(!rtl_ax_self_role_prepare(&r,ap.addr,100,2));
  assert(r.role_type==2&&r.role_stage==5&&commands==4);
  assert(command_word[1]==0x4100&&command_word[2]==(0x4b040100|bw));
  assert(!rtl_ax_monitor_add_interface(&hw,&sta)&&r.sta_role&&r.sta_vif==&sta);
  assert(rtl_ax_monitor_add_interface(&hw,&mesh)==-EBUSY);
  rtl_ax_monitor_remove_interface(&hw,&sta);assert(!r.sta_vif&&!r.sta_role);
  assert(!rtl_ax_monitor_add_interface(&hw,&monitor)&&!r.ap_vif);
  assert(!rtl_ax_monitor_add_interface(&hw,&mesh));
  struct ieee80211_sta peer={.deflink={.rx_nss=1,.supp_rates={0xfff,0xff}}};
  typeof(*peer.cur) cur={.max_amsdu_len=0};peer.cur=&cur;
  r.band.band=chip==3;u8 ra[16];u32 rate,caps;
  assert(rtl_ax_ra_peer(&r,&peer,1,ra,&rate,&caps,false));
  assert(ra[0]==(chip==3?4:6)&&rate==(chip==3?4:0));
  assert(get_unaligned_le64(ra+4)==(BIT_ULL(63)|(chip==3?0xff0:0xff5)));
  r.ap_vif=&ap;assert(rtl_ax_ra_peer(&r,&peer,1,ra,&rate,&caps,false));r.ap_vif=&mesh;
  assert(rtl_ax_monitor_add_interface(&hw,&ap)==-EBUSY);
  struct net_device dev;struct net_device_path_ctx ctx={&dev};struct net_device_path path={};
  assert(rtl_ax_net_setup_tc(&hw,&mesh,&dev,TC_SETUP_BLOCK,NULL)==-EOPNOTSUPP);
  assert(rtl_ax_net_fill_forward_path(&hw,&mesh,NULL,&ctx,&path)==-EOPNOTSUPP&&ctx.dev==&dev);
  assert(!rtl_ax_net_setup_tc(&hw,&ap,&dev,TC_SETUP_BLOCK,NULL));
  assert(!rtl_ax_net_fill_forward_path(&hw,&ap,NULL,&ctx,&path)&&!ctx.dev&&path.dev==&dev);
  assert(!rtl_ax_net_setup_tc(&hw,&sta,&dev,TC_SETUP_BLOCK,NULL));
  ctx.dev=&dev;assert(!rtl_ax_net_fill_forward_path(&hw,&sta,NULL,&ctx,&path)&&!ctx.dev);
  reset_commands();templates=0;
  rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON|BSS_CHANGED_BEACON_ENABLED);
  assert(r.mesh_sync_work.queued);
  assert(r.ap_started&&r.mesh_started&&r.beacon_enabled&&templates==1&&bcn_enable==BIT(12)&&rx_mode==4);
  assert(r.role_type==6);check_join(0x5b040100|bw);
  /* Scan/ROC toggles only beacon enable. The channel remains assigned. */
  reset_commands();cancellations=0;unsigned saved_templates=templates;
  r.dfs.enabled=true;r.dfs.in_cac=false;r.radar_required=true;
  conf.enable_beacon=false;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  assert(!r.mesh_sync_work.queued);
  assert(r.ap_started&&!r.beacon_enabled&&!r.dfs.in_cac&&!tx.blocked&&!cancellations);
  conf.enable_beacon=true;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  assert(r.mesh_sync_work.queued);
  assert(r.ap_started&&r.beacon_enabled&&!commands&&templates==saved_templates);
  r.dfs.enabled=false;r.radar_required=false;
  /* A retained monitor channel can suppress vif IDLE during mesh leave. */
  conf.enable_beacon=false;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  reset_commands();conf.enable_beacon=true;
  rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED|BSS_CHANGED_BEACON_INT|BSS_CHANGED_BEACON);
  assert(commands==1&&command_class[0]==6&&templates==saved_templates+1);templates--;
  reset_commands();rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON);assert(templates==2&&!commands);
  conf.enable_beacon=false;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  mesh.cfg.idle=true;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_IDLE);
  assert(!r.ap_started&&!r.beacon_enabled&&!bcn_enable&&rx_mode==0);
  mesh.cfg.idle=false;
  conf.enable_beacon=true;reset_commands();rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  assert(r.ap_started&&commands==1&&command_class[0]==6);
  conf.enable_beacon=false;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  mesh.cfg.idle=true;rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_IDLE);
  mesh.cfg.idle=false;rtl_ax_monitor_remove_interface(&hw,&mesh);assert(!r.ap_vif);assert(!rtl_ax_monitor_add_interface(&hw,&ap));
  reset_commands();assert(!rtl_ax_ap_start(&hw,&ap,&conf));check_join(0x4b040100|bw);assert(r.role_type==2);
  rtl_ax_ap_stop(&hw,&ap,&conf);rtl_ax_monitor_remove_interface(&hw,&ap);assert(!rtl_ax_monitor_add_interface(&hw,&mesh));
  conf.enable_beacon=true;reset_commands();fail_command=1;
  rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  assert(r.ap_result==-EIO&&!r.ap_started&&!bcn_enable&&stop_requests==1);
  reset_commands();rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);assert(r.ap_started);check_join(0x5b040100|bw);
  struct sk_buff skb={.status={.flag=RX_FLAG_NO_SIGNAL_VAL}};
  r.hw=&hw;r.scan_pending=&skb;rtl_ax_scan_complete_frame(&r,NULL);
  assert(delivered_flags==RX_FLAG_NO_SIGNAL_VAL&&!r.scan_pending);
  r.ap_vif=&ap;r.mesh_started=false;r.scan_pending=&skb;rtl_ax_scan_complete_frame(&r,NULL);
  assert(delivered_flags==(RX_FLAG_NO_SIGNAL_VAL|RX_FLAG_ONLY_MONITOR));
  r.ap_vif=&mesh;
  rtl_ax_ap_stop(&hw,&mesh,&conf);reset_commands();fail_template=1;
  rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);assert(r.ap_result==-ENOMEM&&!r.ap_started&&!bcn_enable&&stop_requests==2);
  fail_template=0;
  /* Removal also retires an active mesh when a monitor retains a channel. */
  reset_commands();rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);
  assert(r.ap_started);rtl_ax_monitor_remove_interface(&hw,&mesh);
  assert(!r.ap_vif&&!r.ap_started&&!r.mesh_started&&!r.beacon_enabled&&!r.mesh_sync_work.queued);
  assert(!rtl_ax_monitor_add_interface(&hw,&mesh));
  r.radar_required=true;r.dfs.error=-EIO;reset_commands();
  rtl_ax_ap_changed(&hw,&mesh,&conf,BSS_CHANGED_BEACON_ENABLED);assert(r.ap_result==-EIO&&!r.ap_started&&!commands&&stop_requests==3);stop_requests=0;
 }
 puts("AP/mesh: interface/offload exclusion, C/XB role creation/switch/retry, beacon start/update/stop/rejoin, scan/ROC pause without teardown, NOHT mesh peers, mesh beacon delivery without PHY report, firmware/template/DFS errors PASS");
}
"""
with tempfile.TemporaryDirectory(prefix="radio-mesh-") as directory:
    work = Path(directory)
    (work / "test.c").write_text(code)
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers", "-Wno-unused-parameter", "-fsanitize=address,undefined", "-I" + str(driver), str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
