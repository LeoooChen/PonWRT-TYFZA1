/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-mcast-owner.h"
#include "rtl9617c-fe-mcast-route.h"

struct model {
 unsigned int reg[0xb000/4],groups[256][2],actions[64][3];
 unsigned int map[256],dbuf[8],elp[64][2],membership[64][2];
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

 assert(!(read_reg(m,address)&0x80000000U));
 if(address==0xaa64) {row=m->groups[command&255];count=2;data=0xaa6c;}
 else if(address==0x1634) {row=m->actions[command&63];count=3;data=0x1640;}
 else if(address==0x1654) {row=m->map+(command&255);count=1;data=0x1658;}
 else if(address==0x164c) {row=m->dbuf+(command&7);count=1;data=0x1650;}
 else if(address==0x1574) {row=m->membership[command&63];count=2;data=0x157c;}
 else {assert(address==0x1420);row=m->elp[command&63];count=2;data=0x1428;}
 assert((command&~(0xc0000000U|255U))==0);
 m->reg[address/4]=command;
 if(fail && !m->applied)return -ETIMEDOUT;
 for(unsigned int i=0;i<count;i++) {
  if(command&0x40000000U)row[i]=read_reg(m,data-i*4);
  else m->reg[(data-i*4)/4]=row[i]^(m->corrupt?1U:0U);
 }
 if(fail)return -ETIMEDOUT;
 m->reg[address/4]=command&~0x80000000U;
 return 0;
}

static void owner_lifecycle(void)
{
 static struct model m;
 static struct rtl_fe_mc_owner owner;
 struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 struct rtl_fe_mc_replica replicas[3]={{.destination=6,.tags=1,.tag={{0x8100,4095}}},
  {.destination=1},{.destination=2}};
 unsigned int old,next,index,calls;

 for(unsigned int i=0;i<4;i++)m.reg[(0x1704+i*4)/4]=0x8100;
 m.groups[200][1]=1;
 assert(rtl_fe_mc_stage(&io,&owner,replicas,2,&index)==-EBUSY && !index && !m.writes);
 m.groups[200][1]=0;
 assert(!rtl_fe_mc_stage(&io,&owner,replicas,2,&old) && old==1);
 assert(m.groups[old][0]==3 && owner.actions[0].users==1);
 assert(!rtl_fe_mc_reference(&io,&owner,old));
 assert(!rtl_fe_mc_stage(&io,&owner,replicas,3,&next) && next==2);
 assert(m.groups[next][0]==7 && m.groups[old][0]==3 && owner.actions[0].users==2);
 assert(!rtl_fe_mc_reference(&io,&owner,next));
 m.fail=m.calls+1;
 assert(rtl_fe_mc_withdraw(&io,&owner,old)==-ETIMEDOUT);
 assert(owner.groups[old].state==RTL_FE_MC_REFERENCED && owner.actions[0].users==2);
 assert(owner.groups[old].withdrawing && rtl_fe_mc_reference(&io,&owner,old)==-EINVAL);
 m.reg[0xaa64/4]=0;m.fail=0;
 assert(!rtl_fe_mc_withdraw(&io,&owner,old));
 assert(!m.groups[old][0] && owner.actions[0].users==1 && m.actions[0][1]);
 assert(owner.groups[old].state==RTL_FE_MC_RETIRED);
 assert(!rtl_fe_mc_withdraw(&io,&owner,next));
 assert(!owner.actions[0].users && owner.actions[0].exposed);
 assert(!rtl_fe_mc_stage(&io,&owner,replicas,2,&index) && index==3);
 assert(m.groups[index][0]==3); /* Share identical immutable actions. */
 assert(!rtl_fe_mc_withdraw(&io,&owner,index));
 assert(owner.groups[index].state==RTL_FE_MC_FREE);
 replicas[1]=replicas[0];calls=m.calls;
 assert(rtl_fe_mc_stage(&io,&owner,replicas,2,&index)==-EEXIST && !index && m.calls==calls);
 replicas[1].destination=1;replicas[1].tags=0;
 /* Fault every command in a fresh complete stage, including applied-write
  * timeout. The caller can always resume abort without orphaning references. */
 for(unsigned int fail=1;fail<=12;fail++)for(unsigned int applied=0;applied<2;applied++) {
  memset(&m,0,sizeof(m));memset(&owner,0,sizeof(owner));owner.initialized=1;
  for(unsigned int i=0;i<4;i++)m.reg[(0x1704+i*4)/4]=0x8100;
  m.fail=fail;m.applied=applied;
  int ret=rtl_fe_mc_stage(&io,&owner,replicas,2,&index);
  assert(ret==-ETIMEDOUT || !ret);
  m.reg[0xaa64/4]=m.reg[0x1634/4]=0;m.fail=0;
  if(index)assert(!rtl_fe_mc_withdraw(&io,&owner,index));
  assert(!m.groups[1][0]);
  for(unsigned int a=0;a<64;a++)assert(!owner.actions[a].users && !m.actions[a][0] && !m.actions[a][1]);
 }
 /* An action-restore failure retains only its unfinished owner; retry must
  * not double-decrement already released shared references. */
 assert(!rtl_fe_mc_stage(&io,&owner,replicas,2,&index));
 m.fail=m.calls+5;m.applied=true;
 assert(rtl_fe_mc_withdraw(&io,&owner,index)==-ETIMEDOUT);
 assert(!owner.actions[0].users && owner.actions[1].users==1);
 m.reg[0x1634/4]=0;m.fail=0;
 assert(!rtl_fe_mc_withdraw(&io,&owner,index));
 assert(!owner.actions[1].users && owner.groups[index].state==RTL_FE_MC_FREE);
 /* Exhaustion must never make an old in-flight NI index refer to new data. */
 for(unsigned int g=1;g<256;g++) {
  assert(!rtl_fe_mc_stage(&io,&owner,replicas,2,&index) && index==g);
  assert(!rtl_fe_mc_reference(&io,&owner,index));
  assert(!rtl_fe_mc_withdraw(&io,&owner,index));
 }
 assert(rtl_fe_mc_stage(&io,&owner,replicas,2,&index)==-ENOSPC && !index);
 puts("Multicast owner: complete stage, shared refs, partial abort, quarantine and exhaustion passed");
}

static void route_lifecycle(void)
{
 static struct model m,saved;
 struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 struct rtl_fe_mc_route route={0};
 unsigned int calls;
 for(unsigned int i=0;i<7;i++)m.reg[rtl_fe_mc_route_regs[i]/4]=0xa55a0080U+i;
 m.reg[0xaa60/4]=0x2f0ff;
 m.reg[0xa1c0/4]=0x24009b;
 m.map[27]=15;m.map[155]=9;
 m.map[1]=m.map[129]=1;m.map[70]=m.map[198]=8;
 m.elp[1][0]=0xaaa8;m.elp[1][1]=0x5500;
 m.elp[6][0]=0x1230;m.elp[6][1]=0x4500;
 m.dbuf[0]=0x2301;m.dbuf[1]=0xffff0000;
 saved=m;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(!rtl_fe_mc_route_port(&io,&route,1));
 assert(!rtl_fe_mc_route_port(&io,&route,6));
 assert(m.dbuf[0]==0x2301 && m.dbuf[1]==0xffff001b);
 assert(m.map[27]==11 && m.map[155]==11);
 assert(m.elp[6][0]==0x1233 && m.elp[6][1]==0x4500);
 assert((m.reg[0x1600/4]&0x100)==0 && (m.reg[0x2300/4]&(1U<<11)));
 assert((m.reg[0x1600/4]&(1U<<31)) && m.reg[0xaa60/4]==0x2f017);
 assert(!rtl_fe_mc_route_restore(&io,&route));
 assert(!route.owned && !memcmp(m.map,saved.map,sizeof(m.map)) && !memcmp(m.dbuf,saved.dbuf,sizeof(m.dbuf)));
 assert(!memcmp(m.elp,saved.elp,sizeof(m.elp)));
 for(unsigned int i=0;i<7;i++)assert(m.reg[rtl_fe_mc_route_regs[i]/4]==saved.reg[rtl_fe_mc_route_regs[i]/4]);
 calls=m.calls;
 for(unsigned int fail=1;fail<=calls;fail++)for(unsigned int applied=0;applied<2;applied++) {
  m=saved;route=(struct rtl_fe_mc_route){0};m.fail=fail;m.applied=applied;
  int ret=rtl_fe_mc_route_prepare(&io,&route);
  if(!ret)ret=rtl_fe_mc_route_port(&io,&route,1);
  if(!ret)ret=rtl_fe_mc_route_port(&io,&route,6);
  if(!ret)ret=rtl_fe_mc_route_restore(&io,&route);
  assert(!ret || ret==-ETIMEDOUT);
  m.fail=0;m.reg[0x1654/4]=m.reg[0x164c/4]=m.reg[0x1420/4]=0;
  assert(!rtl_fe_mc_route_restore(&io,&route) && !route.owned);
  assert(!memcmp(m.map,saved.map,sizeof(m.map)) && !memcmp(m.dbuf,saved.dbuf,sizeof(m.dbuf)));
  assert(!memcmp(m.elp,saved.elp,sizeof(m.elp)));
  for(unsigned int i=0;i<7;i++)assert(m.reg[rtl_fe_mc_route_regs[i]/4]==saved.reg[rtl_fe_mc_route_regs[i]/4]);
 }
 m=saved;route=(struct rtl_fe_mc_route){0};
 for(unsigned int i=0;i<8;i++)m.dbuf[i]=0x2301;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(rtl_fe_mc_route_port(&io,&route,6)==-ENOSPC && !route.dbuf_owned);
 assert(!rtl_fe_mc_route_restore(&io,&route));
 for(unsigned int i=0;i<8;i++)assert(m.dbuf[i]==0x2301);
 /* A live egress policy is not ours to disable. */
 m=saved;route=(struct rtl_fe_mc_route){0};m.elp[6][0]|=1U<<18;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(rtl_fe_mc_route_port(&io,&route,6)==-EOPNOTSUPP && !(route.ports&(1U<<6)));
 assert(m.elp[6][0]==(saved.elp[6][0]|1U<<18));
 assert(!rtl_fe_mc_route_restore(&io,&route));
 /* A preexisting isolation policy must permit the complete path. */
 const struct rtl_fe_mc_replica replicas[]={{.destination=1},{.destination=6}};
 m.reg[0x160c/4]=1;
 assert(rtl_fe_mc_route_members(&io,0,replicas,2)==-EOPNOTSUPP);
 m.membership[0][0]=(1U<<27)|(1U<<1)|(1U<<6);
 assert(rtl_fe_mc_route_members(&io,0,replicas,2)==-EOPNOTSUPP);
 m.membership[27][0]=(1U<<1)|(1U<<6);
 assert(!rtl_fe_mc_route_members(&io,0,replicas,2));
 m.fail=m.calls+1;
 assert(rtl_fe_mc_route_members(&io,0,replicas,2)==-ETIMEDOUT);
 m=saved;route=(struct rtl_fe_mc_route){0};m.reg[0xaa60/4]|=1U<<18;
 assert(rtl_fe_mc_route_prepare(&io,&route)==-EUCLEAN && !route.owned);
 m=saved;route=(struct rtl_fe_mc_route){0};m.reg[0xa1c0/4]^=1U<<6;
 assert(rtl_fe_mc_route_prepare(&io,&route)==-EUCLEAN && !route.owned);
 puts("Multicast route: connector, P6 deep-buffer match, every indirect fault and restoration passed");
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
 owner_lifecycle();
 route_lifecycle();
 return 0;
}
