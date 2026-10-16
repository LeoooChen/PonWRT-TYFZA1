#!/usr/bin/env python3
"""Run production RX_START branch with a live route and failed/successful withdrawal."""
from pathlib import Path
import subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h'
s=p.read_text();old='--before' in sys.argv
if old:s=subprocess.check_output(['git','show','43a8a4c:'+str(p.relative_to(ROOT))],cwd=ROOT,text=True)
a=s.index('\tcase IEEE80211_AMPDU_RX_START:');b=s.index('\tcase IEEE80211_AMPDU_RX_STOP:',a)
common=r'''#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define IEEE80211_AMPDU_RX_START 1
#define RTL_AX_H2C_CMD 0
#define HZ 100
#define dev_info(...) ((void)0)
struct wiphy {int unused;};struct ieee80211_hw {struct wiphy *wiphy;};
struct rtl_ax_station {unsigned macid;};
struct ieee80211_ampdu_params {unsigned action,tid,ssn;};
struct rtl_ax_ap {int tx,cleanup_work;struct {u8 macid,tid;bool valid,deleting;} rx_ba[8];int rx_ba_result;unsigned rx_ba_started;};
static bool gate;static unsigned withdrawals,commands,late_commands;static int fail_retire;
static int __attribute__((unused)) rtl_ax_tx_ni_retire_peer(int tx,unsigned id){(void)tx;assert(id==7);withdrawals++;gate=false;return fail_retire;}
static void rtl_ax_store32(u8 *p,uint32_t v){memcpy(p,&v,4);}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *p,unsigned bytes,bool ack){
 (void)r;assert(q==0 && cat==1 && cls==12 && fn==0 && bytes==8 && ack);commands++;late_commands+=gate;
 uint32_t v;memcpy(&v,p,4);assert(v==(3U|2U<<4|7U<<8|512U<<20));return 0;}
static void wiphy_delayed_work_queue(struct wiphy *w,int *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);}
static int start(struct rtl_ax_ap *radio,struct ieee80211_hw *hw,struct rtl_ax_station *station,struct ieee80211_ampdu_params *params){u8 ba[8];unsigned i;int ret;switch(params->action){
'''
test=r'''default:return -EINVAL;}}
int main(void){
 struct rtl_ax_ap radio={0};struct ieee80211_hw hw={0};struct rtl_ax_station station={.macid=7};
 struct ieee80211_ampdu_params params={.action=1,.tid=2,.ssn=512};gate=true;fail_retire=-EIO;
#ifdef EXPECT_OLD
 assert(!start(&radio,&hw,&station,&params) && !withdrawals && commands==1 && late_commands==1);
 puts("REPRODUCED: BA hardware programmed while existing PE admission stayed open");
#else
 assert(start(&radio,&hw,&station,&params)==-EIO && withdrawals==1 && !commands && !radio.rx_ba[0].valid);
 fail_retire=0;gate=true;
 assert(!start(&radio,&hw,&station,&params) && withdrawals==2 && commands==1 && !late_commands);
 assert(radio.rx_ba[0].valid && !radio.rx_ba[0].deleting && radio.rx_ba[0].macid==7 && radio.rx_ba[0].tid==2);
 puts("RX BA start: withdraw existing PE route before hardware update; withdrawal failure leaves BA programming untouched");
#endif
}
'''
with tempfile.TemporaryDirectory(prefix='rx-ba-admission-',dir=ROOT/'artifacts') as tmp:
 tmp=Path(tmp);(tmp/'case.c').write_text(common+s[a:b]+test)
 cmd=['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined']
 if old:cmd.append('-DEXPECT_OLD')
 subprocess.run(cmd+[str(tmp/'case.c'),'-o',str(tmp/'case')],check=True)
 subprocess.run([str(tmp/'case')],check=True)
