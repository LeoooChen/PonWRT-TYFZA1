/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-mcast-profile.h"

struct model {
 unsigned int reg[0x4000/4],keys[256][11],fib[1040][7],masks[128][4];
 unsigned int commands,fail,writes;
 bool applied;
};
static unsigned int read_reg(void *p,unsigned int reg){return ((struct model *)p)->reg[reg/4];}
static void write_reg(void *p,unsigned int reg,unsigned int value)
{
 struct model *m=p;m->reg[reg/4]=value;m->writes++;
}
static int indirect(void *p,unsigned int reg,unsigned int command)
{
 struct model *m=p;
 unsigned int *row,count,data;
 bool fail=++m->commands==m->fail;
 assert(!(m->reg[reg/4]&0x80000000U));
 if(reg==0x3380){row=m->keys[command&255];count=11;data=0x33ac;}
 else if(reg==0x33b0){row=m->fib[command&2047];count=7;data=0x33cc;}
 else {assert(reg==0x3910);row=m->masks[command&127];count=4;data=0x3920;}
 m->reg[reg/4]=command;
 if(fail&&!m->applied)return -ETIMEDOUT;
 if(reg==0x3380 && command&0x40000000U && read_reg(m,0x3384)&(15U<<27)) {
  unsigned int mask[8],key[11],fib[7];
  rtl_fe_mc_ip_mask(mask);rtl_fe_mc_classifier_key(key);
  rtl_fe_classify_hash(fib,1,0);rtl_fe_bits_set(fib,208,1,0);
  assert(!memcmp(m->fib[512],fib,sizeof(fib)));
  assert(!memcmp(m->masks[55],mask,16)&&!memcmp(m->masks[119],mask+4,16));
  assert(read_reg(m,0x372c)==3 && read_reg(m,0x3730)==61 && read_reg(m,0x3734)==(59|1U<<8));
  assert(read_reg(m,0x3738)==(55|2U<<8) && read_reg(m,0x3750)==(RTL_FE_ACTION_GROUPS|3U<<25));
  for(unsigned int i=0;i<11;i++)assert(read_reg(m,data-4*i)==key[i]);
 }
 for(unsigned int i=0;i<count;i++) {
  if(command&0x40000000U)row[i]=read_reg(m,data-4*i);
  else m->reg[(data-4*i)/4]=row[i];
 }
 if(fail)return -ETIMEDOUT;
 m->reg[reg/4]=command&~0x80000000U;
 return 0;
}
static void unchanged(struct model *m,const struct model *saved)
{
 assert(!memcmp(m->keys,saved->keys,sizeof(m->keys)));
 assert(!memcmp(m->fib,saved->fib,sizeof(m->fib)));
 assert(!memcmp(m->masks,saved->masks,sizeof(m->masks)));
 for(unsigned int r=0x3700;r<0x3834;r+=4)assert(m->reg[r/4]==saved->reg[r/4]);
}
int main(void)
{
 static struct model m,saved;
 struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 struct rtl_fe_mc_profile profile={0};
 unsigned int commands;
 m.reg[0x3700/4]=8;
 for(unsigned int i=0;i<8;i++)m.reg[(0x3704+i*4)/4]=(63-i)|(i<<8);
 for(unsigned int i=0;i<11;i++)m.keys[128][i]=0x12121212+i;
 m.keys[128][10]&=~(15U<<27);
 for(unsigned int i=0;i<7;i++)m.fib[512][i]=0x43434343+i;
 for(unsigned int i=0;i<128;i++)for(unsigned int j=0;j<4;j++)m.masks[i][j]=0x10000*i+j;
 for(unsigned int i=0;i<11;i++)m.reg[(0x372c+i*4)/4]=0xabcd0000+i*16;
 saved=m;
 assert(!rtl_fe_mc_profile_prepare(&io,&profile)&&profile.ready&&profile.owned);
 commands=m.commands;
 assert(!rtl_fe_mc_profile_prepare(&io,&profile)&&m.commands==commands);
 assert(!rtl_fe_mc_profile_restore(&io,&profile)&&!profile.owned);
 unchanged(&m,&saved);commands=m.commands;
 for(unsigned int fail=1;fail<=commands;fail++)for(unsigned int applied=0;applied<2;applied++) {
  m=saved;profile=(struct rtl_fe_mc_profile){0};m.fail=fail;m.applied=applied;
  int ret=rtl_fe_mc_profile_prepare(&io,&profile);
  if(!ret)ret=rtl_fe_mc_profile_restore(&io,&profile);
  assert(ret==-ETIMEDOUT);
  m.fail=0;m.reg[0x3380/4]=m.reg[0x33b0/4]=m.reg[0x3910/4]=0;
  assert(!rtl_fe_mc_profile_restore(&io,&profile)&&!profile.owned);
  unchanged(&m,&saved);
 }
 for(unsigned int scenario=0;scenario<5;scenario++) {
  m=saved;profile=(struct rtl_fe_mc_profile){0};
  if(scenario==0)m.reg[0x372c/4]|=1;
  if(scenario==1)m.keys[200][10]|=1U<<27;
  if(scenario==2)m.reg[0x3704/4]=55;
  if(scenario==3)m.reg[0x3700/4]=9;
  if(scenario==4)m.keys[7][10]|=1U<<27;
  assert(rtl_fe_mc_profile_prepare(&io,&profile)==(scenario==3?-EUCLEAN:-EBUSY));
  assert(!profile.owned&&!m.writes);
 }
 printf("Multicast IP profile: %u command faults/applied timeouts, publication ordering and foreign-owner refusal passed\n",commands*2);
}
