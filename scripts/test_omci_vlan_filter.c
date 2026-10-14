/* SPDX-License-Identifier: GPL-2.0-only */
/* Real OMCI provisioning -> native classifier, checked against factory modes. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"
#include "rtl9617c-pon-path.h"

static unsigned int transaction;
static void be16(uint8_t *p,unsigned int v){p[0]=v>>8;p[1]=v;}
static unsigned int le16(const uint8_t *p){return p[0]|p[1]<<8;}
static void request(struct omci_agent *a,unsigned int action,unsigned int cls,unsigned int entity,
                    const uint8_t *body,unsigned int length)
{
 uint8_t frame[44]={},reply[2048];
 be16(frame,++transaction);frame[2]=0x40|action;frame[3]=10;be16(frame+4,cls);be16(frame+6,entity);
 frame[43]=0x28;memcpy(frame+8,body,length);
 assert(tyfza1_omci_dispatch(a,frame,sizeof(frame),reply,0)==44 && !reply[8]);
}

int main(void)
{
 /* Target libomci_mib.so:0x23650, independently executed for operations00..21. */
 const unsigned char mode[]={0,1,2,3,6,0,2,4,7,0,2,5,8,0,2,3,6,4,7,5,8,
                             9,9,9,9,9,9,9,3,6,4,7,5,8};
 const unsigned int vids[]={65535,0,100,101};
 for(unsigned int operation=0;operation<sizeof(mode);operation++) {
  struct omci_identity identity={.serial={'T','E','S','T',1,2,3,4},.equipment="TYFZA1"};
  struct omci_agent *a=tyfza1_omci_new(&identity);
  uint8_t body[32]={},wire[4096];struct rtl_itu_paths paths={};
  body[0]=0x80;be16(body+2,1063);request(a,8,262,0x8000,body,4);
  memset(body,0,sizeof(body));be16(body,0x8327);be16(body+2,0x8000);body[4]=3;
  be16(body+5,0x8007);be16(body+7,0xffff);be16(body+9,0xffff);be16(body+11,0xffff);
  request(a,4,268,0x100,body,13);
  memset(body,0,sizeof(body));be16(body,0x100);body[2]=5;be16(body+3,0xffff);be16(body+5,0xffff);
  request(a,4,266,0x300,body,10);
  memset(body,0,sizeof(body));be16(body,1);body[2]=1;body[3]=5;be16(body+4,0x300);
  request(a,4,47,0x400,body,14);
  memset(body,0,sizeof(body));be16(body,(5<<13)|(1<<12)|100);body[24]=operation;body[25]=1;
  request(a,4,84,0x400,body,26);
  int length=tyfza1_omci_paths(a,wire);assert(length>=12 && le16(wire)==3);
  paths.count=le16(wire+2);assert(length==12+(int)paths.count*RTL_ITU_PATH_WIRE_BYTES);
  for(unsigned int i=0;i<paths.count;i++) {
   const uint8_t *p=wire+12+i*RTL_ITU_PATH_WIRE_BYTES;
   paths.path[i]=(struct rtl_itu_path){.alloc_id=le16(p),.gem_id=le16(p+2),.vlan=le16(p+4),
    .pbits=p[6],.multicast=p[7],.queue=p[8],.encrypted=p[9],
    .tcont=le16(p+10),.downstream_pbits=p[12],.vlan_exclude=p[23],.index=8};
  }
  for(unsigned int v=0;v<4;v++)for(unsigned int p=0;p<8;p++)for(unsigned int d=0;d<2;d++) {
   unsigned int vlan=vids[v];int tagged=vlan!=65535,allowed=0;
   switch(mode[operation]) {
   case 0:allowed=1;break;case 1:allowed=!tagged;break;case 2:allowed=tagged;break;
   case 3:allowed=!tagged || vlan==100;break;case 4:allowed=!tagged || p==5;break;
   case 5:allowed=!tagged || (vlan==100 && p==5);break;
   case 6:allowed=tagged && vlan==100;break;case 7:allowed=tagged && p==5;break;
   case 8:allowed=tagged && vlan==100 && p==5;break;
   }
   unsigned int tci=(p<<13)|(d<<12)|(vlan&4095);
   const struct rtl_itu_path *path=rtl_itu_path_select(&paths,tagged?tci&4095:65535,tci>>13);
   if(!!path!=allowed) {
    fprintf(stderr,"op=%02x mode=%u vlan=%u PCP=%u DEI=%u selected=%u expected=%u\n",
     operation,mode[operation],vlan,p,d,!!path,allowed);assert(0);
   }
   if(path)assert(path->gem_id==0x8327 && path->tcont==1);
   assert(!!rtl_itu_path_downstream(&paths,vlan,1U<<p)==allowed);
  }
  /* A VID-only hardware flow must also obey the complete PCP policy. */
  for(unsigned int v=0;v<4;v++) {
   int all=1;
   for(unsigned int p=0;p<8;p++)
    all &= rtl_itu_path_select(&paths,vids[v],p)!=NULL;
   assert(!!rtl_itu_path_flow(&paths,vids[v])==all);
   assert(!!rtl_itu_path_downstream(&paths,vids[v],255)==all);
  }
  tyfza1_omci_free(a);
 }
 /* Upstream mapper priorities do not limit downstream frames on the GEM. */
 struct rtl_itu_paths paths={.count=1,.path={{.vlan=100,.pbits=1,.downstream_pbits=255,.index=8}}};
 assert(rtl_itu_path_downstream(&paths,100,255)==&paths.path[0]);
 assert(!rtl_itu_path_flow(&paths,100));
 paths.count=2;paths.path[1]=paths.path[0];paths.path[1].index=9;paths.path[1].pbits=254;
 assert(rtl_itu_path_select(&paths,100,5)==&paths.path[1]);
 assert(!rtl_itu_path_downstream(&paths,100,255));
 puts("OMCI VLAN filters: 34 factory modes, both directions, tagged/untagged, VID/PCP, DEI preservation and GEM selection passed");
}
