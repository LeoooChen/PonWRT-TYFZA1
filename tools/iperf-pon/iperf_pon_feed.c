// SPDX-License-Identifier: GPL-2.0-only
/* Feed genuine iperf3 TCP or UDP packets into the PON-source NI VP11 ABI.
 * Control traffic uses the selected output normally; data traverses the FE.
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
#include <linux/udp.h>
#include <linux/pid.h>
#include <linux/sched/cputime.h>

static struct net_device *feed,*lan,*radio,*bridge;
static void __iomem *dma,*vp,*eq_monitor;
static void *memory;
static dma_addr_t address;
static u32 saved[6],producer;
static struct hrtimer timer;
static u64 data_packets,control_packets,data_bytes,stops,wakes,doorbells;
static u64 data_prepare_ns,payload_copy_ns;
static bool pending, active;
static int init_error;
static DEFINE_MUTEX(release_lock);
module_param(init_error,int,0400);
static u64 stream_packets[4];
static u64 invalid_packets;
static char *output = "ap5";
static bool udp;
static bool eq_backpressure;
static unsigned int output_eq;
static unsigned int server_pid;
module_param(output,charp,0400);
module_param(udp,bool,0400);
module_param(eq_backpressure,bool,0400);
module_param(server_pid,uint,0600);

static unsigned int room(void)
{
 /* Keep61 outstanding requests while using the factory1024-entry ring. */
 unsigned int outstanding=(READ_ONCE(producer)-(readl(vp+0x1c)&1023))&1023;
 unsigned int available=61-min(61U,outstanding);
 if(eq_monitor){
  /* Reserve space for the NI/FE pipeline and already submitted frames.
   * This optional diagnostic throttles the source, not the PE datapath. */
  unsigned int free=readl(eq_monitor+output_eq*16)&0x3fff;
  unsigned int reserved=128+outstanding;
  available=min(available,free>reserved?free-reserved:0);
 }
 return available;
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
 u16 source = 0, destination = 0;
 bool bulk = false;
 u64 begin,copy_begin;

 if(skb->protocol==htons(ETH_P_IP)) {
  if(!udp && ip->protocol==IPPROTO_TCP) {
   source=ntohs(tcp_hdr(skb)->source);destination=ntohs(tcp_hdr(skb)->dest);
   bulk=!tcp_hdr(skb)->syn && !tcp_hdr(skb)->fin && !tcp_hdr(skb)->rst;
  } else if(udp && ip->protocol==IPPROTO_UDP) {
   source=ntohs(udp_hdr(skb)->source);destination=ntohs(udp_hdr(skb)->dest);
   bulk=true;
  }
 }
 if(!bulk || source!=5206 || destination<55000 || destination>55003){
  /* A control skb can finish a batch whose data descriptors are deferred. */
  if(pending){
   dma_wmb();writel(producer,vp+0x18);writel(1,vp+0x68);doorbells++;pending=false;
  }
  control_packets++;
  skb->dev=radio;skb_set_queue_mapping(skb,0);
  dev_queue_xmit(skb);
  return NETDEV_TX_OK;
 }
 if(skb->len<ETH_HLEN || skb->len>1506 || skb_is_gso(skb) ||
    (skb->ip_summed==CHECKSUM_PARTIAL && skb_checksum_help(skb))) {
  invalid_packets++;dev_kfree_skb_any(skb);return NETDEV_TX_OK;
 }
 begin=ktime_get_ns();
 available=room();
 if(!available){
  /* EQ pressure may stop a GSO batch before its final skb arrives. */
  if(pending){
   dma_wmb();writel(producer,vp+0x18);writel(1,vp+0x68);doorbells++;pending=false;
  }
  stops++;netif_stop_queue(dev);
  hrtimer_start(&timer,ns_to_ktime(100000),HRTIMER_MODE_REL);
  return NETDEV_TX_BUSY;
 }
 slot=producer;length=skb->len+8;frame=memory+8192+slot*1536;
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
 producer=(producer+1)&1023;
 pending=true;
 /* Publish the whole Linux TX batch; also flush before the ring fills. */
 if(!netdev_xmit_more() || available==1){
  dma_wmb();writel(producer,vp+0x18);writel(1,vp+0x68);doorbells++;pending=false;
 }
 data_packets++;data_bytes+=skb->len;
 stream_packets[destination-55000]++;
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
  "data_packets=%llu data_bytes=%llu control_packets=%llu invalid_packets=%llu stops=%llu wakes=%llu doorbells=%llu data_prepare_ns=%llu payload_copy_ns=%llu producer=%u consumer=%u room=%u stream_packets=%llu,%llu,%llu,%llu eq_backpressure=%u\n",
  data_packets,data_bytes,control_packets,invalid_packets,stops,wakes,doorbells,data_prepare_ns,payload_copy_ns,producer,readl(vp+0x1c)&1023,room(),
  stream_packets[0],stream_packets[1],stream_packets[2],stream_packets[3],eq_backpressure);
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

/* Keep DMA memory and the module alive if the VP reset does not complete. */
static int restore(void)
{
 u32 status;int ret;
 writel(0,vp);writel(BIT(31)|BIT(30),vp+4);
 ret=readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 if(ret)return ret;
 writel(saved[1],vp+8);writel(saved[2],vp+12);
 writel(saved[3],vp+0x64);writel(saved[4],vp+0x78);
 writel((readl(dma+0x14)&~(3U<<22))|(saved[0]&(3U<<22)),dma+0x14);
 return 0;
}
static int release_set(const char *value,const struct kernel_param *param)
{
 bool requested;int ret;
 ret=kstrtobool(value,&requested);if(ret || !requested)return -EINVAL;
 mutex_lock(&release_lock);
 if(!active){mutex_unlock(&release_lock);return 0;}
 if(feed){unregister_netdev(feed);free_netdev(feed);feed=NULL;}
 ret=restore();if(ret){mutex_unlock(&release_lock);return ret;}
 dma_free_coherent(lan->dev.parent,1024*(1536+8),memory,address);iounmap(dma);
 if(eq_monitor){iounmap(eq_monitor);eq_monitor=NULL;}
 dev_put(bridge);dev_put(radio);dev_put(lan);active=false;
 mutex_unlock(&release_lock);module_put(THIS_MODULE);return 0;
}
static const struct kernel_param_ops release_ops={.set=release_set};
module_param_cb(release,&release_ops,NULL,0200);

static int __init initialize(void)
{
 u32 status;
 int ret;
 lan=dev_get_by_name(&init_net,"lan4");
 radio=dev_get_by_name(&init_net,output);
 bridge=dev_get_by_name(&init_net,"br-lan");
 if(!lan || !radio || !bridge){ret=-ENODEV;goto put;}
 if(eq_backpressure){
  if(!strcmp(output,"ap5"))output_eq=6;
  else if(!strcmp(output,"ap2"))output_eq=7;
  else {ret=-EINVAL;goto put;}
  eq_monitor=ioremap(0x4f43066dcULL,16*16);
  if(!eq_monitor){ret=-ENOMEM;goto put;}
 }
 dma=ioremap(0x4f7001000ULL,0x1000);
 if(!dma){ret=-ENOMEM;goto unmap_eq;}
 vp=dma+0x100+11*0xa0;
 if(readl(vp)){ret=-EBUSY;goto unmap;}
 memory=dma_alloc_coherent(lan->dev.parent,1024*(1536+8),&address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 saved[0]=readl(dma+0x14);saved[1]=readl(vp+8);saved[2]=readl(vp+12);
 saved[3]=readl(vp+0x64);saved[4]=readl(vp+0x78);saved[5]=readl(vp+0x1c);
 producer=saved[5]&1023;
 memset(memory,0,1024*(1536+8));
 for(unsigned int i=0;i<1024;i++)
  put_unaligned_le32(lower_32_bits(address)+8192+i*1536,memory+i*8);
 active=true;__module_get(THIS_MODULE);
 writel(0,vp+0x64);writel(2,vp+8);writel(lower_32_bits(address)+10,vp+12);
 writel(BIT(31)|BIT(30),vp+4);
 ret=readl_poll_timeout(vp+4,status,!(status&BIT(31)),1,100000);
 if(ret)goto retain;
 writel((saved[0]&~(3U<<22))|(1U<<22),dma+0x14);
 writel(BIT(9),vp+0x78);
 feed=alloc_etherdev(0);
 if(!feed){ret=-ENOMEM;goto retain;}
 strscpy(feed->name,"ponfeed%d",IFNAMSIZ);
 feed->netdev_ops=&operations;feed->mtu=1492;
 eth_hw_addr_set(feed,(const u8 *)"\x02\x55\x46\x50\x4f\x4e");
 hrtimer_setup(&timer,tick,CLOCK_MONOTONIC,HRTIMER_MODE_REL);
 ret=register_netdev(feed);
 if(ret){free_netdev(feed);feed=NULL;goto retain;}
 pr_info("IPERF_PON_FEED %s VP11 source7 PPPoE SID4660 protocol=%s output=%s dma=%pad\n",
  feed->name,udp?"udp":"tcp",output,&address);
 return 0;
retain:init_error=ret;return 0;
unmap:iounmap(dma);
unmap_eq:if(eq_monitor){iounmap(eq_monitor);eq_monitor=NULL;}
put:if(bridge)dev_put(bridge);if(radio)dev_put(radio);if(lan)dev_put(lan);return ret;
}

static void __exit finish(void)
{
 WARN_ON(active);
}
module_init(initialize);
module_exit(finish);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("iperf3 TCP/UDP PON ingress feed");
