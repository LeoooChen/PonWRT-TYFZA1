// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic CPU sink for genuine iperf3 TCP or UDP packets.
 * The FE encapsulates PPPoE and forwards to CPU0; the adapter removes the
 * header for iperf3. This does not exercise the physical PON egress queue. */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/rtnetlink.h>
#include <linux/unaligned.h>
#include <linux/inet.h>
#include <linux/percpu.h>
#include <net/flow_offload.h>
#include <net/gro.h>
#include "rtl9617c-ni.h"
static struct net_device *sink,*radio,*lan,*bridge;
static struct rtl9617c_ni_wifi_endpoint endpoint;
static struct flow_block block;
static struct flow_block_cb *callback;
static struct flow_rule *rule;
static struct flow_dissector dissector;
static int identity, init_error;
module_param(init_error,int,0400);
static u64 packets,bytes,received,received_bytes,bad_pppoe;
static u8 first[32];
static char *peer_ip, *peer_mac, *input = "ap5";
static bool udp,udp_gro;
module_param(peer_ip,charp,0400);
module_param(peer_mac,charp,0400);
module_param(input,charp,0400);
module_param(udp,bool,0400);
module_param(udp_gro,bool,0400);
/* The callback runs within the NI owner's serialized poll. Use that same
 * GRO context across CPU migration, as ca_ni_rx_napi does for CPU delivery. */
static struct napi_struct *ni_napi;
static void sink_gro_flush(void)
{
 /* The endpoint has detached. Flush its last SKBs before freeing its netdev. */
 napi_disable(ni_napi);local_bh_disable();
 napi_gro_flush(ni_napi,false);
 netif_receive_skb_list(&ni_napi->gro.rx_list);
 ni_napi->gro.rx_count=0;
 local_bh_enable();napi_enable(ni_napi);napi_schedule(ni_napi);
}
static DEFINE_PER_CPU(u64, stack_skbs);
static DEFINE_PER_CPU(u64, gro_frames);
static DEFINE_PER_CPU(u64, gro_skbs);
static rx_handler_result_t count_stack(struct sk_buff **pskb)
{
 unsigned int segments=skb_shinfo(*pskb)->gso_segs;
 this_cpu_inc(stack_skbs);
 if(segments>1){this_cpu_inc(gro_skbs);this_cpu_add(gro_frames,segments);}
 return RX_HANDLER_PASS;
}
struct sink_key {
 struct flow_dissector_key_control control;
 struct flow_dissector_key_meta meta;
 struct flow_dissector_key_basic basic;
 struct flow_dissector_key_ipv4_addrs ipv4;
 struct flow_dissector_key_ports ports;
};
static struct sink_key key[4],mask;
static u64 flow_packets[4];
static bool flow_installed[4], attached;
static DEFINE_MUTEX(release_lock);
static const struct flow_dissector_key keys[]={
 {FLOW_DISSECTOR_KEY_CONTROL,offsetof(struct sink_key,control)},
 {FLOW_DISSECTOR_KEY_META,offsetof(struct sink_key,meta)},
 {FLOW_DISSECTOR_KEY_BASIC,offsetof(struct sink_key,basic)},
 {FLOW_DISSECTOR_KEY_IPV4_ADDRS,offsetof(struct sink_key,ipv4)},
 {FLOW_DISSECTOR_KEY_PORTS,offsetof(struct sink_key,ports)},
};
static void receive(struct rtl9617c_ni_wifi_endpoint *ep,struct sk_buff *skb,bool forwarded)
{
 if(!received)memcpy(first,skb->data,min_t(unsigned int,sizeof(first),skb->len));
 received++;received_bytes+=skb->len;
 /* FE emitted a PPPoE session frame. Validate and remove only its eight-byte
  * header so the normal IP stack can deliver the payload to iperf3. */
 if(skb->len<ETH_HLEN+8 || get_unaligned_be16(skb->data+12)!=ETH_P_PPP_SES ||
    skb->data[14]!=0x11 || skb->data[15]!=0 ||
    get_unaligned_be16(skb->data+16)!=0x1234 ||
    get_unaligned_be16(skb->data+20)!=0x21){
  bad_pppoe++;dev_kfree_skb_any(skb);return;
 }
 memmove(skb->data+8,skb->data,12);skb_pull(skb,8);
 put_unaligned_be16(ETH_P_IP,skb->data+12);
 /* PPPoE removal does not change the inner checksum verified by NI. */
 if(skb->ip_summed!=CHECKSUM_UNNECESSARY)skb->ip_summed=CHECKSUM_NONE;
 skb->dev=sink;skb->protocol=eth_type_trans(skb,sink);
 skb_unset_transport_header(skb);
 /* Default UDP sockets benefit from RPS before checksum work. With --gsro,
  * use the NI GRO context so the socket receives aggregated datagrams. */
 if(udp && !udp_gro)netif_receive_skb(skb);
 else napi_gro_receive(ni_napi,skb);
}
static netdev_tx_t discard(struct sk_buff *skb,struct net_device *dev)
{dev_kfree_skb_any(skb);return NETDEV_TX_OK;}
static const struct net_device_ops ops={.ndo_start_xmit=discard};
static int command(enum flow_cls_command cmd)
{
 for(unsigned int i=0;i<ARRAY_SIZE(key);i++){
  struct flow_cls_offload cls={.command=cmd,.cookie=(unsigned long)&key[i],.rule=rule,
   .common={.protocol=cpu_to_be16(ETH_P_IP),.skip_sw=true}};
  int ret;
  if(cmd!=FLOW_CLS_REPLACE && !flow_installed[i])continue;
  rule->match.key=&key[i];
  ret=callback->cb(TC_SETUP_CLSFLOWER,&cls,callback->cb_priv);
  if(ret){
   if(cmd==FLOW_CLS_REPLACE) while(i--){
    cls.command=FLOW_CLS_DESTROY;cls.cookie=(unsigned long)&key[i];
    rule->match.key=&key[i];
    if(!callback->cb(TC_SETUP_CLSFLOWER,&cls,callback->cb_priv))flow_installed[i]=false;
   }
   return ret;
  }
  if(cmd==FLOW_CLS_REPLACE)flow_installed[i]=true;
  if(cmd==FLOW_CLS_DESTROY)flow_installed[i]=false;
  if(cmd==FLOW_CLS_STATS){packets+=cls.stats.pkts;bytes+=cls.stats.bytes;flow_packets[i]+=cls.stats.pkts;}
 }
 return 0;
}
static int stats(char *buf,const struct kernel_param *param)
{
 u64 stack_total=0,gro_total=0,frames_total=0;
 int cpu,ret;rtnl_lock();ret=command(FLOW_CLS_STATS);rtnl_unlock();
 /* RPS delivers on multiple CPUs; aggregate their independent counters. */
 for_each_possible_cpu(cpu){
  stack_total+=READ_ONCE(per_cpu(stack_skbs,cpu));
  gro_total+=READ_ONCE(per_cpu(gro_skbs,cpu));
  frames_total+=READ_ONCE(per_cpu(gro_frames,cpu));
 }
 return scnprintf(buf,PAGE_SIZE,"ret=%d FE_packets=%llu FE_bytes=%llu received=%llu received_bytes=%llu bad_pppoe=%llu id=%d stack_skbs=%llu gro_skbs=%llu gro_frames=%llu first=%*phN flow55000=%llu flow55001=%llu flow55002=%llu flow55003=%llu\n",
 ret,packets,bytes,received,received_bytes,bad_pppoe,identity,stack_total,gro_total,frames_total,32,first,flow_packets[0],flow_packets[1],flow_packets[2],flow_packets[3]);
}
static const struct kernel_param_ops statops={.get=stats};
module_param_cb(stats,&statops,NULL,0400);
static void unbind(void)
{
 struct flow_block_offload off={.command=FLOW_BLOCK_UNBIND,
  .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,.net=&init_net,.block=&block};
 INIT_LIST_HEAD(&off.cb_list);
 rtl9617c_ni_fe_setup_tc(lan->dev.parent,radio,TC_SETUP_FT,&off);
 list_del(&callback->list);flow_block_cb_free(callback);
}

/* A failed NI drain leaves the endpoint reachable by DMA/NAPI. Keep this
 * module pinned until explicit release succeeds, so rmmod cannot free it. */
static int release_set(const char *value,const struct kernel_param *param)
{
 bool requested;int ret;
 ret=kstrtobool(value,&requested);if(ret || !requested)return -EINVAL;
 mutex_lock(&release_lock);
 if(!attached){mutex_unlock(&release_lock);return 0;}
 rtnl_lock();
 ret=command(FLOW_CLS_DESTROY);if(ret)goto unlock;
 ret=rtl9617c_ni_wifi_detach(lan->dev.parent,identity);if(ret)goto unlock;
 unbind();kfree(rule);rtl9617c_ni_fe_remove_port(lan->dev.parent,sink);
 sink_gro_flush();netdev_rx_handler_unregister(sink);unregister_netdevice(sink);
 rtnl_unlock();free_netdev(sink);dev_put(bridge);dev_put(lan);dev_put(radio);
 attached=false;mutex_unlock(&release_lock);module_put(THIS_MODULE);return 0;
unlock:
 rtnl_unlock();mutex_unlock(&release_lock);return ret;
}
static const struct kernel_param_ops release_ops={.set=release_set};
module_param_cb(release,&release_ops,NULL,0200);
static int __init initialize(void)
{
 struct flow_block_offload off={.command=FLOW_BLOCK_BIND,
  .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,.net=&init_net,.block=&block};
 u8 ethernet[12],peer[ETH_ALEN];__be32 peer_address;int ret;
 if(!peer_mac || !peer_ip || !mac_pton(peer_mac,peer) ||
    !in4_pton(peer_ip,-1,(u8 *)&peer_address,-1,NULL))return -EINVAL;
 radio=dev_get_by_name(&init_net,input);lan=dev_get_by_name(&init_net,"lan4");
 bridge=dev_get_by_name(&init_net,"br-lan");
 if(!radio||!lan||!bridge){ret=-ENODEV;goto put;}
 sink=alloc_etherdev(0);if(!sink){ret=-ENOMEM;goto put;}
 strscpy(sink->name,"ponsink%d",IFNAMSIZ);sink->netdev_ops=&ops;
 eth_hw_addr_set(sink,bridge->dev_addr);sink->features|=NETIF_F_GRO;
 endpoint.netdev=sink;endpoint.receive=receive;
 rtnl_lock();ret=register_netdevice(sink);if(ret)goto unlock;
 ni_napi=list_first_entry(&((struct net_device *)dev_get_drvdata(lan->dev.parent))->napi_list,
  struct napi_struct,dev_list);
 ret=netdev_rx_handler_register(sink,count_stack,NULL);if(ret)goto unregister_sink;
 ret=dev_open(sink,NULL);if(ret)goto unregister_handler;
 netif_carrier_on(sink);
 INIT_LIST_HEAD(&block.cb_list);INIT_LIST_HEAD(&off.cb_list);
 ret=rtl9617c_ni_fe_setup_tc(lan->dev.parent,radio,TC_SETUP_FT,&off);
 if(ret)goto unregister_handler;
 callback=list_first_entry(&off.cb_list,struct flow_block_cb,list);
 list_splice_tail_init(&off.cb_list,&block.cb_list);
 rule=flow_rule_alloc(5);if(!rule){ret=-ENOMEM;goto unbind;}
 skb_flow_dissector_init(&dissector,keys,ARRAY_SIZE(keys));
 rule->match.dissector=&dissector;rule->match.key=&key[0];rule->match.mask=&mask;
 key[0].meta.ingress_ifindex=radio->ifindex;key[0].basic.n_proto=htons(ETH_P_IP);
 key[0].basic.ip_proto=udp?IPPROTO_UDP:IPPROTO_TCP;mask.basic.n_proto=htons(65535);mask.basic.ip_proto=255;
 key[0].ipv4.src=peer_address;key[0].ipv4.dst=htonl(0xc612ff01);
 mask.ipv4.src=mask.ipv4.dst=htonl(~0U);
 key[0].ports.src=htons(55000);key[0].ports.dst=htons(5206);
 mask.ports.src=mask.ports.dst=htons(65535);
 memcpy(ethernet,bridge->dev_addr,6);memcpy(ethernet+6,peer,6);
 for(unsigned int i=0;i<3;i++)rule->action.entries[i]=(struct flow_action_entry){
  .id=FLOW_ACTION_MANGLE,.mangle={.htype=FLOW_ACT_MANGLE_HDR_TYPE_ETH,.offset=i*4,
   .mask=0,.val=get_unaligned((u32 *)(ethernet+i*4))}};
 rule->action.entries[3]=(struct flow_action_entry){.id=FLOW_ACTION_PPPOE_PUSH,.pppoe={.sid=0x1234,.mtu=1492}};
 rule->action.entries[4]=(struct flow_action_entry){.id=FLOW_ACTION_REDIRECT,.dev=sink};
 /* Match all four client ports accepted by the TCP feed and runners.
  * Download ACKs use the same NI GRO/RPS context as upload data. */
 for(unsigned int i=1;i<ARRAY_SIZE(key);i++){
  key[i]=key[0];key[i].ports.src=htons(55000+i);
 }
 identity=rtl9617c_ni_wifi_attach(lan->dev.parent,&endpoint);
 if(identity<0){ret=identity;goto free_rule;}
 /* Keep the endpoint and any rules whose rollback failed alive for release. */
 ret=command(FLOW_CLS_REPLACE);
 init_error=ret;
 attached=true;__module_get(THIS_MODULE);
 rtnl_unlock();return 0;
free_rule:kfree(rule);
unbind:unbind();
unregister_handler:netdev_rx_handler_unregister(sink);
unregister_sink:unregister_netdevice(sink);
unlock:rtnl_unlock();free_netdev(sink);
put:if(bridge)dev_put(bridge);if(lan)dev_put(lan);if(radio)dev_put(radio);return ret;
}
static void __exit finish(void)
{
 WARN_ON(attached);
}
module_init(initialize);module_exit(finish);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("iperf3 TCP/UDP FE uplink diagnostic endpoint");
