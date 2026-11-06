/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_MCAST_PROFILE_H
#define RTL9617C_FE_MCAST_PROFILE_H
#include <linux/errno.h>
#include "rtl9617c-fe-pipeline.h"

#define RTL_FE_MC_IP_MASK 55
#define RTL_FE_MC_LEARNED_MASK 54
#define RTL_FE_MC_PROFILE 1
#define RTL_FE_MC_CLASSIFIER 128

/* Separate from profile0's eight unicast/bridge/PPPoE templates. WAN0 and
 * LAN128 classify multicast (except broadcast), preserving source and GEM.
 * Its default remains CPU16. MAC-only explicit multicast rules retain their
 * templates alongside the full source/group-IP key used by snooping. */
struct rtl_fe_mc_profile {
 unsigned int key[2][11],fib[2][7],mask[2][8],registers[11];
 unsigned char owned,ready;
};

static inline void rtl_fe_mc_ip_mask(unsigned int words[8])
{
 rtl_fe_bridge_mask(words);
 rtl_fe_bits_set(words,58,9,128);
 rtl_fe_bits_set(words,67,9,128);
 rtl_fe_bits_set(words,77,1,0); /* IP protocol: reports/queries must miss UDP. */
 rtl_fe_bits_set(words,86,2,0); /* IP version/valid. */
 rtl_fe_bits_set(words,44,1,0); /* IP checksum error. */
 rtl_fe_bits_set(words,49,3,0); /* IPv6 extension headers. */
 rtl_fe_bits_set(words,52,1,0); /* Fragment. */
 rtl_fe_bits_set(words,54,1,0); /* Options. */
 rtl_fe_bits_set(words,92,2,0); /* Exact inner PCP/DEI. */
 rtl_fe_bits_set(words,96,2,0); /* Exact outer PCP/DEI. */
}

static inline void rtl_fe_mc_ip_key(unsigned int words[23],const unsigned int *source,
 const unsigned int *destination,unsigned int ipv6,unsigned int protocol)
{
 unsigned int i;
 for(i=0;i<4;i++) {
  rtl_fe_bits_set(words,123+i*32,32,i&&!ipv6?0:destination[ipv6?3-i:0]);
  rtl_fe_bits_set(words,251+i*32,32,i&&!ipv6?0:source[ipv6?3-i:0]);
 }
 rtl_fe_key_set(words,RTL_FE_KEY_IP_VER,ipv6);
 rtl_fe_key_set(words,RTL_FE_KEY_IP_VLD,1);
 rtl_fe_key_set(words,RTL_FE_KEY_IP_PROTOCOL,protocol);
}

static inline void rtl_fe_mc_learned_mask(unsigned int words[8])
{
 rtl_fe_mc_ip_mask(words);
 rtl_fe_bits_set(words,10,17,0);
 rtl_fe_bits_set(words,27,17,0);
}

static inline void rtl_fe_mc_classifier_key(unsigned int words[11])
{
 unsigned int i;
 for(i=0;i<11;i++)words[i]=0;
 /* Original __l3_cls_key_entry_update, IF_ID slot0: all83 bits ignored
  * except MAC multicast=1 and broadcast=0. Unused slots stay invalid. */
 words[0]=~0U;words[1]=0xcbffffffU;words[2]=0x7ffffU;
 words[10]=1U<<27;
}

static inline int rtl_fe_mc_profile_read(const struct rtl_dq_io *io,unsigned int reg,
 unsigned int data,unsigned int index,unsigned int *words,unsigned int count)
{
 unsigned int i;
 int ret;
 if(io->read(io->context,reg)&0x80000000U)return -EBUSY;
 ret=io->indirect(io->context,reg,0x80000000U|index);
 if(ret)return ret;
 for(i=0;i<count;i++)words[i]=io->read(io->context,data-4*i);
 return 0;
}
static inline int rtl_fe_mc_profile_write(const struct rtl_dq_io *io,unsigned int reg,
 unsigned int data,unsigned int index,const unsigned int *words,unsigned int count)
{
 unsigned int actual[11],i;
 int ret;
 if(io->read(io->context,reg)&0x80000000U)return -EBUSY;
 for(i=0;i<count;i++)io->write(io->context,data-4*i,words[i]);
 ret=io->indirect(io->context,reg,0xc0000000U|index);
 if(ret)return ret;
 ret=rtl_fe_mc_profile_read(io,reg,data,index,actual,count);
 if(ret)return ret;
 for(i=0;i<count;i++)if(actual[i]!=words[i])return -EIO;
 return 0;
}

static inline int rtl_fe_mc_profile_mask(const struct rtl_dq_io *io,unsigned int index,const unsigned int words[8])
{
 unsigned int bank;
 int ret;
 for(bank=0;bank<2;bank++) {
  ret=rtl_fe_mc_profile_write(io,0x3910,0x3920,index|(bank<<6),words+bank*4,4);
  if(ret)return ret;
 }
 return 0;
}

/* No published multicast Hash may remain when restoring this owner. Invalid
 * key readback comes first; a lost completion keeps the original snapshots. */
static inline int rtl_fe_mc_profile_restore(const struct rtl_dq_io *io,struct rtl_fe_mc_profile *p)
{
 unsigned int i,side,words[11];
 int ret;
 if(!p->owned)return 0;
 p->ready=0;
 for(side=0;side<2;side++) {
  for(i=0;i<11;i++)words[i]=p->key[side][i];
  words[10]&=~(15U<<27);
  ret=rtl_fe_mc_profile_write(io,0x3380,0x33ac,side*RTL_FE_MC_CLASSIFIER,words,11);
  if(ret)return ret;
 }
 for(i=0;i<11;i++) {
  io->write(io->context,0x372c+i*4,p->registers[i]);
  if(io->read(io->context,0x372c+i*4)!=p->registers[i])return -EIO;
 }
 for(i=0;i<2;i++) {
  ret=rtl_fe_mc_profile_mask(io,RTL_FE_MC_IP_MASK-i,p->mask[i]);
  if(ret)return ret;
 }
 for(side=0;side<2;side++) {
  ret=rtl_fe_mc_profile_write(io,0x33b0,0x33cc,side*RTL_FE_MC_CLASSIFIER*4,p->fib[side],7);
  if(ret)return ret;
  ret=rtl_fe_mc_profile_write(io,0x3380,0x33ac,side*RTL_FE_MC_CLASSIFIER,p->key[side],11);
  if(ret)return ret;
 }
 p->owned=0;
 return 0;
}

static inline int rtl_fe_mc_profile_prepare(const struct rtl_dq_io *io,struct rtl_fe_mc_profile *p)
{
 unsigned int words[11],i,j,side,value;
 int ret;
 if(p->ready)return 0;
 if(!p->owned) {
  /* No silent takeover of another profile or its classifier priority. */
  if(io->read(io->context,0x372c)&15)return -EBUSY;
  for(i=0;i<7;i++) {
   unsigned int count=io->read(io->context,0x3700+i*44)&15;
   if(count>8)return -EUCLEAN;
   for(j=0;j<count;j++) {
    unsigned int mask=io->read(io->context,0x3704+i*44+j*4)&63;
    if(mask==RTL_FE_MC_IP_MASK || mask==RTL_FE_MC_LEARNED_MASK)return -EBUSY;
   }
  }
  /* Any inherited classifier could select profile1 too. Do not make
   * its inactive profile live with our templates. Own WAN0 and LAN128. */
  for(i=0;i<256;i++) {
   ret=rtl_fe_mc_profile_read(io,0x3380,0x33ac,i,words,11);
   if(ret)return ret;
   if(words[10]&(15U<<27))return -EBUSY;
   if(i==0 || i==RTL_FE_MC_CLASSIFIER)for(j=0;j<11;j++)p->key[i/RTL_FE_MC_CLASSIFIER][j]=words[j];
  }
  for(side=0;side<2;side++) {
   ret=rtl_fe_mc_profile_read(io,0x33b0,0x33cc,side*RTL_FE_MC_CLASSIFIER*4,p->fib[side],7);
   if(ret)return ret;
  }
  for(i=0;i<4;i++) {
   ret=rtl_fe_mc_profile_read(io,0x3910,0x3920,(RTL_FE_MC_IP_MASK-i/2)|((i%2)<<6),p->mask[i/2]+(i%2)*4,4);
   if(ret)return ret;
  }
  for(i=0;i<11;i++)p->registers[i]=io->read(io->context,0x372c+i*4);
  p->owned=1;
 }
 rtl_fe_mc_ip_mask(words);
 for(i=0;i<2;i++) {
  if(i)rtl_fe_mc_learned_mask(words);
  ret=rtl_fe_mc_profile_mask(io,RTL_FE_MC_IP_MASK-i,words);
  if(ret)return ret;
 }
 /* Explicit MAC TC rules precede automatically learned IP groups. */
 for(i=0;i<11;i++) {
  value=i==0?3:i==1?61:i==2?(59|1U<<8):i==3?(RTL_FE_MC_IP_MASK|2U<<8):
   i==4?(RTL_FE_MC_LEARNED_MASK|3U<<8):i==9?(RTL_FE_ACTION_GROUPS|3U<<25):0;
  io->write(io->context,0x372c+i*4,value);
  if(io->read(io->context,0x372c+i*4)!=value)return -EIO;
 }
 rtl_fe_classify_hash(words,RTL_FE_MC_PROFILE,0);
 rtl_fe_bits_set(words,208,1,0);
 for(side=0;side<2;side++) {
  ret=rtl_fe_mc_profile_write(io,0x33b0,0x33cc,side*RTL_FE_MC_CLASSIFIER*4,words,7);
  if(ret)return ret;
 }
 rtl_fe_mc_classifier_key(words);
 for(side=0;side<2;side++) {
  ret=rtl_fe_mc_profile_write(io,0x3380,0x33ac,side*RTL_FE_MC_CLASSIFIER,words,11);
  if(ret)return ret;
 }
 p->ready=1;
 return 0;
}
#endif
