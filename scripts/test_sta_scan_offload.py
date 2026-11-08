#!/usr/bin/env python3
"""A connected STA must withdraw NI before software scan changes its channel."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()

def function(name):
    start = re.search(r'^static .*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end, depth = source.index('{', start) + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define RTL_AX_CLIENT_MACID 1
#define RTL_AX_8852C 2
enum {NL80211_CHAN_WIDTH_20_NOHT,NL80211_CHAN_WIDTH_20,NL80211_CHAN_WIDTH_40,NL80211_CHAN_WIDTH_80,NL80211_CHAN_WIDTH_160};
struct cfg80211_chan_def {unsigned width,center_freq1;struct {unsigned hw_value;} *chan;};
struct rtl_ax_ap {void *sta_vif;bool scanning,sta_associated,ap_started,ra_sampling;int scan_offload_result,command_mutex,io,tx;unsigned chip;struct {int error;}dfs;};
struct ieee80211_hw {struct rtl_ax_ap *radio;struct {struct cfg80211_chan_def chandef;}conf;};struct ieee80211_vif {int unused;};
static struct rtl_ax_ap *active;static unsigned moved,retired,ready,losses;static int error,channel_error;
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static int rtl_ax_tx_ni_retire_peer(int tx,unsigned id){(void)tx;assert(id==1&&active->scanning&&!active->command_mutex);retired++;return error;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void rtl_ax_update32(int *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;assert(active->command_mutex);}
static void rtl_ax_receive_mode(int *io,unsigned mode){(void)io;assert(mode==2&&!active->scanning);}
static unsigned ieee80211_frequency_to_channel(unsigned freq){return freq;}
static int rtl_ax_receive_configure(struct rtl_ax_ap *r,unsigned chan,unsigned center,unsigned bw){(void)chan;(void)center;(void)bw;moved++;r->dfs.error=channel_error;return channel_error;}
static void rtl_ax_offload_ready(void *r){assert(r==active&&!active->scanning);ready++;}
static inline void ieee80211_connection_loss(struct ieee80211_vif *v){assert(v==active->sta_vif);losses++;}
'''
test = r'''
int main(void){
 struct rtl_ax_ap radio={.sta_associated=true,.chip=2};struct ieee80211_hw hw={.radio=&radio};struct ieee80211_vif vif={};radio.sta_vif=&vif;
 struct cfg80211_chan_def channel={.width=NL80211_CHAN_WIDTH_20};
 typeof(*channel.chan) chan={1};channel.chan=&chan;active=&radio;hw.conf.chandef=channel;
 for(unsigned fault=0;fault<2;fault++){
  error=fault?-ETIMEDOUT:0;moved=retired=ready=0;
  rtl_ax_sw_scan_start(&hw,&vif,0);assert(retired==1);
  assert(rtl_ax_chandef_locked(&radio,&channel)==error);assert(moved==!fault);
  rtl_ax_sw_scan_complete(&hw,&vif);assert(ready==1);
  assert(!rtl_ax_chandef_locked(&radio,&channel));
 }
 /* mac80211 attempts the operating channel before sw_scan_complete; a
  * failed restore leaves hardware off-channel even though hw.conf changed. */
 error=0;ready=losses=0;rtl_ax_sw_scan_start(&hw,&vif,0);
 channel_error=-ETIMEDOUT;
 assert(rtl_ax_chandef_locked(&radio,&channel)==-ETIMEDOUT);
 rtl_ax_sw_scan_complete(&hw,&vif);
 assert(!ready&&losses==1&&!radio.sta_associated);
 radio.sta_associated=true;ready=losses=0;channel_error=0;
 rtl_ax_sw_scan_start(&hw,&vif,0);
 radio.dfs.error=-ETIMEDOUT;unsigned before=moved;
 rtl_ax_sw_scan_complete(&hw,&vif);
 assert(moved==before+1&&ready==1&&!losses&&radio.sta_associated);
 puts("STA scan: NI withdrawn before RF moves; failed withdrawal blocks change; operating-channel restore retries before offload and disconnects on persistent failure");
}
'''
with tempfile.TemporaryDirectory(prefix='sta-scan-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + '\n'.join(function(name) for name in
        ('rtl_ax_chandef_locked', 'rtl_ax_sw_scan_start', 'rtl_ax_sw_scan_complete')) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
