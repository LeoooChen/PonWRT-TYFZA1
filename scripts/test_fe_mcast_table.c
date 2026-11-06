/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-mcast.h"

struct model {
 unsigned int reg[0xb000/4],groups[256][2],actions[64][3];
 unsigned int calls,fail,writes;
 bool applied,corrupt;
};
static unsigned int read_reg(void *context,unsigned int address)
{
 struct model *m=context;
 assert(address<sizeof(m->reg) && !(address&3));
 return m->reg[address/4];
}
static void write_reg(void *context,unsigned int address,unsigned int value)
{
 struct model *m=context;
 assert(address<sizeof(m->reg) && !(address&3));
 m->writes++;m->reg[address/4]=value;
}
static int indirect(void *context,unsigned int address,unsigned int command)
{
 struct model *m=context;
 unsigned int *row,count,data;
 bool fail=++m->calls==m->fail;

 assert(!read_reg(m,address));
 assert(address==0xaa64 || address==0x1634);
 if(address==0xaa64) {row=m->groups[command&255];count=2;data=0xaa6c;}
 else {row=m->actions[command&63];count=3;data=0x1640;}
 assert((command&~(0xc0000000U|(address==0xaa64?255U:63U)))==0);
 m->reg[address/4]=command;
 if(fail && !m->applied)return -ETIMEDOUT;
 for(unsigned int i=0;i<count;i++) {
  if(command&0x40000000U)row[i]=read_reg(m,data-i*4);
  else m->reg[(data-i*4)/4]=row[i]^(m->corrupt?1U:0U);
 }
 if(fail)return -ETIMEDOUT;
 m->reg[address/4]=0;
 return 0;
}

int main(void)
{
 struct model m={0};
 struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 unsigned int words[3]={0xa55a1234,0x87654321,0x00000a5b},out[3];
 for(unsigned int i=1;i<256;i++) {
  words[0]=i;words[1]=0xa5000000U|i;
  assert(!rtl_fe_mc_group_write(&io,i,words));
 }
 for(unsigned int i=1;i<256;i++) {
  assert(!rtl_fe_mc_group_read(&io,i,out));
  assert(out[0]==i && out[1]==(0xa5000000U|i));
 }
 assert(!rtl_fe_mc_group_read(&io,0,out) && !out[0] && !out[1]);
 for(unsigned int i=0;i<64;i++) {
  words[0]=i;words[1]=0x46007ffd;words[2]=0;
  assert(!rtl_fe_mc_action_write(&io,i,words));
  assert(!rtl_fe_mc_action_read(&io,i,out) && !memcmp(out,words,sizeof(out)));
 }
 unsigned int calls=m.calls,writes=m.writes;
 assert(rtl_fe_mc_group_write(&io,0,words)==-EINVAL);
 for(unsigned int i=256;i<=1024;i++) {
  assert(rtl_fe_mc_group_write(&io,i,words)==-EINVAL);
  assert(rtl_fe_mc_group_read(&io,i,out)==-EINVAL);
 }
 assert(rtl_fe_mc_action_write(&io,64,words)==-EINVAL);
 assert(rtl_fe_mc_action_read(&io,64,out)==-EINVAL);
 assert(m.calls==calls && m.writes==writes);

 /* Every write/readback failure, including applied writes with a lost
  * completion, stays an error. No new command or DATA write may overwrite
  * an engine still busy from the previous transaction.
  */
 for(unsigned int kind=0;kind<2;kind++)
  for(unsigned int applied=0;applied<2;applied++)
   for(unsigned int fail=1;fail<=2;fail++) {
    memset(&m,0,sizeof(m));m.fail=fail;m.applied=applied;
    int ret=kind?rtl_fe_mc_action_write(&io,3,words):rtl_fe_mc_group_write(&io,3,words);
    assert(ret==-ETIMEDOUT);
    calls=m.calls;writes=m.writes;
    ret=kind?rtl_fe_mc_action_write(&io,3,words):rtl_fe_mc_group_write(&io,3,words);
    assert(ret==-EBUSY && m.calls==calls && m.writes==writes);
    memset(out,0xa5,sizeof(out));
    ret=kind?rtl_fe_mc_action_read(&io,3,out):rtl_fe_mc_group_read(&io,3,out);
    assert(ret==-EBUSY && out[0]==0xa5a5a5a5 && m.calls==calls);
    /* Model the late completion of the outstanding command. */
    m.reg[(kind?0x1634:0xaa64)/4]=0;m.fail=0;m.corrupt=true;
    ret=kind?rtl_fe_mc_action_write(&io,3,words):rtl_fe_mc_group_write(&io,3,words);
    assert(ret==-EIO);
    m.corrupt=false;
    if(kind)assert(!rtl_fe_mc_action_write(&io,3,words));
    else assert(!rtl_fe_mc_group_clear(&io,3) && !m.groups[3][0] && !m.groups[3][1]);
   }
 puts("Multicast table IO: NI255/action64, no index alias, readback, busy and partial-write failures passed");
 return 0;
}
