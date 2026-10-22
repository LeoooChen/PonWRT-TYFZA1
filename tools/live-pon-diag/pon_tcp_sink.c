// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic CPU sink for a real TCP test, including its bidirectional ACKs.
 * The FE forwards to CPU0;
 * this deliberately excludes physical PON egress and PPPoE encapsulation. */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/rtnetlink.h>
#include <linux/unaligned.h>
#include <net/flow_offload.h>
#include <net/gro.h>
#include "rtl9617c-ni.h"
static struct net_device *sink,*radio,*lan,*bridge;
static struct rtl9617c_ni_wifi_endpoint endpoint;
static struct flow_block block;
static struct flow_block_cb *callback;
static struct flow_rule *rule;
static struct flow_dissector dissector;
static int identity;
static u64 packets,bytes,received,received_bytes;
static u8 first[32];
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
static u64 stack_skbs,gro_frames,gro_skbs;
static rx_handler_result_t count_stack(struct sk_buff **pskb)
{
 unsigned int segments=skb_shinfo(*pskb)->gso_segs;
 stack_skbs++;
 if(segments>1){gro_skbs++;gro_frames+=segments;}
 return RX_HANDLER_PASS;
}
struct sink_key {
 struct flow_dissector_key_control control;
 struct flow_dissector_key_meta meta;
 struct flow_dissector_key_basic basic;
 struct flow_dissector_key_ipv4_addrs ipv4;
 struct flow_dissector_key_ports ports;
};
static struct sink_key key[2],mask;
static u64 flow_packets[2];
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
 skb->dev=sink;skb->protocol=eth_type_trans(skb,sink);
 skb_unset_transport_header(skb);
 napi_gro_receive(ni_napi,skb);
}
static netdev_tx_t discard(struct sk_buff *skb,struct net_device *dev)
{dev_kfree_skb_any(skb);return NETDEV_TX_OK;}
static const struct net_device_ops ops={.ndo_start_xmit=discard};
static int command(enum flow_cls_command cmd)
{
 for(unsigned int i=0;i<2;i++){
  struct flow_cls_offload cls={.command=cmd,.cookie=(unsigned long)&key[i],.rule=rule,
   .common={.protocol=cpu_to_be16(ETH_P_IP),.skip_sw=true}};
  int ret;rule->match.key=&key[i];
  ret=callback->cb(TC_SETUP_CLSFLOWER,&cls,callback->cb_priv);
  if(ret)return ret;
  if(cmd==FLOW_CLS_STATS){packets+=cls.stats.pkts;bytes+=cls.stats.bytes;flow_packets[i]+=cls.stats.pkts;}
 }
 return 0;
}
static int stats(char *buf,const struct kernel_param *param)
{
 int ret;rtnl_lock();ret=command(FLOW_CLS_STATS);rtnl_unlock();
 return scnprintf(buf,PAGE_SIZE,"ret=%d FE_packets=%llu FE_bytes=%llu received=%llu received_bytes=%llu id=%d stack_skbs=%llu gro_skbs=%llu gro_frames=%llu first=%*phN flow55000=%llu flow55001=%llu\n",
 ret,packets,bytes,received,received_bytes,identity,stack_skbs,gro_skbs,gro_frames,32,first,flow_packets[0],flow_packets[1]);
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
static int __init initialize(void)
{
 struct flow_block_offload off={.command=FLOW_BLOCK_BIND,
  .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,.net=&init_net,.block=&block};
 u8 ethernet[12];int ret;
 radio=dev_get_by_name(&init_net,"ap5");lan=dev_get_by_name(&init_net,"lan4");
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
 identity=rtl9617c_ni_wifi_attach(lan->dev.parent,&endpoint);
 if(identity<0){ret=identity;goto unregister_handler;}
 INIT_LIST_HEAD(&block.cb_list);INIT_LIST_HEAD(&off.cb_list);
 ret=rtl9617c_ni_fe_setup_tc(lan->dev.parent,radio,TC_SETUP_FT,&off);
 if(ret)goto detach;
 callback=list_first_entry(&off.cb_list,struct flow_block_cb,list);
 list_splice_tail_init(&off.cb_list,&block.cb_list);
 rule=flow_rule_alloc(4);if(!rule){ret=-ENOMEM;goto unbind;}
 skb_flow_dissector_init(&dissector,keys,ARRAY_SIZE(keys));
 rule->match.dissector=&dissector;rule->match.key=&key[0];rule->match.mask=&mask;
 key[0].meta.ingress_ifindex=radio->ifindex;key[0].basic.n_proto=htons(ETH_P_IP);
 key[0].basic.ip_proto=IPPROTO_TCP;mask.basic.n_proto=htons(65535);mask.basic.ip_proto=255;
 key[0].ipv4.src=htonl(0xc0a801d4);key[0].ipv4.dst=htonl(0xc6120001);
 mask.ipv4.src=mask.ipv4.dst=htonl(~0U);
 key[0].ports.src=htons(55000);key[0].ports.dst=htons(5206);
 mask.ports.src=mask.ports.dst=htons(65535);
 memcpy(ethernet,bridge->dev_addr,6);memcpy(ethernet+6,"\x06\x7a\xfb\x78\xb2\x87",6);
 for(unsigned int i=0;i<3;i++)rule->action.entries[i]=(struct flow_action_entry){
  .id=FLOW_ACTION_MANGLE,.mangle={.htype=FLOW_ACT_MANGLE_HDR_TYPE_ETH,.offset=i*4,
   .mask=0,.val=get_unaligned((u32 *)(ethernet+i*4))}};
 rule->action.entries[3]=(struct flow_action_entry){.id=FLOW_ACTION_REDIRECT,.dev=sink};
 /* iperf3 --bidir uses55000 for upload and55001 for download ACKs.
  * Both must use the NI owner's GRO/RPS context instead of inline TCP RX. */
 key[1]=key[0];key[1].ports.src=htons(55001);
 ret=command(FLOW_CLS_REPLACE);if(ret)goto free_rule;
 rtnl_unlock();return 0;
free_rule:kfree(rule);
unbind:unbind();
detach:rtl9617c_ni_wifi_detach(lan->dev.parent,identity);rtl9617c_ni_fe_remove_port(lan->dev.parent,sink);sink_gro_flush();
unregister_handler:netdev_rx_handler_unregister(sink);
unregister_sink:unregister_netdevice(sink);
unlock:rtnl_unlock();free_netdev(sink);
put:if(bridge)dev_put(bridge);if(lan)dev_put(lan);if(radio)dev_put(radio);return ret;
}
static void __exit finish(void)
{
 rtnl_lock();command(FLOW_CLS_DESTROY);unbind();kfree(rule);
 rtl9617c_ni_wifi_detach(lan->dev.parent,identity);rtl9617c_ni_fe_remove_port(lan->dev.parent,sink);
 sink_gro_flush();netdev_rx_handler_unregister(sink);
 unregister_netdevice(sink);rtnl_unlock();free_netdev(sink);
 dev_put(bridge);dev_put(lan);dev_put(radio);
}
module_init(initialize);module_exit(finish);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Single TCP uplink FE to CPU diagnostic sink");
