/* SPDX-License-Identifier: GPL-2.0-only */
/* An OLT provisions GEM IWTP directly, without an 802.1p mapper. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"
#include "rtl9617c-pon-path.h"

static unsigned int transaction;
static void be16(uint8_t *p,unsigned int value){p[0]=value>>8;p[1]=value;}
static unsigned int le16(const uint8_t *p){return p[0]|p[1]<<8;}
static void request(struct omci_agent *agent,unsigned int action,unsigned int class_id,
                    unsigned int entity,const uint8_t *body,unsigned int length)
{
 uint8_t frame[44]={},reply[2048];
 be16(frame,++transaction);frame[2]=0x40|action;frame[3]=10;
 be16(frame+4,class_id);be16(frame+6,entity);frame[43]=0x28;
 memcpy(frame+8,body,length);
 assert(tyfza1_omci_dispatch(agent,frame,sizeof(frame),reply,0)==44 && reply[8]==0);
}

int main(void)
{
 for(unsigned int tagged=0;tagged<2;tagged++) {
  struct omci_identity identity={.serial={'T','E','S','T',1,2,3,4},.equipment="TYFZA1"};
  struct omci_agent *agent=tyfza1_omci_new(&identity);
  uint8_t body[32]={},wire[4096];
  struct rtl_itu_paths paths={.count=1};
  body[0]=0x80;be16(body+2,1063);request(agent,8,262,0x8000,body,4);
  memset(body,0,sizeof(body));be16(body,0x8327);be16(body+2,0x8000);body[4]=3;
  be16(body+5,0x8007);be16(body+7,0xffff);be16(body+9,0xffff);be16(body+11,0xffff);
  request(agent,4,268,0x100,body,13);
  memset(body,0,sizeof(body));be16(body,0x100);body[2]=5;be16(body+3,0xffff);be16(body+5,0xffff);
  request(agent,4,266,0x300,body,10);
  memset(body,0,sizeof(body));be16(body,1);body[2]=1;body[3]=5;be16(body+4,0x300);
  request(agent,4,47,0x400,body,14);
  if(tagged) {
   memset(body,0,sizeof(body));be16(body,100);body[24]=4;body[25]=1;
   request(agent,4,84,0x400,body,26);
  }
  assert(tyfza1_omci_paths(agent,wire)==12+RTL_ITU_PATH_WIRE_BYTES && le16(wire)==4);
  paths.path[0]=(struct rtl_itu_path){.alloc_id=le16(wire+12),.gem_id=le16(wire+14),
   .vlan=le16(wire+16),.pbits=wire[18],.multicast=wire[19],.queue=wire[20],
   .encrypted=wire[21],.tcont=le16(wire+22),.downstream_pbits=wire[24],.vlan_exclude=wire[35],.index=8};
  assert(paths.path[0].gem_id==0x8327 && paths.path[0].alloc_id==1063);
  assert(paths.path[0].vlan==(tagged?100:65535));
  assert(paths.path[0].pbits==255);
  for(unsigned int priority=0;priority<8;priority++)
   assert(rtl_itu_path_select(&paths,tagged?100:65535,priority)==&paths.path[0]);
  assert(rtl_itu_path_flow(&paths,tagged?100:65535)==&paths.path[0]);
  assert(rtl_itu_path_downstream(&paths,tagged?100:65535,255)==&paths.path[0]);
  tyfza1_omci_free(agent);
 }
 puts("Direct GEM IWTP: OLT OMCI frames produce untagged/tagged business paths for all PCPs and hardware flow selection");
}
