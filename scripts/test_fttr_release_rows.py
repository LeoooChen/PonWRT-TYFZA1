#!/usr/bin/env python3
"""Exercise real VLAN release against every partial restoration and foreign drift."""
from pathlib import Path
import os
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1];src=root/'port/packages/tyfza1-fttr/src'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "omci-vlan.h"
static unsigned char original[48],desired[48],remote[96];
static unsigned length,writes;
static void put16(unsigned char *p,unsigned n){p[0]=n>>8;p[1]=n;}
static void put32(unsigned char *p,unsigned n){p[0]=n>>24;p[1]=n>>16;p[2]=n>>8;p[3]=n;}
static void reply(struct fttr_me_job *m){
 unsigned op=m->request[2];memset(m->reply,0,44);
 if(op==FTTR_ME_SET){
  assert(m->mask==0x400);
  unsigned i;for(i=0;i<length;i+=16)if(!memcmp(remote+i,m->request+10,8))break;
  bool del=!memcmp(m->request+18,(unsigned char[8]){255,255,255,255,255,255,255,255},8);
  if(del){if(i<length){memmove(remote+i,remote+i+16,length-i-16);length-=16;}}
  else{memcpy(remote+i,m->request+10,16);if(i==length)length+=16;}
  writes++;
 }else if(m->mask==0x4000)put16(m->reply+11,8);
 else if(op==FTTR_ME_GET)put32(m->reply+11,length);
 else {assert(op==FTTR_ME_GET_NEXT);unsigned at=((unsigned)m->request[10]<<8|m->request[11])*29;unsigned n=length-at;if(n>29)n=29;memcpy(m->reply+11,remote+at,n);}
 m->state=FTTR_ME_DONE;m->error=0;m->attempts=1;
}
int main(void){
 unsigned cases=0;
 /* One changed key, one added key, one untouched key. Exercise both rows
  * independently restored, changed/extra unrelated rows and corrupt keys. */
 for(unsigned mode=0;mode<9;mode++){
  memset(original,0,sizeof(original));memset(desired,0,sizeof(desired));
  original[7]=1;original[15]=10;original[23]=3;original[31]=30;
  memcpy(desired,original,16);desired[15]=11;desired[23]=2;desired[31]=20;
  struct fttr_vlan_change v={.onu=7,.instance=1,.stage=FTTR_VLAN_DONE,.verified=true,.recover_release=true,.sent=3};
  v.plan.count=2;memcpy(v.plan.rows,desired,32);
  v.before.table=(struct fttr_vlan_table){.capacity=8,.length=32,.received=32,.complete=true,.data=malloc(32)};
  memcpy(v.before.table.data,original,32);
  v.after.table=(struct fttr_vlan_table){.capacity=8,.length=48,.received=48,.complete=true,.data=malloc(48)};
  memcpy(v.after.table.data,desired,32);memcpy(v.after.table.data+32,original+16,16);
  memcpy(remote,v.after.table.data,48);length=48;writes=0;
  if(mode&1)memcpy(remote,original,16);
  if(mode&2){memmove(remote+16,remote+32,16);length-=16;}
  if(mode==4)remote[15]=99;
  if(mode==5)remote[length-1]=99;
  if(mode==6){memcpy(remote+length,original+16,16);remote[length+7]=4;length+=16;}
  if(mode==7)remote[7]=9;
  if(mode==8)v.recover_release=false;
  uint16_t counter=0;int result=fttr_vlan_change_release(&v,&counter);
  for(unsigned guard=0;result>=0&&result!=2&&guard<50;guard++){
   struct fttr_me_job *m=fttr_vlan_change_pending(&v);assert(m);
   if(result==1)reply(m);
   result=fttr_vlan_change_update(&v,&counter);
  }
  if(mode>=4&&mode<8)assert(result==-EUCLEAN&&!writes);
  else{assert(result==2&&v.restored&&length==32&&!memcmp(remote,original,32));assert(writes==2U-!!(mode&1)-!!(mode&2));}
  free(v.before.table.data);free(v.after.table.data);cases++;
 }
 printf("VLAN partial release: %u full production read/restore cases, untouched/foreign row protection passed\n",cases);
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I'+str(src),str(p/'test.c'),str(src/'omci-vlan.c'),str(src/'omci-me.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env=dict(os.environ,UBSAN_OPTIONS='halt_on_error=1'))
