/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-table.h"

struct model {
 unsigned int reg[0x4000/4], table[64][8], calls, fail;
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
 m->reg[address/4]=value;
}
static int access_reg(void *context,unsigned int address,unsigned int value)
{
 struct model *m=context;
 unsigned int entry=value&63,bank=(value>>6)&1;
 assert(address==0x3910 && (value&0x80000000U));
 if(++m->calls==m->fail)return -1;
 for(unsigned int word=0;word<4;word++){
  unsigned int reg=(0x3920-word*4)/4;
  if(value&0x40000000U)m->table[entry][bank*4+word]=m->reg[reg];
  else m->reg[reg]=m->table[entry][bank*4+word];
 }
 return 0;
}
int main(void)
{
 struct model m={};
 const struct rtl_dq_io io={&m,read_reg,write_reg,access_reg};
 unsigned int mask[8],output[8],saved[8];
 rtl_fe_ipv4_mask(mask);
 assert(mask[7]==0 && !(mask[0]&0xfffffc00U));
 /* A write/read uses both bank addresses and all eight words. */
 for(unsigned int entry=0;entry<64;entry++){
  for(unsigned int word=0;word<8;word++)m.table[entry][word]=saved[word]=0xa55a0000U+entry*8+word;
  assert(!rtl_fe_mask_write(&io,entry,mask));
  assert(!rtl_fe_mask_read(&io,entry,output) && !memcmp(mask,output,sizeof(mask)));
  assert(!rtl_fe_mask_write(&io,entry,saved));
  assert(!rtl_fe_mask_read(&io,entry,output) && !memcmp(saved,output,sizeof(saved)));
 }
 for(unsigned int fail=1;fail<=2;fail++){
  memset(&m,0,sizeof(m));m.fail=fail;
  assert(rtl_fe_mask_write(&io,63,mask)==-1);
  memset(&m,0,sizeof(m));m.fail=fail;
  assert(rtl_fe_mask_read(&io,63,output)==-1);
 }
 struct {unsigned int before,words[8],after;} guarded={.before=0x12345678,.after=0xabcdef01};
 struct rtl_fe_ipv4_action action={.address=0xc0a8471c,.port=62000,.destination=32,
  .mac_da_index=8191,.ingress_interface=63,.egress_interface=62,.cos=7,.translate=1,.deep_queue=1};
 rtl_fe_ipv4_action(guarded.words,&action);
 assert(guarded.before==0x12345678 && guarded.after==0xabcdef01 && guarded.words[7]==0);
 struct {unsigned int before,words[23],after;} key={.before=0x12345678,.after=0xabcdef01};
 struct rtl_fe_ipv4_key input={.source=0xc0a801aa,.destination=0x01010101,.source_port=54321,
  .destination_port=443,.llid=31,.protocol=6,.ingress=7,.profile=3,.mask=63};
 rtl_fe_ipv4_key(key.words,&input);
 assert(key.before==0x12345678 && key.after==0xabcdef01);
 unsigned int c32;unsigned short c16;
 rtl_fe_key_set(key.words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(key.words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_crc(key.words,&c32,&c16);
 printf("Native FE:64 two-bank mask slots/restore, transaction failures, bounded action/key and CRC passed (%08x/%04x)\n",c32,c16);
}
