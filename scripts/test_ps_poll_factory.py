#!/usr/bin/env python3
"""Compare PS-Poll firmware register writes with this board's module."""
from pathlib import Path
import json,re,subprocess,sys,tempfile
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'))
from arm64_module_oracle import ModuleOracle
h=r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
def function(path,name):
 s=path.read_text();a=s.rfind('\n',0,s.index(name+'('))+1;b=s.index('{',a);depth=1;end=b+1
 while depth:depth+=(s[end]=='{')-(s[end]=='}');end+=1
 return s[a:end]
code=r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t __le16;
#define BIT(n) (1U<<(n))
#define IEEE80211_AC_BE 2
struct rtl_ax_io{void *ctx;unsigned char(*read8)(void *,unsigned int);void(*write32)(void *,unsigned int,unsigned int);void(*write8)(void *,unsigned int,unsigned char);void(*delay_us)(void *,unsigned int);};
struct rtl_ax_station{unsigned int macid;};
struct ieee80211_sta{unsigned int aid,uapsd_queues;struct rtl_ax_station drv_priv[1];};
struct rtl_ax_rx_packet{bool crc_error;unsigned int bytes;unsigned char *payload;};
struct rtl_ax_ap{struct rtl_ax_io io;void *hw;unsigned long long ps_poll_requests,ps_poll_errors;};
static struct ieee80211_sta sta={.aid=1,.drv_priv={{1}}};static int locked;
static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(void *hw,const u8 *a,const u8 *b){(void)hw;(void)a;(void)b;return &sta;}
#define rcu_read_lock() (++locked)
#define rcu_read_unlock() (--locked)
static __le16 get_unaligned(const __le16 *p){__le16 v;memcpy(&v,p,2);return v;}
static __le16 get_unaligned_le16(const u8 *p){__le16 v;memcpy(&v,p,2);return v;}
static int ieee80211_is_pspoll(__le16 fc){return(fc&0xfc)==0xa4;}
static unsigned int reads,delays,busy,nwrites,writes[5][3];
static unsigned char read8(void *p,unsigned int a){(void)p;if(a!=0x7160)return 0;return 0xa0|(++reads<=busy);}
static void write32(void *p,unsigned int a,unsigned int v){(void)p;writes[nwrites][0]=a;writes[nwrites][1]=v;writes[nwrites++][2]=4;}
static void write8(void *p,unsigned int a,unsigned char v){write32(p,a,v);writes[nwrites-1][2]=1;}
static void delay(void *p,unsigned int us){(void)p;if(locked||us!=200)__builtin_trap();delays++;}
'''
code+=function(h/'mac-runtime.h','rtl_ax_host_getpkt')+'\n'+function(h/'monitor.h','rtl_ax_ps_poll')
code+=r'''
int main(void){unsigned char frame[32]={0xa4,0,1,0xc0};struct rtl_ax_ap radio={.io={NULL,read8,write32,write8,delay}};struct rtl_ax_rx_packet packet={false,20,frame};unsigned int cases[]={0,3,99,100};
for(unsigned int c=0;c<4;c++){reads=delays=nwrites=0;busy=cases[c];radio.ps_poll_errors=radio.ps_poll_requests=0;rtl_ax_ps_poll(&radio,&packet);printf("{\"busy\":%u,\"reads\":%u,\"delays\":%u,\"errors\":%llu,\"writes\":[",busy,reads,delays,radio.ps_poll_errors);for(unsigned int i=0;i<nwrites;i++)printf("%s[%u,%u,%u]",i?",":"",writes[i][0],writes[i][1],writes[i][2]);puts("]}");}
for(unsigned int c=0;c<4;c++){reads=delays=nwrites=busy=0;packet.crc_error=c==0;packet.bytes=c==1?19:20;frame[2]=c==2?2:1;sta.uapsd_queues=c==3?BIT(IEEE80211_AC_BE):0;rtl_ax_ps_poll(&radio,&packet);if(reads||nwrites)return 2;}return 0;}
'''
with tempfile.TemporaryDirectory()as tmp:
 p=Path(tmp);(p/'test.c').write_text(code);subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
 native=[json.loads(l)for l in subprocess.check_output([str(p/'test')],text=True).splitlines()]
module=r/'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
for chip in ('8852c','8192xb'):
 for row in native:
  reads=[];writes=[];delays=[]
  def boundary(name,args):
   if name=='read8':reads.append(args[1]);return 0xa0|(len(reads)<=row['busy'])
   if name in('write8','write32'):writes.append([args[1],args[2],1 if name=='write8'else 4]);return 0
   if name=='delay':delays.append(args[1]);return 0
   if name in('lock','unlock','log','poll_c2hreg','mac_dbg_status_dump'):return 0
   raise AssertionError((name,args))
  names=['mac_host_getpkt_h2c','proc_msg_reg','_mac_send_h2creg','get_h2creg_offset_'+chip]
  o=ModuleOracle(module,names,boundary);a=o.scratch;adapter,inner,io,platform,ops=[a+x for x in(0x1000,0x3000,0x3100,0x3200,0x3300)]
  for addr,v in((adapter,inner),(inner,io),(adapter+0x18,platform),(adapter+0x5d8,ops)):o.word(addr,v)
  o.uc.mem_write(adapter+0x21,b'\x04');o.uc.mem_write(adapter+0x49,b'\x01')
  for off,name in((0,'read8'),(8,'write8'),(0x28,'write32')):o.word(io+off,o.callback(name))
  for off,name in((0x70,'delay'),(0xa0,'lock'),(0xa8,'unlock'),(0xb0,'log')):o.word(platform+off,o.callback(name))
  o.word(ops+0x68,o.functions['get_h2creg_offset_'+chip]);ret=o.call('mac_host_getpkt_h2c',adapter,1,0)
  assert row['writes']==writes and row['reads']==len(reads)and row['delays']==len(delays)and row['errors']==bool(ret),(chip,row,ret,writes)
for aid in (1,2):
 for delivery in (0,1):
  released=[]
  def admission(name,args):
   if name=='rtw_phl_host_getpkt':released.append(args[1:3]);return 0
   raise AssertionError((name,args))
  o=ModuleOracle(module,['rtw_proccess_pspoll','rtw_get_txq_idx'],admission,external_symbols={'vabits_actual':0x5000e000,'kimage_voffset':0x5000e008});a=o.scratch
  adapter,dvobj,frame,sta,peer,header=[a+x for x in(0x1000,0x2000,0x3500,0x4000,0xb000,0xc000)]
  o.word(adapter+192,dvobj);o.word(dvobj+4544,a+0x3000);o.word(frame+296,header);o.word(sta,peer)
  o.uc.mem_write(header,bytes((0xa4,0,aid,0xc0)));o.uc.mem_write(peer+26,b'\x01\x00\x01')
  o.uc.mem_write(sta+0x6652,(delivery<<2).to_bytes(2,'little'))
  o.call('rtw_proccess_pspoll',adapter,frame,sta)
  assert released==([[1,0]]if aid==1 and not delivery else []),(aid,delivery,released)
print('PS-Poll: 8 factory register vectors and 4 factory AID/UAPSD decisions; CRC/truncation rejected; waiting outside RCU.')
