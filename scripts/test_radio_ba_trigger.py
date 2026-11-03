from pathlib import Path
import subprocess
import tempfile
import shutil

work=Path(tempfile.mkdtemp(prefix='wifi-control-'))
source=(Path(__file__).resolve().parents[1]/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()
start=source.index('\t\t\tif (station && station->macid')
end=source.index('\n\n\t\t\twhile ((skb',start)
block=source[start:end]
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define READ_ONCE(x) (x)
#define BIT(n) (1U<<(n))
#define HZ 100
#define IEEE80211_AC_VO 0
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
static unsigned long jiffies=1000;
struct rtl_ax_station {unsigned macid;unsigned long ba_retry[8];};
struct sta {bool wme;struct {struct {bool ht_supported;}ht_cap;struct {bool vht_supported;}vht_cap;struct {bool has_he;}he_cap;}deflink;};
struct queue {struct sta*sta;unsigned tid;};
struct radio {bool peer_authorized[128];struct {unsigned aggregation[16],stopping;}tx_ba[128];};
static unsigned calls;
static int ieee80211_start_tx_ba_session(struct sta*s,unsigned tid,unsigned timeout){assert(s&&tid<8&&!timeout);calls++;return 0;}
static void trigger(struct radio *radio,struct queue *queue,struct rtl_ax_station *station,unsigned ac){
'''+block+r'''
}
int main(void){
 struct rtl_ax_station st={.macid=1};struct sta s={.wme=true,.deflink={.he_cap={true}}};
 struct queue q={&s,0};struct radio r={.peer_authorized={[1]=true}};
 trigger(&r,&q,&st,2);assert(calls==1&&st.ba_retry[0]==1100);
 trigger(&r,&q,&st,2);assert(calls==1);jiffies=1100;trigger(&r,&q,&st,2);assert(calls==2);
 jiffies=1200;r.tx_ba[1].aggregation[0]=64;trigger(&r,&q,&st,2);assert(calls==2);
 r.tx_ba[1].aggregation[0]=0;r.tx_ba[1].stopping=1;trigger(&r,&q,&st,2);assert(calls==2);r.tx_ba[1].stopping=0;
 trigger(&r,&q,&st,0);assert(calls==2);r.peer_authorized[1]=false;trigger(&r,&q,&st,2);assert(calls==2);
 r.peer_authorized[1]=true;q.tid=16;trigger(&r,&q,&st,2);assert(calls==2);q.tid=0;
 s.deflink.he_cap.has_he=false;trigger(&r,&q,&st,2);assert(calls==2);
 s.deflink.ht_cap.ht_supported=true;s.wme=false;trigger(&r,&q,&st,2);assert(calls==2);s.wme=true;
 trigger(&r,&q,NULL,2);assert(calls==2);trigger(&r,&q,&st,2);assert(calls==3);
 puts("BA trigger: actual data queue, authorized WMM HT/VHT/HE, voice/nondata exclusion, existing/stopping/retry gate PASS");
}
'''
(work/'test_ba.c').write_text(code)
subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(work/'test_ba.c'),'-o',str(work/'test_ba')],check=True)
subprocess.run([str(work/'test_ba')],check=True)

shutil.rmtree(work)
