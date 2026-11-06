#!/usr/bin/env python3
"""Exercise firmware rate decoding and the kernel's HWMP airtime caller."""
from pathlib import Path
import re
import subprocess
import tempfile
import sys

root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
kernel=Path(sys.argv[1]) if len(sys.argv)>1 else root/'artifacts/mesh/kernel'

def function(path,name):
    s=path.read_text();start=re.search(r'^(?:static |u32 )[^;]*?\b'+name+r'\([^;]*?\)\s*\{',s,re.M).start()
    end=s.index('{',start)+1;depth=1
    while depth:
        depth+=(s[end]=='{')-(s[end]=='}');end+=1
    return s[start:end]+'\n'

code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define __ffs(x) __builtin_ctz(x)
#define WARN_ON_ONCE(x) (x)
#define WARN_ON(x) (x)
#define WARN_ONCE(...) ((void)0)
#define WARN(...) ((void)0)
#define do_div(x,d) ((x)/=(d))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define BUILD_BUG_ON(x) ((void)0)
#define BUILD_BUG_ON_NOT_POWER_OF_2(x) ((void)0)
#define ilog2(x) __builtin_ctz(x)
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define max(a,b) ((a)>(b)?(a):(b))
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
enum {RATE_INFO_BW_20,RATE_INFO_BW_5,RATE_INFO_BW_10,RATE_INFO_BW_40,RATE_INFO_BW_80,RATE_INFO_BW_160,RATE_INFO_BW_HE_RU};
enum {RATE_INFO_FLAGS_MCS=1,RATE_INFO_FLAGS_VHT_MCS=2,RATE_INFO_FLAGS_SHORT_GI=4,RATE_INFO_FLAGS_HE_MCS=16};
enum {NL80211_RATE_INFO_HE_GI_0_8,NL80211_RATE_INFO_HE_GI_1_6,NL80211_RATE_INFO_HE_GI_3_2};
enum {NL80211_RATE_INFO_HE_RU_ALLOC_26,NL80211_RATE_INFO_HE_RU_ALLOC_52,NL80211_RATE_INFO_HE_RU_ALLOC_106,NL80211_RATE_INFO_HE_RU_ALLOC_242,NL80211_RATE_INFO_HE_RU_ALLOC_484,NL80211_RATE_INFO_HE_RU_ALLOC_996,NL80211_RATE_INFO_HE_RU_ALLOC_2x996};
enum {NL80211_STA_INFO_SIGNAL=7,NL80211_STA_INFO_TX_BITRATE=8};
struct rate_info {unsigned flags,mcs,nss,bw,he_gi,he_dcm,he_ru_alloc,legacy;};
'''
avg=(kernel/'include/linux/average.h').read_text();code+=avg[avg.index('#define DECLARE_EWMA'):avg.rindex('#endif')]
for mode in ('ht','vht','he'):
    code+=function(kernel/'net/wireless/util.c','cfg80211_calculate_bitrate_'+mode)
code+=r'''
static u32 cfg80211_calculate_bitrate(struct rate_info*r){
 if(r->flags & RATE_INFO_FLAGS_MCS)return cfg80211_calculate_bitrate_ht(r);
 if(r->flags & RATE_INFO_FLAGS_VHT_MCS)return cfg80211_calculate_bitrate_vht(r);
 if(r->flags & RATE_INFO_FLAGS_HE_MCS)return cfg80211_calculate_bitrate_he(r);
 return r->legacy;
}
#include "rate-report.h"
DECLARE_EWMA(rtl_ax_success,8,8);
struct rtl_ax_tx {struct ewma_rtl_ax_success peer_success[128];};
struct bitrate {unsigned bitrate;};
struct rtl_ax_ap {struct rtl_ax_tx *tx;int command_lock;u8 ra_feedback[128][8],ra_rssi[128][3];u64 ra_reports[128],ra_rssi_samples[128];struct {unsigned band;struct bitrate *bitrates;}band;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};struct ieee80211_vif {};
struct rtl_ax_station {unsigned macid;bool mesh;};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];struct {unsigned supp_rates[2];}deflink;};
struct station_info {u64 filled;int signal;struct rate_info txrate;};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw*h){return h->radio;}
'''
code+=function(driver/'tx.c','rtl_ax_tx_success')
tx_source=(driver/'tx.c').read_text()
a=tx_source.index('\t\tif (slot.macid && !(info->flags & IEEE80211_TX_CTL_NO_ACK))')
b=tx_source.index('\n\t\tif (info->flags & IEEE80211_TX_CTL_AMPDU)',a)
code+=r'''
#define IEEE80211_TX_CTL_NO_ACK BIT(0)
#define IEEE80211_TX_STAT_ACK BIT(1)
static void rpq_metric(struct rtl_ax_tx *tx,unsigned macid,unsigned flags){
 struct {unsigned macid;}slot={macid};struct {unsigned flags;}storage={flags},*info=&storage;
'''+tx_source[a:b]+'\n}\n'

code+=function(driver/'monitor.h','rtl_ax_sta_statistics')
code+=function(driver/'monitor.h','rtl_ax_get_expected_throughput')
code+=r'''
#define ARITH_SHIFT 8
#define TEST_FRAME_LEN 8192
#define LINK_FAIL_THRESH 95
#define MAX_METRIC 0xffffffffU
#define NL80211_PLINK_ESTAB 4
struct mesh_stats {unsigned plink_state,fail_avg,tx_rate_avg;};
struct sta_info {struct mesh_stats *mesh;struct ieee80211_hw *hw;struct ieee80211_sta sta;};
struct ieee80211_local {};
static unsigned ewma_mesh_fail_avg_read(unsigned *v){return *v;}
static unsigned ewma_mesh_tx_rate_avg_read(unsigned *v){return *v;}
static unsigned sta_get_expected_throughput(struct sta_info *s){return rtl_ax_get_expected_throughput(s->hw,&s->sta);}
'''
code+=function(kernel/'net/mac80211/mesh_hwmp.c','airtime_link_metric_get')
code+=r'''
int main(void){
 struct rtl_ax_tx tx={};struct bitrate rates[]={{10},{20},{55},{110},{60},{90},{120},{180},{240},{360},{480},{540}};
 struct rtl_ax_ap r={.tx=&tx,.band={0,rates}};struct ieee80211_hw hw={&r};struct ieee80211_vif vif;
 struct ieee80211_sta sta={.drv_priv={{1,true}},.deflink={{0xfff,0xff}}};
 ewma_rtl_ax_success_add(&tx.peer_success[1],100);
 assert(rtl_ax_get_expected_throughput(&hw,&sta)==1000);
 r.band.band=1;r.band.bitrates=rates+4;assert(rtl_ax_get_expected_throughput(&hw,&sta)==6000);
 r.ra_reports[1]=1;r.ra_rssi_samples[1]=1;r.ra_rssi[1][0]=152;
 struct rate_info rate;struct station_info info={};
 for(unsigned i=0;i<12;i++){
  r.ra_feedback[1][4]=i;r.ra_feedback[1][5]=0;
  assert(rtl_ax_rate_report(r.ra_feedback[1],&rate)&&rate.legacy==rates[i].bitrate);
  rtl_ax_sta_statistics(&hw,&vif,&sta,&info);
  assert(info.txrate.legacy==rate.legacy&&info.signal==-34&&info.filled==(BIT_ULL(7)|BIT_ULL(8)));
  assert(rtl_ax_get_expected_throughput(&hw,&sta)==rates[i].bitrate*100);
 }
 for(unsigned mode=1;mode<=3;mode++)for(unsigned bw=0;bw<(mode==1?2:4);bw++)for(unsigned gi=0;gi<(mode==3?6:2);gi++)for(unsigned nss=1;nss<=2;nss++){
  for(unsigned mcs=0;mcs<=(mode==1?7:mode==2?9:11);mcs++){
   r.ra_feedback[1][4]=mcs+(nss-1)*(mode==1?8:16);r.ra_feedback[1][5]=mode|(gi<<2)|(bw<<5);
   assert(rtl_ax_rate_report(r.ra_feedback[1],&rate));
   assert(rate.mcs==mcs+(mode==1?(nss-1)*8:0));
   if(mode!=1)assert(rate.nss==nss);
   assert((rate.bw==(unsigned[]){RATE_INFO_BW_20,RATE_INFO_BW_40,RATE_INFO_BW_80,RATE_INFO_BW_160}[bw]));
   if(mode==3)assert(rate.he_gi==(gi==1||gi==3||gi==5?0:gi==2||gi==4?1:2));
   else assert(!!(rate.flags&RATE_INFO_FLAGS_SHORT_GI)==!!gi);
   assert(rtl_ax_get_expected_throughput(&hw,&sta)==cfg80211_calculate_bitrate(&rate)*100);
  }
 }
 r.ra_feedback[1][4]=27;r.ra_feedback[1][5]=3|(5<<2)|(3<<5);
 u32 peak=rtl_ax_get_expected_throughput(&hw,&sta);assert(peak==2401900);
 struct mesh_stats mesh={.plink_state=NL80211_PLINK_ESTAB};struct sta_info peer={&mesh,&hw,sta};struct ieee80211_local local;
 u32 good=airtime_link_metric_get(&local,&peer);
 rpq_metric(&tx,1,IEEE80211_TX_CTL_NO_ACK);assert(rtl_ax_tx_success(&tx,1)==100);
 rpq_metric(&tx,0,0);assert(rtl_ax_tx_success(&tx,1)==100);
 for(unsigned i=0;i<8;i++)rpq_metric(&tx,1,0);
 u32 bad=airtime_link_metric_get(&local,&peer);assert(bad>good&&rtl_ax_get_expected_throughput(&hw,&sta)<peak);
 for(unsigned i=0;i<100;i++)rpq_metric(&tx,1,0);
 assert(!rtl_ax_tx_success(&tx,1));assert(rtl_ax_get_expected_throughput(&hw,&sta)==1);assert(airtime_link_metric_get(&local,&peer)>bad);
 mesh.plink_state=0;assert(airtime_link_metric_get(&local,&peer)==MAX_METRIC);mesh.plink_state=NL80211_PLINK_ESTAB;
 for(unsigned i=0;i<100;i++)rpq_metric(&tx,1,IEEE80211_TX_STAT_ACK);
 assert(airtime_link_metric_get(&local,&peer)<=good+1);
 r.ra_feedback[1][4]|=128;assert(!rtl_ax_rate_report(r.ra_feedback[1],&rate));
 memset(&info,0,sizeof(info));rtl_ax_sta_statistics(&hw,&vif,&sta,&info);assert(info.filled==BIT_ULL(7));
 r.ra_feedback[1][4]=12;r.ra_feedback[1][5]=0;assert(!rtl_ax_rate_report(r.ra_feedback[1],&rate));
 r.ra_reports[1]=0;sta.deflink.supp_rates[1]=0;assert(!rtl_ax_get_expected_throughput(&hw,&sta));
 sta.deflink.supp_rates[1]=0xff;sta.drv_priv[0].macid=0;assert(!rtl_ax_get_expected_throughput(&hw,&sta));
 sta.drv_priv[0].macid=1;sta.drv_priv[0].mesh=false;assert(!rtl_ax_get_expected_throughput(&hw,&sta));
 puts("Mesh metric: legacy/HT/VHT/HE bandwidth/NSS/GI, kernel bitrate and HWMP caller, ACK loss/recovery/disconnect, RSSI and MU boundary PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='mesh-metric-') as temp:
    p=Path(temp);(p/'test.c').write_text(code)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-fsanitize=address,undefined','-I'+str(driver),str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
