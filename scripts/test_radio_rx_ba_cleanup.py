#!/usr/bin/env python3
"""Execute RX BA stop, cleanup and offload admission with retirement/H2C faults."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--revision")
args = parser.parse_args()
source = (subprocess.check_output(["git", "show", f"{args.revision}:{relative}"], cwd=root, text=True)
          if args.revision else (root / relative).read_text())


def function(name):
    start = source.index("static ", source.rfind("\n", 0, source.index(name + "(")))
    end = source.index("\n}\n", start) + 3
    return source[start:end]


common = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define HZ 100
#define RTL_AX_H2C_CMD 0
#define RTL_AX_SECURITY_ENTRIES 128
#define IEEE80211_NUM_TIDS 16
#define min(a,b) ((a)<(b)?(a):(b))
#define dev_info(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct wiphy {int unused;};struct wiphy_work {int unused;};
struct wiphy_delayed_work {struct wiphy_work work;};
struct rtl_ax_security_role {unsigned valid,tx_keyid,cam[2];};
struct rtl_ax_station {unsigned macid;};
struct rtl_ax_ap {
 struct {unsigned macid,tid;bool valid,deleting;}rx_ba[8];
 unsigned rx_ba_stopped;int rx_ba_result,tx,rx;
 bool ap_started,monitor_started,offload_retry,peer_authorized[128];
 struct {unsigned aggregation[16],stopping;}tx_ba[128];
 struct {bool pending;}retired_stations[128];
 struct rtl_ax_security_role security_roles[128];unsigned security_unlink[128];
 int ap_vif,offload_result;unsigned offload_started;
 struct wiphy_work offload_work;struct wiphy_delayed_work cleanup_work;
};
struct ieee80211_hw {struct wiphy *wiphy;};
struct params {unsigned tid;};
static int retire_error,command_error;static unsigned retired,commands,queued,ready,admitted,window;
static void rtl_ax_store32(u8 *p,u32 v){(void)p;(void)v;}
static int rtl_ax_tx_rx_retire(int tx,unsigned macid){(void)tx;assert(macid==7);retired++;return retire_error;}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,u8 *data,unsigned len,bool ack){
 (void)r;(void)data;assert(!q&&cat==1&&cls==12&&!fn&&len==8&&ack);commands++;return command_error;
}
static void rtl_ax_rx_ba_window(int rx,unsigned macid,unsigned tid,unsigned size){(void)rx;assert(macid==7&&tid==3);window=size;}
static void rtl_ax_offload_ready(struct rtl_ax_ap *r){(void)r;ready++;}
static void wiphy_delayed_work_queue(struct wiphy *w,struct wiphy_delayed_work *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);queued++;}
static void wiphy_work_queue(struct wiphy *w,struct wiphy_work *work){(void)w;(void)work;}
static bool rtl_ax_security_cleanup(struct rtl_ax_ap *r){(void)r;return false;}
static int rtl_ax_station_release(struct rtl_ax_ap *r,unsigned id){(void)r;(void)id;assert(0);return 0;}
static int rtl_ax_tx_ba_finish(struct rtl_ax_ap *r,unsigned id,unsigned tid){(void)r;(void)id;(void)tid;assert(0);return 0;}
static bool rtl_ax_tx_peer_security(int tx,unsigned id,unsigned cam,unsigned key){(void)tx;(void)cam;(void)key;assert(id==7);return true;}
struct wdev {int netdev;};static struct wdev wdev;
static struct wdev *ieee80211_vif_to_wdev(int vif){(void)vif;return &wdev;}
static int rtl_ax_tx_ni_peer(int tx,int dev,unsigned id){(void)tx;(void)dev;assert(id==7);admitted++;return 1;}
"""
stop = source.split("case IEEE80211_AMPDU_RX_STOP:\n", 1)[1].split("\n\t}\n", 1)[0]
common += function("rtl_ax_rx_ba_remove")
common += "\nstatic int stop(struct rtl_ax_ap *radio,struct ieee80211_hw *hw,struct rtl_ax_station *station,struct params *params){unsigned i;int ret;\n" + stop + "\n}\n"
common += function("rtl_ax_cleanup_work") + function("rtl_ax_offload_work")
common += r"""
int main(void){
 struct wiphy w={0};struct ieee80211_hw hw={&w};struct rtl_ax_station sta={7};struct params p={3};
 struct rtl_ax_ap r={.ap_started=true,.monitor_started=true};
 r.rx_ba[0].valid=true;r.rx_ba[0].macid=7;r.rx_ba[0].tid=3;window=64;
 r.peer_authorized[7]=true;r.tx_ba[7].aggregation[0]=64;r.security_roles[7].valid=1;
 retire_error=-ETIMEDOUT;
 assert(stop(&r,&hw,&sta,&p)==-ETIMEDOUT);
 assert(r.rx_ba[0].valid&&r.rx_ba[0].deleting&&queued==1&&!commands&&window==64);
 rtl_ax_offload_work(&w,&r.offload_work);assert(!admitted);
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(queued==2&&!commands&&r.rx_ba[0].deleting);
 retire_error=0;command_error=-ETIMEDOUT;
 rtl_ax_cleanup_work(&w,&r.cleanup_work.work);assert(queued==3&&commands==1&&r.rx_ba[0].deleting&&window==64);
 command_error=0;rtl_ax_cleanup_work(&w,&r.cleanup_work.work);
 assert(!r.rx_ba[0].valid&&!r.rx_ba[0].deleting&&window==0&&r.rx_ba_stopped==1&&ready==1);
 rtl_ax_offload_work(&w,&r.offload_work);assert(admitted==1);
 unsigned before=retired;assert(!stop(&r,&hw,&sta,&p)&&retired==before);
 /* Successful restart/stop reuses the same entry without delayed deletion. */
 r.rx_ba[0].valid=true;window=64;assert(!stop(&r,&hw,&sta,&p));
 assert(!r.rx_ba[0].valid&&window==0&&r.rx_ba_stopped==2);
 puts("PASS: RX BA retirement and H2C failures retry; pending deletion blocks offload; success resumes admission");
}
"""
with tempfile.TemporaryDirectory(prefix="radio-rx-ba-") as temp:
    p = Path(temp)
    (p / "test.c").write_text(common)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", str(p / "test.c"), "-o", str(p / "test")], check=True)
    subprocess.run([str(p / "test")], check=True)
