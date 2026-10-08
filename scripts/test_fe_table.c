/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-pipeline.h"

struct model {
 unsigned int reg[0x4000/4], table[64][8], ages[128][4], counters[64][7], calls, fail;
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
 assert((address==0x3910 || address==0x3928 || address==0x35c8) && (value&0x80000000U));
 if(address==0x35c8) {
  assert(!(value&0x40000000U));
  if(++m->calls==m->fail)return -1;
  for(unsigned int i=0;i<7;i++)m->reg[(0x35e4-i*4)/4]=m->counters[entry][i];
  return 0;
 }
 if(address==0x3928){
  unsigned int row=value&127;
  if(++m->calls==m->fail)return -1;
  for(unsigned int i=0;i<4;i++){
   if(value&0x40000000U)m->ages[row][i]=m->reg[(0x3938-i*4)/4];
   else m->reg[(0x3938-i*4)/4]=m->ages[row][i];
  }
  return 0;
 }
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
 /* Every age slot updates exactly one nibble and keeps all other owners. */
 for(unsigned int row=0;row<128;row++)
  for(unsigned int word=0;word<4;word++)m.ages[row][word]=0x54321076U;
 for(unsigned int index=0;index<RTL_FE_HASH_ENTRIES;index++){
  unsigned int row=index>>5,slot=index&31,old=m.ages[row][slot>>3],shift=(slot&7)*4;
  unsigned int age;
  assert(!rtl_fe_main_age_read(&io,index,&age) && age==((old>>shift)&15));
  assert(!rtl_fe_main_age(&io,index,7));
  assert(m.ages[row][slot>>3]==((old&~(15U<<shift))|(7U<<shift)));
  assert(!rtl_fe_main_age(&io,index,(old>>shift)&15));
  assert(m.ages[row][slot>>3]==old);
 }
 /* Actual DNS board MIB fixture:three71-byte upstream frames and three
  * 425-byte replies; all seven MIB words must retain their hardware order. */
 unsigned int counters[7];
 memcpy(m.counters[31],(unsigned int[]){213,0,3,0,0,0,0},sizeof(counters));
 memcpy(m.counters[30],(unsigned int[]){1275,0,3,0,0,0,0},sizeof(counters));
 assert(!rtl_fe_tx_counter_read(&io,31,counters) && counters[0]==213 && counters[2]==3);
 assert(!rtl_fe_tx_counter_read(&io,30,counters) && counters[0]==1275 && counters[2]==3);
 m.fail=m.calls+1;
 memset(counters,0xa5,sizeof(counters));
 assert(rtl_fe_tx_counter_read(&io,31,counters)==-1 && counters[0]==0xa5a5a5a5);
 m.fail=0;
 rtl_fe_dma_tables(&io,0x03400000,2);
 assert(read_reg(&m,0x3838)==2 && read_reg(&m,0x383c)==0x03400000);
 assert(read_reg(&m,0x3844)==0x03400000+RTL_FE_ACTION_OFFSET);
 assert(read_reg(&m,0x3850)==0x03400000+RTL_FE_OVERFLOW_OFFSET);
 assert(read_reg(&m,0x385c)==0x03400000+RTL_FE_DEFAULT_OFFSET);
 assert(RTL_FE_ACTION_OFFSET+4096*32<=RTL_FE_OVERFLOW_OFFSET);
 assert(RTL_FE_OVERFLOW_OFFSET+64*32<=RTL_FE_DEFAULT_OFFSET);
 assert(RTL_FE_DEFAULT_OFFSET+32*16<=RTL_FE_MEMORY_BYTES);
 assert(read_reg(&m,0x3834)==0x6f && read_reg(&m,0x38a0)==0x10301);
 unsigned int miss[4];rtl_fe_cpu_miss(miss);
 assert(((miss[1]>>22)&3)==3); /* Both keep-original enable/value. */
 assert(((miss[0]>>30)|(miss[1]<<2))%1024==16); /* Miss goes toCPU16. */
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
 unsigned int fib[7],lpb[3],fdb[4];
 rtl_fe_classify_hash(fib,3,7);
 assert((fib[6]&0xff00U)==0x3800U); /* valid+profile3 in bits203..207 */
 assert(((fib[6]>>17)&63)==7 && (fib[6]&(1U<<16)));
 rtl_fe_ingress_lpb(lpb,0,16);
 assert(((lpb[2]>>4)&63)==16 && ((lpb[1]>>6)&15)==15);
 rtl_fe_fdb_key(fdb,(const unsigned char[]){2,0x77,0x66,0x55,0x44,0x33});
 assert(fdb[0]==0x003ffa00U);
 assert(fdb[1]==0x21980000U && fdb[2]==0x13bb32aaU && fdb[3]==0);
 unsigned int ttl_mask[8];rtl_fe_ipv4_mask(ttl_mask);
 assert(!(ttl_mask[1]&(3U<<23))); /* TTL is the ignore selector, not range3. */
 unsigned int c32;unsigned short c16;
 rtl_fe_key_set(key.words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(key.words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_crc(key.words,&c32,&c16);
 printf("Native FE:64 two-bank mask slots/restore, transaction failures, bounded action/key and CRC passed (%08x/%04x)\n",c32,c16);
}
