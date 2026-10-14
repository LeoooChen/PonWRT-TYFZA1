/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-table.h"

struct model {unsigned int tuple, index, header[33], mask, calls;int fail;};
static unsigned int read_reg(void *context, unsigned int reg)
{
 struct model *m=context;
 if(reg==0x3704)return m->tuple;
 assert(reg==0x38dc);
 return m->index++==33?0x96170000U|m->mask:0xc000U|m->mask;
}
static void write_reg(void *context, unsigned int reg, unsigned int value)
{
 struct model *m=context;
 if(reg==0x3704)m->tuple=value;
 else if(reg==0x38d8)m->index=value;
 else {assert(reg==0x38dc && m->index<33);m->header[m->index++]=value;}
}
static int command(void *context, unsigned int reg, unsigned int value)
{
 struct model *m=context;
 assert(reg==0x38e0 && value==1 && m->index==33);
 m->calls++;m->mask=m->tuple&63;
 /* Hardware follows tuple0, not the pointer written to SWO input32. */
 return m->fail?-ETIMEDOUT:0;
}
int main(void)
{
 struct model m={.tuple=0x1234013f};
 const struct rtl_dq_io io={&m,read_reg,write_reg,command};
 unsigned int header[32],crc32;unsigned short crc16;
 for(unsigned int i=0;i<32;i++)header[i]=0xa5000000+i;
 for(unsigned int mask=0;mask<64;mask++){
  assert(!rtl_fe_crc_hardware(&io,header,mask,&crc32,&crc16));
  assert(m.tuple==0x1234013f && m.mask==mask && m.header[32]==mask);
  assert(!memcmp(m.header,header,sizeof(header)));
  assert(crc32==(0x96170000U|mask) && crc16==(0xc000U|mask));
 }
 m.fail=1;crc32=0xaabbccdd;crc16=0x9876;
 assert(rtl_fe_crc_hardware(&io,header,12,&crc32,&crc16)==-ETIMEDOUT);
 assert(m.tuple==0x1234013f && crc32==0xaabbccdd && crc16==0x9876);
 puts("SWO: all64 masks selected through tuple0; profile restored on success/timeout; outputs unchanged on timeout");
}
