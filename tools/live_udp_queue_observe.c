#include <linux/module.h>
#include <linux/netfilter.h>
#include <linux/netfilter_ipv4.h>
#include <linux/ip.h>
#include <linux/ktime.h>
#include <linux/spinlock.h>
#include <linux/udp.h>
#include <net/net_namespace.h>
#include <net/udp.h>
static unsigned int port=5210;
module_param(port,uint,0444);
static atomic64_t rcvbuf_value,max_rmem;
static atomic64_t packets, bytes, small, medium, big, giant, total_true, max_true, max_head;
static DEFINE_SPINLOCK(timing_lock);
static u64 last_ns,max_gap,window_start,window_count,max_burst;
static unsigned int observe(void *priv,struct sk_buff *skb,const struct nf_hook_state *state)
{
 const struct iphdr *ip=ip_hdr(skb);struct udphdr udp,*u;
 if(ip->protocol!=IPPROTO_UDP)return NF_ACCEPT;
 u=skb_header_pointer(skb,ip->ihl*4,sizeof(udp),&udp);
 if(!u||ntohs(u->dest)!=port||ntohs(u->len)!=1208)return NF_ACCEPT;
 {unsigned long f;u64 now=ktime_get_ns();spin_lock_irqsave(&timing_lock,f);if(last_ns)max_gap=max(max_gap,now-last_ns);last_ns=now;if(now-window_start>=1000000){window_start=now;window_count=0;}window_count++;max_burst=max(max_burst,window_count);spin_unlock_irqrestore(&timing_lock,f);}
 {struct sock *sk;rcu_read_lock();sk=udp4_lib_lookup(&init_net,ip->saddr,u->source,ip->daddr,u->dest,state->in?state->in->ifindex:0);if(sk){u32 m=atomic_read(&sk->sk_rmem_alloc);atomic64_set(&rcvbuf_value,READ_ONCE(sk->sk_rcvbuf));if(m>atomic64_read(&max_rmem))atomic64_set(&max_rmem,m);sock_gen_put(sk);}rcu_read_unlock();}
 atomic64_inc(&packets);atomic64_add(skb->len,&bytes);atomic64_add(skb->truesize,&total_true);
 if(skb->truesize<=2048)atomic64_inc(&small);
 else if(skb->truesize<=4096)atomic64_inc(&medium);
 else if(skb->truesize<=16384)atomic64_inc(&big);
 else atomic64_inc(&giant);
 if(skb->truesize>atomic64_read(&max_true))atomic64_set(&max_true,skb->truesize);
 if(skb_headroom(skb)>atomic64_read(&max_head))atomic64_set(&max_head,skb_headroom(skb));
 return NF_ACCEPT;
}
static int stats_get(char *b,const struct kernel_param *p){return scnprintf(b,PAGE_SIZE,"packets=%lld bytes=%lld truesize_sum=%lld <=2k=%lld <=4k=%lld <=16k=%lld >16k=%lld max_true=%lld max_headroom=%lld max_gap_us=%llu max_1ms_burst=%llu socket_rcvbuf=%lld max_rmem=%lld\n",atomic64_read(&packets),atomic64_read(&bytes),atomic64_read(&total_true),atomic64_read(&small),atomic64_read(&medium),atomic64_read(&big),atomic64_read(&giant),atomic64_read(&max_true),atomic64_read(&max_head),max_gap/1000,max_burst,atomic64_read(&rcvbuf_value),atomic64_read(&max_rmem));}
static const struct kernel_param_ops stats_ops={.get=stats_get};
module_param_cb(stats,&stats_ops,NULL,0444);
static struct nf_hook_ops hook={.hook=observe,.pf=NFPROTO_IPV4,.hooknum=NF_INET_LOCAL_IN,.priority=NF_IP_PRI_LAST};
static int __init start(void){return nf_register_net_hook(&init_net,&hook);}
static void __exit stop(void){nf_unregister_net_hook(&init_net,&hook);}
module_init(start);module_exit(stop);MODULE_LICENSE("GPL");MODULE_DESCRIPTION("Read-only UDP skb memory observation");
