from pathlib import Path
import re
import subprocess
import tempfile
import shutil

work = Path(tempfile.mkdtemp(prefix='wifi-control-'))
source = (Path(__file__).resolve().parents[1]/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()


def function(name):
    start = re.search(r'^static[^;]*?\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start()
    end=source.index('{',start)+1
    depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}')
        end+=1
    return source[start:end]+'\n'


code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
typedef uint32_t u32;
#define READ_ONCE(x) (x)
#define min(a,b) ((a)<(b)?(a):(b))
#define IEEE80211_TX_CTL_NO_CCK_RATE (1U<<27)
#define NL80211_BAND_2GHZ 0
#define __ffs(x) __builtin_ctz(x)
struct ieee80211_sta {struct {u32 supp_rates[2];}deflink;};
struct ieee80211_vif {struct {u32 basic_rates;}bss_conf;};
struct ieee80211_tx_info {u32 flags;struct {struct ieee80211_vif*vif;struct {int idx,flags,count;}rates[1];}control;};
struct rate {unsigned hw_value;};
struct rtl_ax_ap {u32 ra_basic_mask;struct {unsigned band,n_bitrates;struct rate *bitrates;}band;int command_mutex;struct {void(*write16)(void*,unsigned,unsigned);}io;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};struct cfg80211_bitrate_mask {unsigned dummy;};
static unsigned hw_rts_time,hw_rts_length,locked,reg_value;
static struct rtl_ax_ap*rtl_ax_monitor_radio(struct ieee80211_hw*h){return h->radio;}
static void mutex_lock(int*p){(void)p;assert(!locked++);}
static void mutex_unlock(int*p){(void)p;assert(locked--==1);}
static void write16(void*p,unsigned reg,unsigned value){(void)p;assert(locked&&reg==0xc614);reg_value=value;}
'''
code+=function('rtl_ax_tx_basic_rate')+function('rtl_ax_set_rts_threshold')
code+=r'''
int main(void){
 struct rate rates[12];for(unsigned i=0;i<12;i++)rates[i].hw_value=i;
 struct rtl_ax_ap r={.ra_basic_mask=~0U,.band={0,12,rates},.io={write16}};struct ieee80211_hw hw={&r};
 struct ieee80211_sta sta={.deflink={.supp_rates={0xfff,0xff}}};
 struct ieee80211_vif vif={.bss_conf={0x150}};struct ieee80211_tx_info info={.control={.vif=&vif}};
 assert(rtl_ax_tx_basic_rate(&r,&sta,&info)==4&&info.control.rates[0].idx==4&&info.control.rates[0].count==1);
 vif.bss_conf.basic_rates=15;assert(rtl_ax_tx_basic_rate(&r,&sta,&info)==0);
 info.flags=IEEE80211_TX_CTL_NO_CCK_RATE;assert(rtl_ax_tx_basic_rate(&r,&sta,&info)==4);
 info.flags=0;sta.deflink.supp_rates[0]=12;assert(rtl_ax_tx_basic_rate(&r,&sta,&info)==2);
 r.band.band=1;r.band.n_bitrates=8;r.band.bitrates=rates+4;vif.bss_conf.basic_rates=0x54;
 assert(rtl_ax_tx_basic_rate(&r,&sta,&info)==6&&info.control.rates[0].idx==2);
 info.control.vif=NULL;assert(rtl_ax_tx_basic_rate(&r,NULL,&info)==4);
 assert(!rtl_ax_set_rts_threshold(&hw,-1,(u32)-1)&&reg_value==0);
 assert(!rtl_ax_set_rts_threshold(&hw,-1,2347)&&reg_value==0xff92);
 assert(!rtl_ax_set_rts_threshold(&hw,-1,65535)&&reg_value==0xffff);
 assert(!rtl_ax_set_rts_threshold(&hw,-1,0)&&reg_value==0xff00);
 hw_rts_time=64;hw_rts_length=160;assert(!rtl_ax_set_rts_threshold(&hw,-1,(u32)-1)&&reg_value==0x020a);

 puts("Unified RA helpers: 2G/5G basic and peer rates, NO_CCK, status rate, RTS units/saturation/default PASS");
}
'''
(work/'test_basic.c').write_text(code)
subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-fsanitize=address,undefined',str(work/'test_basic.c'),'-o',str(work/'test_basic')],check=True)
subprocess.run([str(work/'test_basic')],check=True)

shutil.rmtree(work)
