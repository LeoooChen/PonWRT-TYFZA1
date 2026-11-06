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
