#!/usr/bin/env python3
"""Original HE BSSID color producer and native shared-CAM update lifecycle."""
from pathlib import Path
import sys,struct,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
# Reuse the target HAL fixture without running its unrelated PPE matrix.
setup=(root/'scripts/test_radio_he_join.py').read_text().split('with tempfile.TemporaryDirectory() as temp:',1)[0]
setup=setup.replace("'get_set_bits_of_msk',","'get_set_bits_of_msk','set_role_bss_clr',")
setup=setup.replace(" if name=='hal_mac_get_macid_num':"," if name=='color_read':return color_register[0]\n if name=='color_write':color_register[0]=a[2];return 0\n if name=='hal_mac_get_macid_num':")
color_register=[0]
exec(compile(setup,str(root/'scripts/test_radio_he_join.py'),'exec'))
color_ops=0x6060000;o.uc.mem_map(color_ops,0x10000)
o.word(hal,color_ops);o.word(color_ops,color_ops+256)
o.word(color_ops+256+32,o.callback("color_read"));o.word(color_ops+256+40,o.callback("color_write"))
count=0
for client in (False,True):
 o.uc.mem_write(role+8,struct.pack('<I',1 if client else 2))
 for he in (False,True):
  for color in range(64):
   o.uc.mem_write(sta+2513,bytes([color]))
   o.uc.mem_write(sta+2332,struct.pack('<I',32 if he else 8))
   o.uc.mem_write(info,bytes(512));o.uc.mem_write(out,bytes(512))
   o.call('_hal_stainfo_to_macrinfo',hal,sta,info,5 if client else 2,1)
   o.call('role_info_init',hal,info);o.call('fill_bssid_cam_info',hal,info,out)
   assert o.uc.mem_read(out+53,1)[0]==(color if he else 0)
   color_register[0]=0xa5a5a5a5;o.call('set_role_bss_clr',hal,info)
   assert color_register[0]==((0xa5a5a5a5&~63)|(color if he else 0))
   count+=1
# Actual native writers, with H2C acknowledgment and MMIO modeled separately.
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
pc=(driver/'pci.c').read_text()
def extract(name):
 start=pc.index('static int '+name+'(');pos=pc.index('{',start)+1;depth=1
 while depth:depth+=(pc[pos]=='{')-(pc[pos]=='}');pos+=1
 return pc[start:pos]
code=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
typedef uint8_t u8;
#define RTL_AX_H2C_DATA 1
#include "'''+str(driver/'cam-owner.h')+r'''"
struct rtl_ax_ap {struct rtl_ax_cam_owners cam_owners;u8 role_cam[60],bss_color;int command_mutex,io;bool sta_role;};
static u8 sent[60];static unsigned calls,writes;static int error;static unsigned reg=0xa5a5a5a5;
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned c,unsigned cls,unsigned f,const void *p,unsigned len,bool ack){
 assert(!r->command_mutex&&q==1&&c==1&&cls==6&&f==0&&len==60&&ack);memcpy(sent,p,60);calls++;return error;
}
static void mutex_lock(int *m){assert(!*m);*m=1;}
static void mutex_unlock(int *m){assert(*m);*m=0;}
static void rtl_ax_port0_color(int *io,unsigned color){(void)io;assert(color<64);reg=(reg&~63U)|color;writes++;}
'''
code+='#define ETH_ALEN 6\nstatic void rtl_ax_store32(u8 *p,uint32_t v){memcpy(p,&v,4); }\n'
code+="static uint32_t rtl_ax_ap_read32(const u8 *p){uint32_t v;memcpy(&v,p,4);return v;}\n"
code+=function('rtl_ax_peer_cam(')+function('rtl_ax_role_cam(')+function('rtl_ax_client_cam(')
code+=extract('rtl_ax_cam_write')+extract('rtl_ax_bss_color_update')
code+=r'''
int main(void){
 struct rtl_ax_ap r={};u8 initial[60],cam[60];
 for(unsigned i=0;i<60;i++)initial[i]=i*3+1;
 for(unsigned color=0;color<64;color++)for(unsigned failure=0;failure<2;failure++){
  memcpy(r.role_cam,initial,60);r.bss_color=17;calls=writes=0;reg=0xa5a5a5a5;error=failure?-ETIMEDOUT:0;
  assert(rtl_ax_bss_color_update(&r,color)==error);assert(calls==1&&sent[53]==color);
  for(unsigned i=0;i<60;i++)if(i!=53)assert(sent[i]==initial[i]);
  if(failure){assert(!writes&&r.bss_color==17&&!memcmp(r.role_cam,initial,60));}
  else {assert(writes==1&&r.bss_color==color&&!memcmp(r.role_cam,sent,60));assert(reg==((0xa5a5a5a5&~63U)|color));}
  error=0;assert(!rtl_ax_bss_color_update(&r,color));
  for(unsigned peer=0;peer<128;peer++)for(unsigned valid=0;valid<2;valid++){
   memset(&r.cam_owners,0,sizeof(r.cam_owners));memcpy(cam,initial,60);cam[32]=peer;cam[8]=7;cam[53]=0;
   assert(!rtl_ax_cam_write(&r,cam));cam[8]=valid?7:6;
   assert(!rtl_ax_cam_write(&r,cam));assert(sent[53]==color&&sent[4]==0&&sent[8]==cam[8]);
   for(unsigned i=0;i<60;i++)if(i!=53)assert(sent[i]==cam[i]);
  }
 }
 for(unsigned color=0;color<64;color++)for(unsigned kind=0;kind<3;kind++)for(unsigned valid=0;valid<2;valid++){
  const u8 self[6]={2,1,2,3,4,5},peer[6]={2,9,8,7,6,5};
  r.bss_color=color;memset(&r.cam_owners,0,sizeof(r.cam_owners));
  if(kind==0)rtl_ax_role_cam(cam,0,self);
  else if(kind==1)rtl_ax_peer_cam(cam,127,self,peer,42,valid);
  else rtl_ax_client_cam(cam,1,self,peer,42,true,valid);
  /* The previous callers sent this zero color directly, overwriting CAM0. */
  assert(cam[53]==0);if(!(cam[8]&1)){cam[8]|=1;assert(!rtl_ax_cam_write(&r,cam));cam[8]&=~1;}
  assert(!rtl_ax_cam_prepare(&r.cam_owners,cam));memcpy(initial,cam,60);
  assert(!rtl_ax_cam_write(&r,cam)&&sent[53]==color);
  for(unsigned i=0;i<60;i++)if(i!=53)assert(sent[i]==initial[i]);
 }
 memset(&r.cam_owners,0,sizeof(r.cam_owners));r.sta_role=true;r.bss_color=19;
 memset(cam,0,60);cam[8]=7;cam[32]=1;cam[33]=64;cam[36]=42;
 assert(!rtl_ax_cam_write(&r,cam)&&!memcmp(r.role_cam,cam,60));
 assert(!rtl_ax_bss_color_update(&r,37)&&sent[33]==64&&sent[36]==42);
 memcpy(initial,r.role_cam,60);cam[36]=73;error=-ETIMEDOUT;
 assert(rtl_ax_cam_write(&r,cam)==-ETIMEDOUT&&!memcmp(initial,r.role_cam,60));error=0;
 calls=writes=0;assert(rtl_ax_bss_color_update(&r,64)==-EINVAL&&!calls&&!writes);
 puts("Native BSSID color: 128 ACK/failure/retry transitions, 32768 peer create/delete writes passed");
}
'''
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'test.c';binary=Path(temp)/'test';c.write_text(code)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
print(f'Original HAL→BSSID CAM color: {count} AP/STA/HE/legacy cases passed')
