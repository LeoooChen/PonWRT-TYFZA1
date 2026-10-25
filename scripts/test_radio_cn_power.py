#!/usr/bin/env python3
"""Check the production country notifier's power-only CN override."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--monitor', type=Path, default=root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h')
args = parser.parse_args()
text = args.monitor.read_text()
start = text.rfind('\n', 0, text.index('rtl_ax_regulatory_notify(')) + 1
end = text.index('{', start) + 1
depth = 1
while depth:
    depth += (text[end] == '{') - (text[end] == '}')
    end += 1
function = text[start:end]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define IEEE80211_CHAN_DISABLED 1U
#define RTL_AX_TX_POWER_MAX_DBM 30
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define rtl_ax_receive_configure(...) 0
#define rtl_ax_power_apply(...) 0
#define dev_err(...) ((void)0)
enum {NL80211_DFS_UNSET,NL80211_DFS_FCC,NL80211_DFS_ETSI,NL80211_DFS_JP};
struct regulatory_request {char alpha2[2];unsigned dfs_region;};
struct channel {unsigned flags;int max_power,max_reg_power,orig_mpwr;};
struct rtl_ax_ap {
 int command_mutex;char power_country[3];unsigned dfs_domain;
 bool monitor_started,radar_required;
 struct {bool configured;} channel;
 struct {unsigned n_channels;} band;
 struct channel channels[4];
};
struct wiphy {struct rtl_ax_ap *radio;};
#define wiphy_to_ieee80211_hw(w) (w)
#define rtl_ax_monitor_radio(w) ((w)->radio)
static bool cn_power_30;
'''
source += function
source += r'''
int main(void){
 const char *countries[]={"CN","US","00"};
 unsigned cases=0;
 for(unsigned enabled=0;enabled<2;enabled++)
  for(unsigned country=0;country<3;country++)
   for(int old_cap=20;old_cap<=23;old_cap+=3){
    struct rtl_ax_ap radio={.band={.n_channels=4}};
    struct wiphy wiphy={.radio=&radio};
    struct regulatory_request request={.dfs_region=NL80211_DFS_FCC};
    memcpy(request.alpha2,countries[country],2);cn_power_30=enabled;
    unsigned flags[]={0,8,16,IEEE80211_CHAN_DISABLED|32};
    for(unsigned i=0;i<4;i++)radio.channels[i]=(struct channel){flags[i],old_cap,old_cap,30};
    rtl_ax_regulatory_notify(&wiphy,&request);
    assert(!memcmp(radio.power_country,countries[country],2)&&radio.dfs_domain==1);
    for(unsigned i=0;i<4;i++){
     int expected=enabled&&!country&&i<3?30:old_cap;
     assert(radio.channels[i].max_power==expected);
     assert(radio.channels[i].max_reg_power==expected);
     assert(radio.channels[i].flags==flags[i]&&radio.channels[i].orig_mpwr==30);
    }
    cases++;
   }
 printf("PASS: %u notifier cases; CN power ceilings only, disabled/radar/indoor flags and non-CN limits unchanged\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-cn-power-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(work / 'native.c'),
                    '-o', str(work / 'native')], check=True)
    subprocess.run([str(work / 'native')], check=True)
