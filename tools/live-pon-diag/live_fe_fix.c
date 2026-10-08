// SPDX-License-Identifier: GPL-2.0-or-later
/* Correct the running native FE mask's VLAN selector after bitfield review. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include "rtl9617c-fe-table.h"
static void __iomem *base;
static unsigned int reg_read(void *ctx,unsigned int reg){return readl(base+reg);}
static void reg_write(void *ctx,unsigned int reg,unsigned int value){writel(value,base+reg);}
static int reg_access(void *ctx,unsigned int reg,unsigned int value)
{
 u32 status;
 writel(value,base+reg);
 return readl_poll_timeout(base+reg,status,!(status&BIT(31)),1,100000);
}
static int __init fix_init(void)
{
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 unsigned int mask[8],readback[8],before;
 int ret;

 base=ioremap(0x4f4300000ULL,0x10000);
 if(!base)return -ENOMEM;
 ret=rtl_fe_mask_read(&io,63,mask);
 if(ret)goto unmap;
 before=mask[3];
 mask[3]=(mask[3]|BIT(18))&~BIT(4);
 ret=rtl_fe_mask_write(&io,63,mask);
 if(ret)goto unmap;
 ret=rtl_fe_mask_read(&io,63,readback);
 if(!ret && memcmp(mask,readback,sizeof(mask)))ret=-EIO;
 pr_info("TYFZA1 native FE mask63 correction:word3=%08x -> %08x readback=%d\n",before,mask[3],ret);
unmap:
 iounmap(base);
 return ret;
}
static void __exit fix_exit(void){}
module_init(fix_init);
module_exit(fix_exit);
MODULE_DESCRIPTION("Correct the native FE VLAN mask bit100");
MODULE_LICENSE("GPL");
