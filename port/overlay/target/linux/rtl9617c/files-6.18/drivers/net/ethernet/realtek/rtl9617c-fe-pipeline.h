/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_PIPELINE_H
#define RTL9617C_FE_PIPELINE_H
#include "rtl9617c-fe-table.h"

struct rtl_fe_cam_protocol {
 unsigned int saved[5];
 unsigned char bank,index;
 unsigned char created;
};

/* Factory aal_cam_init installs EtherType8864 and PPP21/57. Parser keys use
 * the actual CAM index plus one, independently of the packet editor's values. */
static inline int rtl_fe_cam_protocol_get(const struct rtl_dq_io *io,
 struct rtl_fe_cam_protocol *binding,unsigned int bank,unsigned int protocol)
{
 unsigned int count=bank==2?63:15,index,word,words[5],free_index=count;
 unsigned int width=bank==2?5:3;
 int ret;

 binding->bank=bank;binding->created=0;
 for(index=0;index<count;index++) {
  ret=io->indirect(io->context,0x3200,0x80000000U|(bank<<16)|index);
  if(ret)return ret;
  for(word=0;word<width;word++)words[word]=io->read(io->context,0x3214-word*4);
  if(words[0]&(1U<<16)) {
   if((words[0]&65535)==protocol){binding->index=index;return index+1;}
  } else if(free_index==count) {
   free_index=index;
   for(word=0;word<width;word++)binding->saved[word]=words[word];
  }
 }
 if(free_index==count)return -ENOSPC;
 binding->index=free_index;binding->created=1;
 for(word=0;word<width;word++)io->write(io->context,0x3214-word*4,word?0:protocol|(1U<<16));
 ret=io->indirect(io->context,0x3200,0xc0000000U|(bank<<16)|free_index);
 if(ret)return ret;
 return free_index+1;
}

static inline int rtl_fe_cam_protocol_put(const struct rtl_dq_io *io,struct rtl_fe_cam_protocol *binding)
{
 unsigned int word;
 int ret;

 if(!binding->created)return 0;
 for(word=0;word<(binding->bank==2?5U:3U);word++)io->write(io->context,0x3214-word*4,binding->saved[word]);
 ret=io->indirect(io->context,0x3200,0xc0000000U|(binding->bank<<16)|binding->index);
 if(!ret)binding->created=0;
 return ret;
}

/* Classifier mode0, default result: select main Hash profile while leaving
 * destination and bytes to the matched action. Source can be kept or set
 * explicitly for a controlled pipeline test. Format from aal_l3_cls.h. */
static inline void rtl_fe_classify_hash(unsigned int words[7],unsigned int profile,unsigned int source)
{
 unsigned int i;
 for(i=0;i<7;i++)words[i]=0;
 rtl_fe_bits_set(words,203,1,1); /* t2_ctrl_vld */
 rtl_fe_bits_set(words,204,4,profile); /* t2_ctrl */
 rtl_fe_bits_set(words,186,1,1); /* stage2_ctrl_vld */
 rtl_fe_bits_set(words,187,1,0); /* stage2_ctrl */
 rtl_fe_bits_set(words,156,1,1); /* permit */
 rtl_fe_bits_set(words,208,1,1); /* lspid_vld */
 rtl_fe_bits_set(words,209,6,source); /* lspid */
}

/* LPB profile used by the internal LAN-to-L3 connector. No VLAN/packet
 * rewriting occurs here; classifier and flow actions decide forwarding. */
static inline void rtl_fe_ingress_lpb(unsigned int words[3],unsigned int source,unsigned int destination)
{
 unsigned int i;
 for(i=0;i<3;i++)words[i]=0;
 rtl_fe_bits_set(words,24,14,1700); /* mru_len */
 rtl_fe_bits_set(words,38,4,15); /* t5_ctrl */
 rtl_fe_bits_set(words,42,4,0); /* t1_ctrl */
 rtl_fe_bits_set(words,68,6,destination); /* default_ldpid */
 rtl_fe_bits_set(words,88,6,source); /* lspid */
}
static inline int rtl_fe_classifier_read(const struct rtl_dq_io *io,unsigned int index,unsigned int words[7])
{
 unsigned int i;
 int ret=io->indirect(io->context,0x33b0,0x80000000U|index);
 if(ret)return ret;
 for(i=0;i<7;i++)words[i]=io->read(io->context,0x33cc-i*4);
 return 0;
}
static inline int rtl_fe_classifier_write(const struct rtl_dq_io *io,unsigned int index,const unsigned int words[7])
{
 unsigned int i;
 for(i=0;i<7;i++)io->write(io->context,0x33cc-i*4,words[i]);
 return io->indirect(io->context,0x33b0,0xc0000000U|index);
}

static inline int rtl_fe_source_mac_read(const struct rtl_dq_io *io,unsigned int index,unsigned int words[5])
{
 unsigned int i;
 int ret=io->indirect(io->context,0x3200,0x80030000U|index);
 if(ret)return ret;
 for(i=0;i<5;i++)words[i]=io->read(io->context,0x3214-i*4);
 return 0;
}
static inline int rtl_fe_source_mac_write(const struct rtl_dq_io *io,unsigned int index,const unsigned int words[5])
{
 unsigned int i;
 for(i=0;i<5;i++)io->write(io->context,0x3214-i*4,words[i]);
 return io->indirect(io->context,0x3200,0xc0030000U|index);
}
/* TX interface MIB is224 bits:bytes64,UC32,non-UC32,drop-bytes64,
 * drop-packets32. A flow can own one of64 egress-interface counter slots. */
static inline int rtl_fe_tx_counter_read(const struct rtl_dq_io *io,
 unsigned int index,unsigned int words[7])
{
 unsigned int i;
 int ret=io->indirect(io->context,0x35c8,0x80000000U|index);

 if(ret)return ret;
 for(i=0;i<7;i++)words[i]=io->read(io->context,0x35e4-i*4);
 return 0;
}
/* L2 FDB key stores a48-bit MAC beginning at bit51; the return value's
 * high13 bits are the same destination-MAC index used by the Hash action. */
static inline void rtl_fe_fdb_key(unsigned int words[4],const unsigned char mac[6])
{
 unsigned int i;
 for(i=0;i<4;i++)words[i]=0;
 for(i=0;i<6;i++)rtl_fe_bits_set(words,51+(5-i)*8,8,mac[i]);
 words[0]=(1U<<9)|(255U<<11)|(1U<<19)|(1U<<20)|(1U<<21); /* Static UC; both permissions enabled. */
}
static inline int rtl_fe_fdb_command(const struct rtl_dq_io *io,unsigned int command,unsigned int words[4],unsigned int *result)
{
 unsigned int i,control=io->read(io->context,0x1c00);
 int ret;
 io->write(io->context,0x1c00,control|0x80000000U);
 for(i=0;i<4;i++)io->write(io->context,0x1cb0-i*4,words[i]);
 io->write(io->context,0x1c2c,0);
 ret=io->indirect(io->context,0x1ca0,0x80000000U|command);
 io->write(io->context,0x1c00,control);
 if(ret)return ret;
 *result=io->read(io->context,0x1c2c);
 for(i=0;i<4;i++)words[i]=io->read(io->context,0x1cb0-i*4);
 return 0;
}
#endif
