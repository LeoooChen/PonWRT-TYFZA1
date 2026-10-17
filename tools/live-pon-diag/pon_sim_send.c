// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ktime.h>
#include <linux/unaligned.h>
#include <net/ip.h>
#include <net/checksum.h>
static unsigned int packets=8,rate_mbps=100;
module_param(packets,uint,0600);
module_param(rate_mbps,uint,0600);
MODULE_PARM_DESC(rate_mbps,"Ethernet wire rate including FCS, preamble and inter-frame gap");
static int __init send_init(void)
{
 struct net_device *lan=dev_get_by_name(&init_net,"lan4");
 void __iomem *dma,*vp;
 void *memory;
 dma_addr_t address;
 u32 saved[6],status,first;
 u64 started,elapsed;
 unsigned int done=0;
 int ret=0;
 if(!lan)return -ENODEV;
 if(!packets || !rate_mbps || rate_mbps>1000){dev_put(lan);return -EINVAL;}
 dma=ioremap(0x4f7001000ULL,0x1000);vp=dma+0x100+11*0xa0;
 if(readl(vp)){ret=-EBUSY;goto unmap;}
 memory=dma_alloc_coherent(lan->dev.parent,128*1024,&address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 saved[0]=readl(dma+0x14);saved[1]=readl(vp+8);saved[2]=readl(vp+12);
 saved[3]=readl(vp+0x64);saved[4]=readl(vp+0x78);saved[5]=readl(vp+0x1c);
 first=saved[5]&63;
 memset(memory,0,128*1024);
 for(unsigned int i=0;i<64;i++){
  u8 *eth=memory+512+i*1536;
  struct iphdr *ip=(void *)(eth+14);
  struct udphdr *udp=(void *)(eth+34);
  memcpy(eth,"\x00\x50\xc2\x01\x02\x00",6);memcpy(eth+6,"\x02\x55\x46\x50\x4f\x4e",6);
  put_unaligned_be16(ETH_P_IP,eth+12);
  ip->version=4;ip->ihl=5;ip->ttl=64;ip->protocol=IPPROTO_UDP;
  ip->tot_len=htons(1500);ip->saddr=htonl(0xc6120001);ip->daddr=htonl(0xc6120002);
  ip_send_check(ip);udp->source=htons(55000);udp->dest=htons(55001);udp->len=htons(1480);
  for(unsigned int byte=56;byte<1514;byte++)eth[byte]=(byte*31+7)&255;
  memcpy(eth+42,"TYFPON-SIM",10);put_unaligned_be32(i,eth+52);
  udp->check=csum_tcpudp_magic(ip->saddr,ip->daddr,1480,IPPROTO_UDP,csum_partial(udp,1480,0));
  if(!udp->check)udp->check=CSUM_MANGLED_0;
  put_unaligned_le32((u32)address+512+i*1536,memory+i*8);
  /* CPU injection enters connector25 with PON source7/POL_ID0.
   * The matched PON tuple selects the actual Wi-Fi hardware output. */
  put_unaligned_le32(0x81000001U|(7U<<25)|(1514U<<8),memory+i*8+4);
 }
 /* TYFZA1 descriptor fetch uses the same CCI alias as native VP8/9/10. */
 writel(0,vp+0x64);writel(2,vp+8);writel(lower_32_bits(address)+6,vp+12);
 writel(BIT(31)|BIT(30),vp+4);
 ret=readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);if(ret)goto restore;
 writel((saved[0]&~(3U<<22))|(1U<<22),dma+0x14);writel(BIT(9),vp+0x78);writel(1,vp);
 started=ktime_get_ns();
 while(done<packets){
  /* The NI consumer retains its last two prefetched payloads. */
  unsigned int n=min(61U,packets-done),end=(first+done+n)&63;
  for(unsigned int i=0;i<n;i++){
   unsigned int slot=(first+done+i)&63;
   u8 *eth=memory+512+slot*1536;
   struct udphdr *udp=(void *)(eth+34);
   __be32 previous=get_unaligned((__be32 *)(eth+52));
   csum_replace4(&udp->check,previous,htonl(done+i));
   put_unaligned_be32(done+i,eth+52);
   if(!udp->check)udp->check=CSUM_MANGLED_0;
  }
  dma_wmb();writel(end,vp+0x18);writel(1,vp+0x68);
  ret=readl_poll_timeout(vp+0x1c,status,(status&63)==end,1,100000);if(ret)break;
  done+=n;
  /* 1514-byte frame + FCS4 + preamble/SFD8 + IFG12. */
  u64 due=started+div_u64((u64)done*1538*8*1000,rate_mbps);
  u64 now=ktime_get_ns();
  if(due>now+1000){
   unsigned int wait=div_u64(due-now,1000);
   usleep_range(wait,wait+20);
  }
 }
 elapsed=ktime_get_ns()-started;
 writel(0,vp);
 pr_info("PON_SIM HP11 VP11 dma=%pad source=7 connector=25 sent=%u requested=%u read=%u ret=%d target_wire_Mbps=%u elapsed_ns=%llu offered_wire_bps=%llu offered_frame_bps=%llu\n",
  &address,done,packets,readl(vp+0x1c)&63,ret,rate_mbps,elapsed,
  div64_u64((u64)done*1538*8*NSEC_PER_SEC,elapsed),
  div64_u64((u64)done*1514*8*NSEC_PER_SEC,elapsed));
restore:
 writel(0,vp);writel(saved[1],vp+8);writel(saved[2],vp+12);
 writel(BIT(31)|BIT(30),vp+4);readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 writel(saved[3],vp+0x64);writel(saved[4],vp+0x78);
 writel((readl(dma+0x14)&~(3U<<22))|(saved[0]&(3U<<22)),dma+0x14);
 dma_free_coherent(lan->dev.parent,128*1024,memory,address);
unmap:iounmap(dma);dev_put(lan);return ret;
}
static void __exit send_exit(void) {}
module_init(send_init);
module_exit(send_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 PON source tuple NI DMA injection");
