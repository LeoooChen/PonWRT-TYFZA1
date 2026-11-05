#!/usr/bin/env python3
"""Exercise regulatory notifications across actual native DFS stop/start code."""
from pathlib import Path
import argparse
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--monitor', type=Path)
args = parser.parse_args()
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'


def function(file, name):
    path = args.monitor if file == 'monitor.h' and args.monitor else driver / file
    source = path.read_text()
    at = source.index(name + '(')
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define RTL_AX_8852C 2
#define rtl_ax_scan_complete_frame(...) ((void)0)
#define BIT(n) (1U << (n))
#define READ_ONCE(v) (v)
#define WRITE_ONCE(p,v) ((p)=(v))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define wiphy_delayed_work_cancel(w,p) ((void)(w),(void)(p))
#define wiphy_work_cancel(w,p) ((void)(w),(void)(p))
#define tasklet_kill(p) ((void)(p))
#define wiphy_delayed_work_queue(w,p,d) ((void)(w),(void)(p),(void)(d))
#define HZ 100
enum {NL80211_DFS_UNSET,NL80211_DFS_FCC,NL80211_DFS_ETSI,NL80211_DFS_JP};
struct regulatory_request {char alpha2[2];unsigned dfs_region;};
struct rtl_ax_io {void (*write32)(void *,unsigned,unsigned);};
struct rtl_ax_tx {bool blocked;};
struct rtl_ax_dfs {bool enabled,hardware_enabled,in_cac,detected;unsigned region;int error;};
struct rtl_ax_ap {
 struct rtl_ax_dfs dfs;struct rtl_ax_io io;struct rtl_ax_tx *tx;
 unsigned chip,dfs_domain;bool radar_required,monitor_started,beacon_enabled,ap_started,ra_sampling;
 char power_country[3];int command_mutex,command_lock;
 int cleanup_work,ra_work,dm_work,ra_update_work,tx_tasklet,rx;
 int requested_tx_dbm,power_inputs,power_hardware;
 struct {unsigned band,primary,center,bandwidth;bool configured;}channel;
 struct {int dev;} *pdev;
};
struct wiphy {struct rtl_ax_ap *radio;};
struct ieee80211_hw {struct rtl_ax_ap *radio;struct wiphy *wiphy;struct {bool radar_enabled;int chandef;}conf;};
static struct ieee80211_hw hw;
static unsigned dfs_region,retunes,power_writes,hardware_starts;
static int hardware_result,errors;
static char last_error[256];
static struct ieee80211_hw *wiphy_to_ieee80211_hw(struct wiphy *w){(void)w;return &hw;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *h){return h->radio;}
static void dev_err(void *dev,const char *fmt,...) {
 va_list a;(void)dev;errors++;va_start(a,fmt);vsnprintf(last_error,sizeof(last_error),fmt,a);va_end(a);
}
static int rtl_ax_tx_pause(struct rtl_ax_tx *tx){(void)tx;return 0;}
static void rtl_ax_rx_close(int rx){(void)rx;}
static void rtl_ax_rx_open(int rx){(void)rx;}
static void rtl_ax_tx_open(struct rtl_ax_tx *tx){(void)tx;}
static void rtl_ax_receive_mode(struct rtl_ax_io *io,unsigned mode){(void)io;(void)mode;}
static void rtl_ax_update32(struct rtl_ax_io *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;}
static int rtl_ax_dfs_hardware(struct rtl_ax_io *io,bool enable){(void)io;hardware_starts+=enable;return hardware_result;}
static void rtl_ax_tx_dfs_block(struct rtl_ax_tx *tx,bool block){tx->blocked=block;}
static void rtl_ax_dfs_initialize(struct rtl_ax_dfs *dfs,unsigned region){dfs->region=region;}
static void rtl_ax_dfs_channel(struct rtl_ax_dfs *dfs,unsigned region,unsigned primary,unsigned bandwidth){(void)dfs;(void)region;(void)primary;(void)bandwidth;}
static int rtl_ax_power_apply(struct rtl_ax_io *io,unsigned chip,void *inputs,unsigned band,unsigned primary,unsigned center,unsigned width,char *country,int dbm,void *state){
 (void)io;(void)chip;(void)inputs;(void)band;(void)primary;(void)center;(void)width;(void)country;(void)dbm;(void)state;power_writes++;return 0;
}
'''
source = common + function('pci.c', 'rtl_ax_dfs_stop_locked') + function('pci.c', 'rtl_ax_dfs_start_locked')
source += r'''
/* Channel/RFK hardware is outside this regression. Keep the production
 * detector's domain rejection and error publication at this boundary. */
static int rtl_ax_receive_configure(struct rtl_ax_ap *r,unsigned primary,unsigned center,unsigned width){
 (void)primary;(void)center;(void)width;retunes++;
 rtl_ax_tx_dfs_block(r->tx,true);
 r->dfs.error=rtl_ax_dfs_stop_locked(r);
 if(!r->dfs.error)r->dfs.error=rtl_ax_dfs_start_locked(r);
 return r->dfs.error;
}
static int rtl_ax_chandef_locked(struct rtl_ax_ap *r,int *def){
 (void)def;return rtl_ax_receive_configure(r,r->channel.primary,r->channel.center,r->channel.bandwidth);
}
'''
source += function('monitor.h', 'rtl_ax_monitor_start')
source += function('monitor.h', 'rtl_ax_monitor_stop')
source += function('monitor.h', 'rtl_ax_regulatory_notify')
source += r'''
int main(void){
 struct rtl_ax_tx tx={};struct rtl_ax_ap r={.tx=&tx,.chip=RTL_AX_8852C,
  .channel={.primary=36,.center=50,.bandwidth=3,.configured=true}};
 struct wiphy w={.radio=&r};struct {int dev;} pdev={};
 hw.radio=&r;hw.wiphy=&w;hw.conf.radar_enabled=true;r.pdev=(void *)&pdev;
 struct regulatory_request world={.alpha2={'0','0'},.dfs_region=NL80211_DFS_UNSET};
 struct regulatory_request cn={.alpha2={'C','N'},.dfs_region=NL80211_DFS_FCC};

 /* A failed .start need not receive .stop. A country hint must not
  * start the old detector on an interface which never started. */
 r.dfs_domain=1;hardware_result=-ETIMEDOUT;
 assert(rtl_ax_monitor_start(&hw)==-ETIMEDOUT);
 assert(!r.monitor_started&&r.radar_required&&r.dfs.error==-ETIMEDOUT);
 retunes=0;hardware_result=0;
 rtl_ax_regulatory_notify(&w,&world);
 if(retunes||errors){fprintf(stderr,"BUG: stopped radio retuned %u times, errors=%d: %s",retunes,errors,last_error);return 1;}
 assert(r.dfs.error==-ETIMEDOUT&&!r.dfs.hardware_enabled);
 assert(!memcmp(r.power_country,"00",2)&&r.dfs_domain==0&&power_writes==1);
 rtl_ax_regulatory_notify(&w,&cn);
 assert(!retunes&&r.dfs_domain==1&&!memcmp(r.power_country,"CN",2));

 r.monitor_started=true;r.radar_required=true;
 assert(!rtl_ax_dfs_start_locked(&r));
 assert(r.dfs.enabled&&r.dfs.in_cac&&tx.blocked);
 rtl_ax_monitor_stop(&hw,false);
 assert(!r.monitor_started&&!r.radar_required&&!r.dfs.enabled&&!r.dfs.hardware_enabled);
 rtl_ax_regulatory_notify(&w,&world);assert(!retunes&&!errors);

 /* The next start must use the new valid domain. */
 rtl_ax_regulatory_notify(&w,&cn);r.radar_required=true;
 assert(!rtl_ax_dfs_start_locked(&r));r.monitor_started=true;
 unsigned starts=hardware_starts;
 rtl_ax_regulatory_notify(&w,&world);
 assert(retunes==1&&errors==1&&r.dfs.error==-EOPNOTSUPP&&tx.blocked);
 assert(!r.dfs.enabled&&hardware_starts==starts);
 assert(strstr(last_error,"-95 country=00 dfs_region=0 primary=36 center=50 width=160MHz"));

 /* Supported domain updates still reach the real detector setup. */
 unsigned domains[]={NL80211_DFS_FCC,NL80211_DFS_ETSI,NL80211_DFS_JP};
 unsigned native[]={1,3,2};
 for(unsigned i=0;i<3;i++){
  cn.dfs_region=domains[i];rtl_ax_regulatory_notify(&w,&cn);
  assert(r.dfs_domain==native[i]&&r.dfs.region==native[i]&&!r.dfs.error);
 }
 hardware_result=-ETIMEDOUT;
 rtl_ax_regulatory_notify(&w,&cn);assert(r.dfs.error==-ETIMEDOUT&&errors==2);
 rtl_ax_monitor_stop(&hw,false);assert(r.dfs.error==-ETIMEDOUT&&errors==3);
 rtl_ax_regulatory_notify(&w,&world);
 assert(r.dfs.error==-ETIMEDOUT&&errors==3);
 puts("Regulatory lifecycle: idle/failed-start notifications do not rearm DFS; stop clears demand; restart uses new domain; active unknown-domain and hardware errors stay visible");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='rtl-ax-regulatory-') as folder:
    folder = Path(folder)
    (folder / 'test.c').write_text(source)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    str(folder / 'test.c'), '-o', str(folder / 'test')], check=True)
    subprocess.run([str(folder / 'test')], check=True)
