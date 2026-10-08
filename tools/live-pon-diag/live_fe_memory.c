// SPDX-License-Identifier: GPL-2.0-or-later
/* Prepare native DMA Hash/action/miss tables while data ingress bypasses FE. */
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include "rtl9617c-fe-table.h"

static const unsigned int regs[] = {
 0x3834,0x3838,0x383c,0x3840,0x3844,0x3848,0x384c,0x3850,
 0x3854,0x3858,0x385c,0x38a0,0x38c4,0x3724,
};
static struct device *device;
static void __iomem *base;
static void *memory;
static dma_addr_t dma_address;
static unsigned int saved[ARRAY_SIZE(regs)];
static bool configured;
static unsigned int reg_read(void *context,unsigned int reg) {return readl(base+reg);}
static void reg_write(void *context,unsigned int reg,unsigned int value) {writel(value,base+reg);}
static int reg_access(void *context,unsigned int reg,unsigned int value)
{
 u32 status;
 unsigned int busy=reg==0x38b0 || reg==0x393c ? 1 : BIT(31);
 writel(value,base+reg);
 return readl_poll_timeout(base+reg,status,!(status&busy),1,100000);
}
static int cache_dma_check(const struct rtl_dq_io *io)
{
 struct rtl_fe_ipv4_action action={.address=0xc6336401,.port=62000,.destination=32,
  .mac_da_index=2,.ingress_interface=0,.egress_interface=1,.cos=0,.translate=1,.deep_queue=1};
 unsigned int expected[8],actual[7],metadata[7],slot,index=0;
 unsigned int crc32=0x12345678;
 unsigned short crc16=0x0123;
 unsigned int *hash=memory,*actions=memory+RTL_FE_ACTION_OFFSET;
 int ret;

 rtl_fe_ipv4_action(expected,&action);
 hash[index]=cpu_to_le32(crc32);
 memcpy(actions+index*8,expected,32);
 dma_wmb();
 ret=rtl_fe_main_age(io,index,7);
 if(ret)return ret;
 ret=rtl_fe_cache_command(io,index,crc16,2);
 if(ret)return ret;
 io->write(io->context,0x38ac,(index&31)|((unsigned int)crc16<<5)|(3U<<26)|(1U<<24));
 ret=io->indirect(io->context,0x38b0,1);
 if(ret)return ret;
 pr_info("TYFZA1 FE cache allocation status=%08x\n",readl(base+0x38b4));
 if(readl(base+0x38b4)&6)return -EIO;
 for(slot=0;slot<32;slot++) {
  ret=rtl_fe_cache_read(io,slot,0,metadata);
  if(ret)return ret;
  if(metadata[0]!=crc32 || (metadata[1]&0x1fff)!=crc16)continue;
  ret=rtl_fe_cache_read(io,slot,1,actual);
  if(ret)return ret;
  if(memcmp(actual,expected,28)) {
   unsigned int i;
   for(i=0;i<7;i++)pr_info("TYFZA1 FE cache action slot%u word%u=%08x expected=%08x\n",slot,i,actual[i],expected[i]);
   pr_info("TYFZA1 FE cache key=%08x/%08x proves main Hash DMA read\n",metadata[0],metadata[1]);
   return -EIO;
  }
  pr_info("TYFZA1 native FE DMA fetch verified:CRC=%08x cache_slot=%u NAT_action224 matches CPU-written table\n",crc32,slot);
  ret=rtl_fe_cache_command(io,index,crc16,1);
  if(ret)return ret;
  ret=rtl_fe_main_age(io,index,0);
  if(ret)return ret;
  hash[index]=0;
  dma_wmb();
  return 0;
 }
 return -ENOENT;
}
static void restore(void)
{
 unsigned int i;
 /* Stop cache allocations before detaching every DMA base from this memory. */
 writel(0,base+0x38c4);
 writel(1,base+0x393c);
 {u32 status;readl_poll_timeout(base+0x393c,status,!(status&1),1,100000);}
 for(i=0;i<ARRAY_SIZE(regs);i++)
  writel(saved[i],base+regs[i]);
 readl(base+0x383c);
 configured=false;
}
static int __init memory_init(void)
{
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 unsigned int i,*defaults;
 u32 status;
 int ret;

 device=bus_find_device_by_name(&platform_bus_type,NULL,"4f4300000.ethernet");
 if(!device)return -ENODEV;
 base=ioremap(0x4f4300000ULL,0x4000);
 if(!base){ret=-ENOMEM;goto put_device;}
 memory=dma_alloc_coherent(device,RTL_FE_MEMORY_BYTES,&dma_address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 defaults=memory+RTL_FE_DEFAULT_OFFSET;
 for(i=0;i<32;i++)rtl_fe_cpu_miss(defaults+i*4);
 dma_wmb();
 for(i=0;i<ARRAY_SIZE(regs);i++)saved[i]=readl(base+regs[i]);
 rtl_fe_dma_tables(&io,lower_32_bits(dma_address),(upper_32_bits(dma_address)&255)|2);
 configured=true;
 writel(1,base+0x393c);
 ret=readl_poll_timeout(base+0x393c,status,!(status&1),1,100000);
 if(ret)goto restore_tables;
 pr_info("TYFZA1 native FE DMA:address=%pad bytes=%u main=%u flow32B defaults16B cache256-internal; memory initialization complete\n",
  &dma_address,RTL_FE_MEMORY_BYTES,RTL_FE_HASH_ENTRIES);
 pr_info("TYFZA1 FE DMA readback:HASH=%08x/%08x ACTION=%08x/%08x DEFAULT=%08x/%08x config=%08x cache=%08x\n",
  readl(base+0x3838),readl(base+0x383c),readl(base+0x3840),readl(base+0x3844),
  readl(base+0x3858),readl(base+0x385c),readl(base+0x3834),readl(base+0x38a0));
 writel(RTL_FE_ACTION_GROUPS|(3U<<25),base+0x3724);
 ret=cache_dma_check(&io);
 if(ret)goto restore_tables;
 return 0;
restore_tables:
 restore();
 dma_free_coherent(device,RTL_FE_MEMORY_BYTES,memory,dma_address);
unmap:
 iounmap(base);
put_device:
 put_device(device);
 return ret;
}
static void __exit memory_exit(void)
{
 if(configured)restore();
 dma_free_coherent(device,RTL_FE_MEMORY_BYTES,memory,dma_address);
 iounmap(base);
 put_device(device);
 pr_info("TYFZA1 native FE DMA tables detached and original configuration restored\n");
}
module_init(memory_init);
module_exit(memory_exit);
MODULE_DESCRIPTION("Native RTL9617C FE DMA table initialization");
MODULE_LICENSE("GPL");
