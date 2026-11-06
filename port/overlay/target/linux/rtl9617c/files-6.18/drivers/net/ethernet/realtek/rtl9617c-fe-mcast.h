/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_MCAST_H
#define RTL9617C_FE_MCAST_H
#include <linux/errno.h>
#include "rtl9617c-qm-deepq.h"

/* ca-ne.ko ni_mce_indx_table and l2fe_arb_fib_table. Software group IDs
 * (1024 entries in stock) are NOT valid NI hardware indices. Bitmap bits
 * select arbitration actions, not physical ports. The FE mutex serializes
 * these indirect engines; an outstanding command must finish before reuse.
 */
#define RTL_FE_MC_GROUPS 256
#define RTL_FE_MC_ACTIONS 64

/* FC's replica producer specifies the FINAL S/C tags, including PCP/DEI.
 * It does not use the command as a relative push. The L2 editor TPID table
 * differs from the L3 parser table: outer selects A..D, inner only C/D.
 * Read its actual values rather than assuming that L3 selectors also apply.
 * Destination0..6 covers native Ethernet/P6; PON GEM and PE radio replicas
 * require their own checked routing/metadata and are not represented here.
 */
struct rtl_fe_mc_replica {
 unsigned int destination,tags;
 struct {unsigned short protocol,tci;} tag[2];
};

static inline int rtl_fe_mc_replica_encode(const struct rtl_dq_io *io,
 const struct rtl_fe_mc_replica *replica,unsigned int words[3])
{
 unsigned int i,slot,first,tpid,result[3]={2,2,1};

 if(replica->destination>6 || replica->tags>2)return -EOPNOTSUPP;
 result[1]|=replica->destination<<24|1U<<30; /* Preserve COS. */
 for(i=0;i<replica->tags;i++) {
  first=i?2:0;
  if(replica->tag[i].protocol!=0x8100 && replica->tag[i].protocol!=0x88a8)
   return -EOPNOTSUPP;
  for(slot=first;slot<4;slot++)
   if((io->read(io->context,0x1704+slot*4)&65535)==replica->tag[i].protocol)break;
  if(slot==4)return -EOPNOTSUPP;
  tpid=slot-first;
  if(i)result[0]=1U|(replica->tag[i].tci&4095U)<<2|1U<<14|tpid<<15|
   1U<<16|(replica->tag[i].tci>>13)<<18|1U<<21|((replica->tag[i].tci>>12)&1U)<<22;
  else result[1]=(result[1]&~3U)|1U|(replica->tag[i].tci&4095U)<<2|1U<<14|tpid<<15|
   1U<<17|(replica->tag[i].tci>>13)<<19|1U<<22|((replica->tag[i].tci>>12)&1U)<<23;
 }
 /* Linux has already selected the recipients. Match stock's explicit
  * source-filter disable, retaining source MAC and TTL/Hop Limit. */
 for(i=0;i<3;i++)words[i]=result[i];
 return 0;
}

static inline int rtl_fe_mc_group_read(const struct rtl_dq_io *io,
 unsigned int index,unsigned int words[2])
{
 unsigned int result[2];
 int ret;

 if(index>=RTL_FE_MC_GROUPS)return -EINVAL;
 if(io->read(io->context,0xaa64)&0x80000000U)return -EBUSY;
 ret=io->indirect(io->context,0xaa64,0x80000000U|index);
 if(ret)return ret;
 result[0]=io->read(io->context,0xaa6c);
 result[1]=io->read(io->context,0xaa68);
 words[0]=result[0];words[1]=result[1];
 return 0;
}

static inline int rtl_fe_mc_group_write(const struct rtl_dq_io *io,
 unsigned int index,const unsigned int words[2])
{
 unsigned int actual[2];
 int ret;

 /* Entry0 is the reserved empty group, never a native allocation target. */
 if(!index || index>=RTL_FE_MC_GROUPS)return -EINVAL;
 if(io->read(io->context,0xaa64)&0x80000000U)return -EBUSY;
 io->write(io->context,0xaa6c,words[0]);
 io->write(io->context,0xaa68,words[1]);
 ret=io->indirect(io->context,0xaa64,0xc0000000U|index);
 if(ret)return ret;
 ret=rtl_fe_mc_group_read(io,index,actual);
 if(ret)return ret;
 return actual[0]==words[0] && actual[1]==words[1]?0:-EIO;
}

static inline int rtl_fe_mc_action_read(const struct rtl_dq_io *io,
 unsigned int index,unsigned int words[3])
{
 unsigned int i;
 int ret;

 if(index>=RTL_FE_MC_ACTIONS)return -EINVAL;
 if(io->read(io->context,0x1634)&0x80000000U)return -EBUSY;
 ret=io->indirect(io->context,0x1634,0x80000000U|index);
 if(ret)return ret;
 for(i=0;i<3;i++)words[i]=io->read(io->context,0x1640-i*4);
 return 0;
}

static inline int rtl_fe_mc_action_write(const struct rtl_dq_io *io,
 unsigned int index,const unsigned int words[3])
{
 unsigned int actual[3],i;
 int ret;

 if(index>=RTL_FE_MC_ACTIONS)return -EINVAL;
 if(io->read(io->context,0x1634)&0x80000000U)return -EBUSY;
 for(i=0;i<3;i++)io->write(io->context,0x1640-i*4,words[i]);
 ret=io->indirect(io->context,0x1634,0xc0000000U|index);
 if(ret)return ret;
 ret=rtl_fe_mc_action_read(io,index,actual);
 if(ret)return ret;
 for(i=0;i<3;i++)if(actual[i]!=words[i])return -EIO;
 return 0;
}

/* This is only table withdrawal. It does NOT retire packets already carrying
 * the NI/action index. The owner must remove classifier references before
 * clearing the bitmap, and prove retirement before releasing either index.
 */
static inline int rtl_fe_mc_group_clear(const struct rtl_dq_io *io,unsigned int index)
{
 const unsigned int empty[2]={0,0};
 return rtl_fe_mc_group_write(io,index,empty);
}
#endif
