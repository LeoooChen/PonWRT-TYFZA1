#!/usr/bin/env python3
"""Execute per-interface EDCA callbacks and restoration through shared WMM0."""
from pathlib import Path
import argparse,json,re,subprocess,tempfile
r=Path(__file__).resolve().parents[1];d=r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path);a=p.parse_args()
s=(d/'monitor.h').read_text()
def function(name):
 start=re.search(r'^static (?:void|int) '+name+r'\([^;]*?\)\s*\{',s,re.M).start();end=s.index('{',start)+1;depth=1
 while depth:depth+=(s[end]=='{')-(s[end]=='}');end+=1
 return s[start:end]+'\n'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define IEEE80211_NUM_ACS 4
#define RTL_AX_8852C 3
#define ilog2(x) (31-__builtin_clz((unsigned)(x)))
#define is_power_of_2(x) ((x) && !((x)&((x)-1)))
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
#include "role-identity.h"
struct ieee80211_tx_queue_params {u16 txop,cw_min,cw_max;u8 aifs;};
struct ieee80211_vif {struct rtl_ax_vif drv_priv[1];struct {bool use_short_slot;}bss_conf;};
struct rtl_ax_ap {struct ieee80211_vif *vif_owner[128];u32 chip,edca_order,edca_initial[4],edca_words[4],traffic_edca_saved[2];u8 edca_initial_valid,edca_valid;int command_mutex;void *tx;
 struct {bool enabled;unsigned timeout,streams;}traffic_wmm;
 struct {void *ctx;u32(*read32)(void*,unsigned);void(*write32)(void*,unsigned,u32);}io;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};
static unsigned writes,quota;static u32 regs[4];static const unsigned addresses[]={0xc30c,0xc308,0xc300,0xc304};
static void mutex_lock(int *m){assert(!*m);*m=1;}static void mutex_unlock(int *m){assert(*m);*m=0;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static unsigned index_of(unsigned reg){for(unsigned i=0;i<4;i++)if(addresses[i]==reg)return i;assert(0);return 0;}
static u32 read32(void *ctx,unsigned reg){struct rtl_ax_ap *radio=ctx;assert(radio->command_mutex);return regs[index_of(reg)];}
static void write32(void *ctx,unsigned reg,u32 value){struct rtl_ax_ap *radio=ctx;assert(radio->command_mutex);regs[index_of(reg)]=value;writes++;}
static void rtl_ax_write32(struct rtl_ax_ap *r,unsigned reg,u32 value){write32(r,reg,value);}
static void rtl_ax_tx_wmm(void *tx,unsigned value){(void)tx;quota=value;}
'''+''.join(function(n) for n in ('rtl_ax_edca_select','rtl_ax_edca_apply','rtl_ax_edca_active','rtl_ax_ap_conf_tx'))
# Real dynamic WMM transition block: publishes override then restores its current saved values.
start=s.index('\tif (radio->traffic_wmm.enabled && !was_wmm)');end=s.index('\n\tfor (id = 1;',start)
code+='static void dynamic(struct rtl_ax_ap *radio,bool was_wmm){mutex_lock(&radio->command_mutex);\n'+s[start:end]+'\nmutex_unlock(&radio->command_mutex);}\n'
code+=r'''
int main(void){
 for(unsigned chip=3;chip<=4;chip++)for(unsigned ac=0;ac<4;ac++){
  struct rtl_ax_ap r={.chip=chip};r.io=(typeof(r.io)){&r,read32,write32};struct ieee80211_hw hw={&r};
  struct ieee80211_vif a={.bss_conf={true}},b={.bss_conf={true}};r.vif_owner[0]=&a;r.vif_owner[41]=&b;
  a.drv_priv[0].edca_active=b.drv_priv[0].edca_active=true;
  const struct ieee80211_tx_queue_params pa={.txop=47,.cw_min=3,.cw_max=7,.aifs=2},pb={.txop=94,.cw_min=7,.cw_max=15,.aifs=3};
  for(unsigned i=0;i<4;i++)regs[i]=0xa500+i;unsigned initial=regs[ac];
  assert(!rtl_ax_ap_conf_tx(&hw,&a,0,ac,&pa));u32 aword=regs[ac];
  assert(aword==(47U<<16|3U<<12|2U<<8|((chip==3?16:10)+18)));
  assert(!rtl_ax_ap_conf_tx(&hw,&b,0,ac,&pb));u32 bword=regs[ac];
  assert(bword==(94<<16|4<<12|3<<8|((chip==3?16:10)+27)));
  /* Older A slot update must not combine B's params with A's slot size. */
  a.bss_conf.use_short_slot=false;mutex_lock(&r.command_mutex);rtl_ax_edca_apply(&r,&a,ac);mutex_unlock(&r.command_mutex);
  assert(regs[ac]==bword);u32 along=a.drv_priv[0].edca[ac].word;
  assert(along==(47U<<16|3U<<12|2U<<8|((chip==3?16:10)+2*(chip==3?9:20))));
  rtl_ax_edca_active(&r,&b,false);assert(regs[ac]==along);
  rtl_ax_edca_active(&r,&b,true);assert(regs[ac]==bword);
  /* New config wins; stop/remove restores the still-live BSS's request. */
  assert(!rtl_ax_ap_conf_tx(&hw,&a,0,ac,&pa)&&regs[ac]==along);
  rtl_ax_edca_active(&r,&a,false);assert(regs[ac]==bword);r.vif_owner[0]=NULL;
  rtl_ax_edca_active(&r,&b,false);assert(regs[ac]==initial&&!(r.edca_valid&BIT(ac)));
  assert(!r.edca_initial_valid);regs[ac]=0xbe00+ac;
  rtl_ax_edca_active(&r,&b,true);assert(regs[ac]==bword);
  rtl_ax_edca_active(&r,&b,false);assert(regs[ac]==0xbe00+ac&&!r.edca_initial_valid);
  r.edca_order=~0U;unsigned before=writes;assert(rtl_ax_ap_conf_tx(&hw,&b,0,ac,&pb)==-EOVERFLOW&&writes==before);
  struct ieee80211_tx_queue_params bad=pb;bad.cw_min=2;assert(rtl_ax_ap_conf_tx(&hw,&b,0,ac,&bad)==-EINVAL);
  bad=pb;bad.txop=2048;assert(rtl_ax_ap_conf_tx(&hw,&b,0,ac,&bad)==-EINVAL);
  assert(rtl_ax_ap_conf_tx(&hw,&b,0,4,&pb)==-EINVAL&&writes==before);
 }
 /* Latest config and ownership changes remain behind temporary dynamic BE. */
 for(unsigned remove=0;remove<2;remove++) {
  struct rtl_ax_ap r={.chip=3};r.io=(typeof(r.io)){&r,read32,write32};struct ieee80211_hw hw={&r};
  struct ieee80211_vif a={.bss_conf={true}},b={.bss_conf={true}};
  r.vif_owner[0]=&a;r.vif_owner[41]=&b;a.drv_priv[0].edca_active=b.drv_priv[0].edca_active=true;
  struct ieee80211_tx_queue_params p={.cw_min=15,.cw_max=63,.aifs=3};
  regs[2]=0xaaaa;regs[3]=0xbbbb;
  for(unsigned ac=2;ac<4;ac++){assert(!rtl_ax_ap_conf_tx(&hw,&a,0,ac,&p));p.aifs++;assert(!rtl_ax_ap_conf_tx(&hw,&b,0,ac,&p));}
  r.traffic_wmm.enabled=true;dynamic(&r,false);assert(regs[2]==0x642b);
  p.aifs=7;assert(!rtl_ax_ap_conf_tx(&hw,&a,0,2,&p)&&regs[2]==0x642b);
  u32 want=a.drv_priv[0].edca[2].word;
  if(remove){rtl_ax_edca_active(&r,&a,false);r.vif_owner[0]=NULL;want=b.drv_priv[0].edca[2].word;}
  assert(r.traffic_edca_saved[0]==want);
  r.traffic_wmm.enabled=false;dynamic(&r,true);assert(regs[2]==want);
  r.traffic_wmm.enabled=true;r.traffic_wmm.timeout=33;r.traffic_wmm.streams=4;quota=512;dynamic(&r,false);
  rtl_ax_edca_active(&r,&a,false);rtl_ax_edca_active(&r,&b,false);
  assert(!r.traffic_wmm.enabled&&!r.traffic_wmm.timeout&&r.traffic_wmm.streams==1&&!quota&&regs[2]==0xaaaa&&regs[3]==0xbbbb);
 }
 puts("EDCA ownership: both chips/all ACs, cross-VIF slot update, latest request, stop/restart/removal, dynamic restore, validation and overflow PASS");
}
'''
if a.original:
 rows=json.loads(a.original.read_text())['records'];checks=[]
 for row in rows:
  ac={'VO':0,'VI':1,'BE':2,'BK':3}[row['AC']]
  chip=3 if row['chip']=='8852c' else 4
  write=row['writes'];assert len(write)==1 and write[0]['operation']=='write32'
  checks.append('{struct rtl_ax_ap r={.chip=%d};r.io=(typeof(r.io)){&r,read32,write32};struct ieee80211_hw hw={&r};struct ieee80211_vif vif={.bss_conf={true}};vif.drv_priv[0].edca_active=true;r.vif_owner[0]=&vif;struct ieee80211_tx_queue_params p={.cw_min=%d,.cw_max=%d,.aifs=%d,.txop=%d};assert(!rtl_ax_ap_conf_tx(&hw,&vif,0,%d,&p));assert(addresses[%d]==%s&&regs[%d]==%s);}' % (chip,(1<<row['ECW_min'])-1,(1<<row['ECW_max'])-1,row['AIFSN'],row['TXOP_32us'],ac,ac,write[0]['address'],ac,write[0]['value']))
 code=code.replace('int main(void){','int main(void){\n'+ '\n'.join(checks),1)
with tempfile.TemporaryDirectory() as temp:
 p=Path(temp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-misleading-indentation','-fsanitize=address,undefined','-I'+str(d),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
