// SPDX-License-Identifier: GPL-2.0-or-later
/* Prepare native DMA Hash/action/miss tables while data ingress bypasses FE. */
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include <linux/delay.h>
#include <net/ip.h>
#include <net/checksum.h>
#include "rtl9617c-ni.h"
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-fe-table.h"

static const unsigned int regs[] = {
 0x3834,0x3838,0x383c,0x3840,0x3844,0x3848,0x384c,0x3850,
 0x3854,0x3858,0x385c,0x38a0,0x38c4,0x3724,
 0x3400,0x3404,0x3414,0x3418,0x341c,0x3700,0x3704,0x3860,
 0x394c,0x3950,0x3954,0x3958,0x395c,
 0x2300,0x23a0,0x23a4,0x30b8,0xa5f8,
};
static struct device *device;
static void __iomem *base;
static void *memory;
static dma_addr_t dma_address;
static unsigned int saved[ARRAY_SIZE(regs)];
static bool configured;
static unsigned int egress=16;
module_param(egress,uint,0600);
static unsigned int lan_redirect=25;
module_param(lan_redirect,uint,0600);
static bool wired_ingress;
module_param(wired_ingress,bool,0600);
static unsigned int lan_fib_saved[3];
static bool lan_fib_configured;
static bool packet_test;
static bool mac_test;
module_param(mac_test,bool,0600);
static bool destination_test=true;
module_param(destination_test,bool,0600);
static bool mac_configured,fdb_created;
static unsigned int cam_saved[5],interface_saved,fdb_saved[4];
static const u8 source_mac[6]={2,0x66,0x55,0x44,0x33,0x22};
static const u8 destination_mac[6]={2,0x77,0x66,0x55,0x44,0x33};
module_param(packet_test,bool,0600);
static bool pipeline_configured;
static unsigned int mask_saved[8],classifier_saved[2][7],map_saved[4];
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
static int configure_lan_ingress(const struct rtl_dq_io *io)
{
 unsigned int i,words[3];
 int ret=io->indirect(io->context,0x1e14,0x80000000U);
 if(ret)return ret;
 for(i=0;i<3;i++)words[i]=lan_fib_saved[i]=io->read(io->context,0x1e20-i*4);
 words[1]=(words[1]&~(255U<<8))|(lan_redirect<<8);
 for(i=0;i<3;i++)io->write(io->context,0x1e20-i*4,words[i]);
 ret=io->indirect(io->context,0x1e14,0xc0000000U);
 if(ret)return ret;
 lan_fib_configured=true;
 /* The RAM bring-up inherited NI's force-CPU receive bypass. L2
  * classification must see the physical LAN packet for this test. */
 writel(readl(base+0xa5f8)&~(BIT(28)|BIT(31)|GENMASK(21,16)),base+0xa5f8);
 pr_info("TYFZA1 native FE wired ingress:LAN0 PONCLE redirect=%u; Hash miss returns CPU16\n",lan_redirect);
 return 0;
}

static int configure_mac(const struct rtl_dq_io *io,unsigned int *destination_index)
{
 unsigned int cam[5]={},fdb[4],result,i;
 int ret;
 ret=rtl_fe_source_mac_read(io,14,cam_saved);
 if(ret)return ret;
 ret=io->indirect(io->context,0x3000,0x8000001f);
 if(ret)return ret;
 interface_saved=io->read(io->context,0x3004);
 mac_configured=true;
 for(i=0;i<6;i++)rtl_fe_bits_set(cam,(5-i)*8,8,source_mac[i]);
 cam[1]|=1U<<16;
 ret=rtl_fe_source_mac_write(io,14,cam);
 if(ret)return ret;
 io->write(io->context,0x3004,BIT(18)|(15U<<19));
 ret=io->indirect(io->context,0x3000,0xc000001f);
 if(ret)return ret;
 rtl_fe_fdb_key(fdb,destination_mac);
 ret=rtl_fe_fdb_command(io,4,fdb,&result);
 if(ret)return ret;
 pr_info("TYFZA1 FE destination FDB read result=%08x control=%08x\n",result,readl(base+0x1c00));
 if((result&15)==5)memcpy(fdb_saved,fdb,sizeof(fdb));
 else {
  rtl_fe_fdb_key(fdb,destination_mac);
  ret=rtl_fe_fdb_command(io,0x45,fdb,&result);
  if(ret)return ret;
  pr_info("TYFZA1 FE destination FDB append result=%08x\n",result);
  if((result&15)!=5)return -EIO;
  fdb_created=true;
  rtl_fe_fdb_key(fdb,destination_mac);
  ret=rtl_fe_fdb_command(io,4,fdb,&result);
  if(ret)return ret;
  pr_info("TYFZA1 FE destination FDB lookup after append=%08x data=%08x/%08x/%08x/%08x\n",result,fdb[0],fdb[1],fdb[2],fdb[3]);
  if((result&15)!=5)return -EIO;
 }
 *destination_index=(result>>4)&0x1fff;
 pr_info("TYFZA1 FE MAC setup:source_CAM14=%pM EIF31=007c0000 destination_FDB%u=%pM\n",
  source_mac,*destination_index,destination_mac);
 return 0;
}

static int packet_pipeline(const struct rtl_dq_io *io)
{
 struct rtl_fe_ipv4_key key={.source=0x0a172a01,.destination=0xc0a80101,
  .source_port=53000,.destination_port=18082,.protocol=17,.ingress=0,.mask=63};
 struct rtl_fe_ipv4_action action={.address=0xc6336401,.port=62000,.destination=16,
  .translate=1,.cos=0};
 unsigned int mask[8],key_words[RTL_FE_KEY_WORDS],action_words[8],cls[7],lpb[3],crc32,index,i,trace[64];
 unsigned short crc16;
 unsigned int destination_index;
 struct net_device *owner=dev_get_drvdata(device);
 struct sk_buff *skb;
 struct ethhdr *eth;
 struct iphdr *ip;
 struct udphdr *udp;
 const struct rtl9617c_ni_frame frame={.header_low=(25<<3)|BIT(29)};
 int ret;
 static const char payload[]="TYFZA1-FE-NAT-TEST";

 ret=rtl_fe_mask_read(io,63,mask_saved);
 if(ret)return ret;
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_read(io,1024+i,classifier_saved[i]);
  if(ret)return ret;
 }
 for(i=0;i<4;i++) {
  ret=io->indirect(io->context,0x1654,0x80000000U|25|(i<<6));
  if(ret)return ret;
  map_saved[i]=io->read(io->context,0x1658);
 }
 pipeline_configured=true;
 rtl_fe_ipv4_mask(mask);
 mask[6]|=BIT(201-192)|BIT(215-192)|BIT(218-192);
 ret=rtl_fe_mask_write(io,63,mask);
 if(ret)return ret;
 rtl_fe_classify_hash(cls,0,0);
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_write(io,1024+i,cls);
  if(ret)return ret;
 }
 rtl_fe_ingress_lpb(lpb,0,16);
 for(i=0;i<3;i++)writel(lpb[i],base+0x3414+i*4);
 writel((readl(base+0x3400)&~(BIT(0)|BIT(10)|BIT(19)))|BIT(3)|BIT(2),base+0x3400);
 writel(7|(25<<8)|(1<<14)|(24<<16)|(2<<22)|(3<<24),base+0x3404);
 for(i=0;i<4;i++) {
  io->write(io->context,0x1658,13);
  ret=io->indirect(io->context,0x1654,0xc0000000U|25|(i<<6));
  if(ret)return ret;
 }
 writel(readl(base+0x2300)|BIT(13),base+0x2300);
 writel(0xff|(6<<16),base+0x2304+13*12);
 for(i=0;i<5;i++)writel(0,base+0x394c+i*4);
 writel(63|(7<<8),base+0x3704);
 writel(1,base+0x3700);
 writel(0x020d0000,base+0x3860); /* group16/18/19,128-bit CPU miss */
 rtl_fe_ipv4_key(key_words,&key);
 rtl_fe_key_set(key_words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(key_words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_crc(key_words,&crc32,&crc16);
 index=(crc16&0xfe00)>>4;
 rtl_fe_ipv4_action(action_words,&action);
 rtl_fe_action_set(action_words,RTL_FE_MCGID,egress);
 rtl_fe_action_set(action_words,RTL_FE_L3_IF_VLD1,0);
 rtl_fe_action_set(action_words,RTL_FE_SMAC_TRANS,0);
 rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX_VLD,0);
 if(mac_test) {
  ret=configure_mac(io,&destination_index);
  if(ret)return ret;
  rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX_VLD,destination_test);
  rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX,destination_index);
  rtl_fe_action_set(action_words,RTL_FE_L3_IF_VLD1,1);
  rtl_fe_action_set(action_words,RTL_FE_EGR_L3_IF_IDX1,31);
 }
 ((u32 *)memory)[index]=cpu_to_le32(crc32);
 memcpy(memory+RTL_FE_ACTION_OFFSET+index*32,action_words,32);
 dma_wmb();
 ret=rtl_fe_main_age(io,index,7);
 if(ret)return ret;
 pr_info("TYFZA1 FE packet static flow:index=%u CRC=%08x/%04x input=10.23.42.1:53000->192.168.1.1:18082 output_source=198.51.100.1:62000\n",index,crc32,crc16);
 if(wired_ingress)return configure_lan_ingress(io);
 skb=alloc_skb(128,GFP_KERNEL);
 if(!skb)return -ENOMEM;
 eth=skb_put(skb,sizeof(*eth));
 ether_addr_copy(eth->h_dest,owner->dev_addr);
 ether_addr_copy(eth->h_source,(u8[]){2,0x11,0x22,0x33,0x44,0x55});
 eth->h_proto=htons(ETH_P_IP);
 ip=skb_put_zero(skb,sizeof(*ip));
 ip->version=4;ip->ihl=5;ip->ttl=64;ip->protocol=17;
 ip->saddr=htonl(key.source);ip->daddr=htonl(key.destination);
 ip->tot_len=htons(sizeof(*ip)+sizeof(*udp)+sizeof(payload));
 ip_send_check(ip);
 udp=skb_put_zero(skb,sizeof(*udp));
 udp->source=htons(key.source_port);udp->dest=htons(key.destination_port);
 udp->len=htons(sizeof(*udp)+sizeof(payload));
 memcpy(skb_put(skb,sizeof(payload)),payload,sizeof(payload));
 udp->check=csum_tcpudp_magic(ip->saddr,ip->daddr,sizeof(*udp)+sizeof(payload),17,
  csum_partial(udp,sizeof(*udp)+sizeof(payload),0));
 if(!udp->check)udp->check=CSUM_MANGLED_0;
 skb->dev=owner;skb->protocol=htons(ETH_P_IP);
 pr_info("TYFZA1 FE packet egress=%u CPU_RX_before=%lu\n",egress,owner->stats.rx_packets);
 ret=rtl9617c_ni_transmit(device,owner,skb,&frame);
 if(ret==NETDEV_TX_BUSY){kfree_skb(skb);return -EBUSY;}
 usleep_range(500,1000);
 for(i=0;i<64;i++) {
  writel(i,base+0x30b8);
  trace[i]=readl(base+0x30bc);
 }
 {
  unsigned int actual32;
  unsigned short actual16;
  ret=rtl_fe_crc_hardware(io,trace,63,&actual32,&actual16);
  if(ret)return ret;
  pr_info("TYFZA1 FE actual packet CRC=%08x/%04x expected=%08x/%04x\n",actual32,actual16,crc32,crc16);
  for(i=0;i<RTL_FE_KEY_WORDS;i++) {
   unsigned int word=io->read(io->context,0x38dc);
   if(word!=key_words[i])pr_info("TYFZA1 FE actual tuple word%u=%08x expected=%08x\n",i,word,key_words[i]);
  }
 }
 for(i=0;i<64;i++)
  pr_info("TYFZA1 FE packet stage%u word%u=%08x\n",i>>5,i&31,trace[i]);
 for(i=0;i<4;i++) {
  writel((15<<5)|i,base+0x30b8);
  pr_info("TYFZA1 FE packet counters%u=%08x\n",i,readl(base+0x30bc));
 }
 pr_info("TYFZA1 FE packet CPU_RX_after=%lu\n",owner->stats.rx_packets);
 for(i=0;i<2;i++) {
  writel((14<<5)|i,base+0x30b8);
  pr_info("TYFZA1 FE packet output_HeaderA%u=%08x\n",i,readl(base+0x30bc));
 }
 return 0;
}

static void restore(void)
{
 unsigned int i;
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 if(lan_fib_configured) {
  for(i=0;i<3;i++)writel(lan_fib_saved[i],base+0x1e20-i*4);
  reg_access(NULL,0x1e14,0xc0000000U);
  lan_fib_configured=false;
 }
 if(mac_configured) {
  unsigned int fdb[4],result;
  if(fdb_created) {
   rtl_fe_fdb_key(fdb,destination_mac);
   rtl_fe_fdb_command(&io,0x85,fdb,&result);
   fdb_created=false;
  }
  rtl_fe_source_mac_write(&io,14,cam_saved);
  writel(interface_saved,base+0x3004);
  reg_access(NULL,0x3000,0xc000001f);
  mac_configured=false;
 }
 if(pipeline_configured) {
  for(i=0;i<4;i++) {
   writel(map_saved[i],base+0x1658);
   reg_access(NULL,0x1654,0xc0000000U|25|(i<<6));
  }
  rtl_fe_mask_write(&io,63,mask_saved);
  for(i=0;i<2;i++)rtl_fe_classifier_write(&io,1024+i,classifier_saved[i]);
  pipeline_configured=false;
 }
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
 base=ioremap(0x4f4300000ULL,0x10000);
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
 if(packet_test) {ret=packet_pipeline(&io);if(ret)goto restore_tables;}
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
