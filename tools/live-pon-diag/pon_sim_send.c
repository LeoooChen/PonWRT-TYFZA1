// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/unaligned.h>
#include <linux/sched/cputime.h>
#include <net/ip.h>
#include <net/checksum.h>
static unsigned int packets=8,rate_mbps=100,session_id=0x1234;
module_param(packets,uint,0600);
module_param(rate_mbps,uint,0600);
module_param(session_id,uint,0600);
MODULE_PARM_DESC(rate_mbps,"Ethernet wire rate including FCS, preamble and inter-frame gap");
static void wait_deadline(u64 due)
{
 u64 now=ktime_get_ns();
 if(due>now+20000){
  unsigned int wait=div_u64(due-now-20000,1000);
  usleep_range(wait,wait+10);
 }
 while(ktime_get_ns()<due)cpu_relax();
}
static int __init send_init(void)
{
 struct net_device *lan=dev_get_by_name(&init_net,"lan4");
 void __iomem *dma,*vp;
 void *memory;
 dma_addr_t address;
 u32 saved[6],status,first;
 u64 started,elapsed,due,max_lateness=0;
 unsigned int late_batches=0;
 u64 user_start,system_start,user_end,system_end;
 unsigned int done=0;
 int ret=0;
 task_cputime(current,&user_start,&system_start);
 if(!lan)return -ENODEV;
 if(!packets || !rate_mbps){dev_put(lan);return -EINVAL;}
 dma=ioremap(0x4f7001000ULL,0x1000);vp=dma+0x100+11*0xa0;
 if(readl(vp)){ret=-EBUSY;goto unmap;}
 memory=dma_alloc_coherent(lan->dev.parent,1024*(1536+8),&address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 saved[0]=readl(dma+0x14);saved[1]=readl(vp+8);saved[2]=readl(vp+12);
 saved[3]=readl(vp+0x64);saved[4]=readl(vp+0x78);saved[5]=readl(vp+0x1c);
 first=saved[5]&1023;
 memset(memory,0,1024*(1536+8));
 for(unsigned int i=0;i<1024;i++){
  u8 *eth=memory+8192+i*1536;
  struct iphdr *ip=(void *)(eth+22);
  struct udphdr *udp=(void *)(eth+42);
  memcpy(eth,"\x98\x1e\x89\x61\x68\xd0",6);memcpy(eth+6,"\x02\x55\x46\x50\x4f\x4e",6);
  put_unaligned_be16(ETH_P_PPP_SES,eth+12);
  eth[14]=0x11;eth[15]=0;put_unaligned_be16(session_id,eth+16);
  put_unaligned_be16(1494,eth+18);put_unaligned_be16(0x21,eth+20);
  ip->version=4;ip->ihl=5;ip->ttl=64;ip->protocol=IPPROTO_UDP;
  ip->tot_len=htons(1492);ip->saddr=htonl(0xc612ff01);ip->daddr=htonl(0xc6120002);
  ip_send_check(ip);udp->source=htons(55000);udp->dest=htons(55001);udp->len=htons(1472);
  for(unsigned int byte=64;byte<1514;byte++)eth[byte]=(byte*31+7)&255;
  memcpy(eth+50,"TYFPPP-SIM",10);put_unaligned_be32(i,eth+60);
  udp->check=csum_tcpudp_magic(ip->saddr,ip->daddr,1472,IPPROTO_UDP,csum_partial(udp,1472,0));
  if(!udp->check)udp->check=CSUM_MANGLED_0;
  put_unaligned_le32((u32)address+8192+i*1536,memory+i*8);
  /* CPU injection enters connector25 with PON source7/POL_ID0.
   * The matched PON tuple selects the actual Wi-Fi hardware output. */
  put_unaligned_le32(0x81000001U|(7U<<25)|(1514U<<8),memory+i*8+4);
 }
 /* TYFZA1 descriptor fetch uses the same CCI alias as native VP8/9/10. */
 writel(0,vp+0x64);writel(2,vp+8);writel(lower_32_bits(address)+10,vp+12);
 writel(BIT(31)|BIT(30),vp+4);
 ret=readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);if(ret)goto restore;
 writel((saved[0]&~(3U<<22))|(1U<<22),dma+0x14);writel(BIT(9),vp+0x78);
 pr_info("PON_SIM source ready\n");
 writel(1,vp);
 started=ktime_get_ns();due=started;
 while(done<packets){
  /* The NI consumer retains its last two prefetched payloads. */
  unsigned int n=min(61U,packets-done),end=(first+done+n)&1023;
  for(unsigned int i=0;i<n;i++){
   unsigned int slot=(first+done+i)&1023;
   u8 *eth=memory+8192+slot*1536;
   struct udphdr *udp=(void *)(eth+42);
   __be32 previous=get_unaligned((__be32 *)(eth+60));
   csum_replace4(&udp->check,previous,htonl(done+i));
   put_unaligned_be32(done+i,eth+60);
   if(!udp->check)udp->check=CSUM_MANGLED_0;
  }
  /* Prepare while the previous batch's wire time elapses. */
  wait_deadline(due);
  u64 published=ktime_get_ns();
  if(published>due){
   late_batches++;max_lateness=max(max_lateness,published-due);
  }
  dma_wmb();writel(end,vp+0x18);writel(1,vp+0x68);
  ret=readl_poll_timeout(vp+0x1c,status,(status&1023)==end,1,100000);if(ret)break;
  done+=n;
  /* 1514-byte frame + FCS4 + preamble/SFD8 + IFG12. */
  /* A delayed wakeup must not accumulate catch-up bursts into the QM. */
  due=max(due,published)+div_u64((u64)n*1538*8*1000,rate_mbps);
 }
 wait_deadline(due);
 elapsed=ktime_get_ns()-started;
 writel(0,vp);
 task_cputime(current,&user_end,&system_end);
 pr_info("PON_SIM HP11 VP11 dma=%pad source=7 connector=25 PPPoE_session=%u sent=%u requested=%u read=%u ret=%d target_wire_Mbps=%u elapsed_ns=%llu offered_wire_bps=%llu offered_frame_bps=%llu\n",
  &address,session_id,done,packets,readl(vp+0x1c)&1023,ret,rate_mbps,elapsed,
  mul_u64_u64_div_u64((u64)done*1538*8,NSEC_PER_SEC,elapsed),
  mul_u64_u64_div_u64((u64)done*1514*8,NSEC_PER_SEC,elapsed));
 pr_info("PON_SIM generator_raw_cpu_ns=%llu\n",user_end+system_end-user_start-system_start);
 pr_info("PON_SIM late_batches=%u max_lateness_ns=%llu\n",late_batches,max_lateness);
restore:
 writel(0,vp);writel(saved[1],vp+8);writel(saved[2],vp+12);
 writel(BIT(31)|BIT(30),vp+4);readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 writel(saved[3],vp+0x64);writel(saved[4],vp+0x78);
 writel((readl(dma+0x14)&~(3U<<22))|(saved[0]&(3U<<22)),dma+0x14);
 dma_free_coherent(lan->dev.parent,1024*(1536+8),memory,address);
unmap:iounmap(dma);dev_put(lan);return ret;
}
static void __exit send_exit(void) {}
module_init(send_init);
module_exit(send_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 valid PPPoE session-frame NI DMA injection");
