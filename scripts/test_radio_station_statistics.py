#!/usr/bin/env python3
"""Verify the live driver's nl80211 statistics use firmware reports, not requests."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'artifacts/live-radio-pe-ni-5g/monitor.h').read_text()
start = source.index('static void rtl_ax_sta_statistics(')
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
common = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
enum {RATE_INFO_BW_20,RATE_INFO_BW_5,RATE_INFO_BW_10,RATE_INFO_BW_40,RATE_INFO_BW_80,RATE_INFO_BW_160};
enum {RATE_INFO_FLAGS_MCS=1,RATE_INFO_FLAGS_VHT_MCS=2,RATE_INFO_FLAGS_SHORT_GI=4};
enum {NL80211_STA_INFO_SIGNAL=7,NL80211_STA_INFO_TX_BITRATE=8};
struct rtl_ax_ap {int command_lock;u8 ra_feedback[128][8],ra_rssi[128][3];u64 ra_reports[128],ra_rssi_samples[128];};
struct ieee80211_hw {struct rtl_ax_ap *radio;};struct ieee80211_vif {int member;};
struct rtl_ax_station {unsigned macid;};struct ieee80211_sta {struct rtl_ax_station drv_priv[1];};
struct station_info {u64 filled;int signal;struct {unsigned flags,mcs,nss,bw;}txrate;};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
'''
test = r'''
int main(void) {
 struct rtl_ax_ap radio={};struct ieee80211_hw hw={&radio};struct ieee80211_sta sta={{{7}}};
 struct ieee80211_vif vif={};struct station_info info={};
 rtl_ax_sta_statistics(&hw,&vif,&sta,&info);assert(!info.filled);
 radio.ra_reports[7]=1;radio.ra_rssi_samples[7]=1;radio.ra_rssi[7][0]=152;
 unsigned bw[]={RATE_INFO_BW_20,RATE_INFO_BW_40,RATE_INFO_BW_80,RATE_INFO_BW_160};
 for(unsigned mode=1;mode<3;mode++)for(unsigned width=0;width<4;width++)for(unsigned gi=0;gi<2;gi++) {
  memset(&info,0,sizeof(info));radio.ra_feedback[7][4]=mode==1?15:25;
  radio.ra_feedback[7][5]=mode|(gi<<2)|(width<<5);
  rtl_ax_sta_statistics(&hw,&vif,&sta,&info);
  assert(info.signal==-34 && info.txrate.bw==bw[width]);
  assert(info.txrate.flags==((mode==1?1:2)|(gi?4:0)));
  assert(info.txrate.mcs==(mode==1?15:9));assert(info.txrate.nss==(mode==1?0:2));
  assert(info.filled==(BIT_ULL(7)|BIT_ULL(8)));
 }
 radio.ra_feedback[7][4]|=128;memset(&info,0,sizeof(info));
 rtl_ax_sta_statistics(&hw,&vif,&sta,&info);assert(info.filled==BIT_ULL(7));
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-station-statistics-') as temp:
    c, exe = Path(temp) / 'test.c', Path(temp) / 'test'
    c.write_text(common + source[start:end] + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('Station statistics: firmware HT/VHT, all bandwidths, short GI, RSSI, and MU boundary passed')
