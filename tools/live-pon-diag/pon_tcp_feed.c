// SPDX-License-Identifier: GPL-2.0-only
/* Feed genuine Linux TCP packets into the verified PON-source NI VP11 ABI.
 * Control traffic uses the AP normally; reverse iperf3 data traverses ASIC.
 */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/hrtimer.h>
#include <linux/unaligned.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/pid.h>
#include <linux/sched/cputime.h>

static struct net_device *feed,*lan,*radio,*bridge;
static void __iomem *dma,*vp;
static void *memory;
static dma_addr_t address;
static u32 saved[6],producer;
static struct hrtimer timer;
static u64 data_packets,control_packets,data_bytes,stops,wakes,doorbells;
static u64 data_prepare_ns,payload_copy_ns;
static u64 stream_packets[4];
static unsigned int server_pid;
module_param(server_pid,uint,0600);

static unsigned int room(void)
{
 return ((readl(vp+0x1c)&63)-READ_ONCE(producer)-3)&63;
}

static enum hrtimer_restart tick(struct hrtimer *unused)
{
 if(room()){
  wakes++;netif_wake_queue(feed);
  return HRTIMER_NORESTART;
 }
 hrtimer_forward_now(&timer,ns_to_ktime(100000));
 return HRTIMER_RESTART;
}

static netdev_tx_t transmit(struct sk_buff *skb,struct net_device *dev)
{
 struct iphdr *ip=ip_hdr(skb);
 u8 *frame;
 unsigned int slot,length,available;
 u64 begin,copy_begin;

 if(skb->protocol!=htons(ETH_P_IP) || ip->protocol!=IPPROTO_TCP ||
    tcp_hdr(skb)->source!=htons(5206) || ntohs(tcp_hdr(skb)->dest)<55000 ||
    ntohs(tcp_hdr(skb)->dest)>55003 ||
    tcp_hdr(skb)->syn || tcp_hdr(skb)->fin || tcp_hdr(skb)->rst){
  control_packets++;
  skb->dev=radio;skb_set_queue_mapping(skb,0);
  dev_queue_xmit(skb);
  return NETDEV_TX_OK;
 }
 begin=ktime_get_ns();
 available=room();
 if(!available){
  stops++;netif_stop_queue(dev);
  hrtimer_start(&timer,ns_to_ktime(100000),HRTIMER_MODE_REL);
  return NETDEV_TX_BUSY;
 }
 slot=producer;length=skb->len+8;frame=memory+512+slot*1536;
 memcpy(frame,bridge->dev_addr,ETH_ALEN);
 memcpy(frame+6,"\x02\x55\x46\x50\x4f\x4e",ETH_ALEN);
 put_unaligned_be16(ETH_P_PPP_SES,frame+12);
 frame[14]=0x11;frame[15]=0;
 put_unaligned_be16(0x1234,frame+16);
 put_unaligned_be16(skb->len-ETH_HLEN+2,frame+18);
 put_unaligned_be16(0x21,frame+20);
 copy_begin=ktime_get_ns();
 skb_copy_bits(skb,ETH_HLEN,frame+22,skb->len-ETH_HLEN);
 payload_copy_ns+=ktime_get_ns()-copy_begin;
 put_unaligned_le32(0x81000001U|(7U<<25)|(length<<8),memory+slot*8+4);
 producer=(producer+1)&63;
 /* Publish the whole Linux TX batch; also flush before the ring fills. */
 if(!netdev_xmit_more() || available==1){
  dma_wmb();writel(producer,vp+0x18);writel(1,vp+0x68);doorbells++;
 }
 data_packets++;data_bytes+=skb->len;
 stream_packets[ntohs(tcp_hdr(skb)->dest)-55000]++;
 dev->stats.tx_packets++;dev->stats.tx_bytes+=skb->len;
 dev_consume_skb_any(skb);
 data_prepare_ns+=ktime_get_ns()-begin;
 return NETDEV_TX_OK;
}

static int start(struct net_device *dev)
{
 writel(1,vp);netif_carrier_on(dev);netif_start_queue(dev);
 return 0;
}

static int stop(struct net_device *dev)
{
 hrtimer_cancel(&timer);netif_stop_queue(dev);writel(0,vp);
 return 0;
}

static const struct net_device_ops operations={
 .ndo_open=start,.ndo_stop=stop,.ndo_start_xmit=transmit,
};

static int show_stats(char *buffer,const struct kernel_param *param)
{
 return scnprintf(buffer,PAGE_SIZE,
  "data_packets=%llu data_bytes=%llu control_packets=%llu stops=%llu wakes=%llu doorbells=%llu data_prepare_ns=%llu payload_copy_ns=%llu producer=%u consumer=%u room=%u stream_packets=%llu,%llu,%llu,%llu\n",
  data_packets,data_bytes,control_packets,stops,wakes,doorbells,data_prepare_ns,payload_copy_ns,producer,readl(vp+0x1c)&63,room(),
  stream_packets[0],stream_packets[1],stream_packets[2],stream_packets[3]);
}
static const struct kernel_param_ops stats_ops={.get=show_stats};
module_param_cb(stats,&stats_ops,NULL,0400);

static int server_cpu(char *buffer,const struct kernel_param *param)
{
 struct task_struct *task,*thread;
 struct signal_struct *signal;
 unsigned long flags;
 u64 total,user,system;
 unsigned int threads;
 rcu_read_lock();
 task=pid_task(find_vpid(server_pid),PIDTYPE_PID);
 if(!task){rcu_read_unlock();return -ESRCH;}
 signal=task->signal;
 /* Same raw thread-group accounting as kernel/sched/cputime.c, including
  * already joined iperf3 worker threads. Match /proc/stat's100-Hz basis. */
 read_seqlock_excl_irqsave(&signal->stats_lock,flags);
 total=signal->utime+signal->stime;threads=signal->nr_threads;
 for_each_thread(task,thread){
  task_cputime(thread,&user,&system);total+=user+system;
 }
 read_sequnlock_excl_irqrestore(&signal->stats_lock,flags);
 rcu_read_unlock();
 return scnprintf(buffer,PAGE_SIZE,"pid=%u threads=%u raw_cpu_ns=%llu\n",server_pid,threads,total);
}
static const struct kernel_param_ops cpu_ops={.get=server_cpu};
module_param_cb(server_cpu,&cpu_ops,NULL,0400);

static void restore(void)
{
 u32 status;
 writel(0,vp);writel(saved[1],vp+8);writel(saved[2],vp+12);
 writel(BIT(31)|BIT(30),vp+4);
 readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 writel(saved[3],vp+0x64);writel(saved[4],vp+0x78);
 writel((readl(dma+0x14)&~(3U<<22))|(saved[0]&(3U<<22)),dma+0x14);
}

static int __init initialize(void)
{
 u32 status;
 int ret;
 lan=dev_get_by_name(&init_net,"lan4");
 radio=dev_get_by_name(&init_net,"ap5");
 bridge=dev_get_by_name(&init_net,"br-lan");
 if(!lan || !radio || !bridge){ret=-ENODEV;goto put;}
 dma=ioremap(0x4f7001000ULL,0x1000);
 if(!dma){ret=-ENOMEM;goto put;}
 vp=dma+0x100+11*0xa0;
 if(readl(vp)){ret=-EBUSY;goto unmap;}
 memory=dma_alloc_coherent(lan->dev.parent,128*1024,&address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 saved[0]=readl(dma+0x14);saved[1]=readl(vp+8);saved[2]=readl(vp+12);
 saved[3]=readl(vp+0x64);saved[4]=readl(vp+0x78);saved[5]=readl(vp+0x1c);
 producer=saved[5]&63;
 memset(memory,0,128*1024);
 for(unsigned int i=0;i<64;i++)
  put_unaligned_le32(lower_32_bits(address)+512+i*1536,memory+i*8);
 writel(0,vp+0x64);writel(2,vp+8);writel(lower_32_bits(address)+6,vp+12);
 writel(BIT(31)|BIT(30),vp+4);
 ret=readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 if(ret)goto restore_dma;
 writel((saved[0]&~(3U<<22))|(1U<<22),dma+0x14);
 writel(BIT(9),vp+0x78);
 feed=alloc_etherdev(0);
 if(!feed){ret=-ENOMEM;goto restore_dma;}
 strscpy(feed->name,"ponfeed%d",IFNAMSIZ);
 feed->netdev_ops=&operations;feed->mtu=1492;
 eth_hw_addr_set(feed,(const u8 *)"\x02\x55\x46\x50\x4f\x4e");
 hrtimer_setup(&timer,tick,CLOCK_MONOTONIC,HRTIMER_MODE_REL);
 ret=register_netdev(feed);
 if(ret)goto free_netdev;
 pr_info("PON_TCP_FEED %s VP11 source7 PPPoE SID4660 TCP5206->55000 ready, dma=%pad\n",feed->name,&address);
 return 0;
free_netdev:free_netdev(feed);
restore_dma:restore();dma_free_coherent(lan->dev.parent,128*1024,memory,address);
unmap:iounmap(dma);
put:if(bridge)dev_put(bridge);if(radio)dev_put(radio);if(lan)dev_put(lan);return ret;
}

static void __exit finish(void)
{
 unregister_netdev(feed);free_netdev(feed);restore();
 dma_free_coherent(lan->dev.parent,128*1024,memory,address);iounmap(dma);
 dev_put(bridge);dev_put(radio);dev_put(lan);
}
module_init(initialize);
module_exit(finish);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Genuine TCP PPPoE NI ingress feed for TYFZA1 Wi-Fi diagnosis");
