// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/rtnetlink.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/unaligned.h>
#include <net/flow_offload.h>
#include <net/net_namespace.h>
extern int rtl9617c_ni_fe_add_port(struct device *,struct net_device *,unsigned int);
extern void rtl9617c_ni_fe_remove_port(struct device *,struct net_device *);
extern int rtl9617c_ni_fe_setup_tc(struct device *,struct net_device *,enum tc_setup_type,void *);
static struct net_device *sim,*radio,*lan,*bridge;
static struct flow_block block;
static struct flow_block_cb *callback;
static struct flow_rule *rule;
static struct flow_dissector dissector;
struct sim_keys {
 struct flow_dissector_key_control control;
 struct flow_dissector_key_meta meta;
 struct flow_dissector_key_basic basic;
 struct flow_dissector_key_ipv4_addrs ipv4;
 struct flow_dissector_key_ports ports;
};
static struct sim_keys key,mask;
static u64 counted_packets,counted_bytes;
static const struct flow_dissector_key keys[]={
 {FLOW_DISSECTOR_KEY_CONTROL,offsetof(struct sim_keys,control)},
 {FLOW_DISSECTOR_KEY_META,offsetof(struct sim_keys,meta)},
 {FLOW_DISSECTOR_KEY_BASIC,offsetof(struct sim_keys,basic)},
 {FLOW_DISSECTOR_KEY_IPV4_ADDRS,offsetof(struct sim_keys,ipv4)},
 {FLOW_DISSECTOR_KEY_PORTS,offsetof(struct sim_keys,ports)}
};
static unsigned char peer[6]={0x06,0x7a,0xfb,0x78,0xb2,0x87};
static int flow_command(enum flow_cls_command command)
{
 struct flow_cls_offload cls={.command=command,.cookie=(unsigned long)&key,.rule=rule,
  .common={.protocol=cpu_to_be16(ETH_P_IP),.skip_sw=true}};
 int ret=callback->cb(TC_SETUP_CLSFLOWER,&cls,callback->cb_priv);
 if(command==FLOW_CLS_STATS)pr_info("PON_SIM FE ret=%d packets=%llu bytes=%llu\n",ret,cls.stats.pkts,cls.stats.bytes);
 return ret;
}
static int sim_stats(char *buffer,const struct kernel_param *param)
{
 struct flow_cls_offload cls={.command=FLOW_CLS_STATS,.cookie=(unsigned long)&key,.rule=rule};
 rtnl_lock();
 int ret=callback->cb(TC_SETUP_CLSFLOWER,&cls,callback->cb_priv);
 counted_packets+=cls.stats.pkts;counted_bytes+=cls.stats.bytes;
 rtnl_unlock();
 return scnprintf(buffer,PAGE_SIZE,"ret=%d packets=%llu bytes=%llu lastused=%llu\n",
  ret,counted_packets,counted_bytes,cls.stats.lastused);
}
static const struct kernel_param_ops stats_ops={.get=sim_stats};
module_param_cb(stats,&stats_ops,NULL,0400);
static netdev_tx_t sim_drop(struct sk_buff *skb,struct net_device *dev)
{ dev_kfree_skb_any(skb);return NETDEV_TX_OK; }
static const struct net_device_ops sim_ops={.ndo_start_xmit=sim_drop};
static int __init sim_init(void)
{
 struct flow_block_offload off={.command=FLOW_BLOCK_BIND,
  .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,.net=&init_net,.block=&block};
 u8 ethernet[12];
 int ret;
 lan=dev_get_by_name(&init_net,"eth3");radio=dev_get_by_name(&init_net,"ap5");
 bridge=dev_get_by_name(&init_net,"br-lan");
 if(!lan || !radio)return -ENODEV;
 sim=alloc_netdev(0,"ponsim%d",NET_NAME_UNKNOWN,ether_setup);
 if(!sim)return -ENOMEM;
 sim->netdev_ops=&sim_ops;eth_hw_addr_random(sim);
 rtnl_lock();
 ret=register_netdevice(sim);if(ret)goto out;
 ret=dev_open(sim,NULL);if(ret)goto unregister;
 netif_carrier_on(sim);
 ret=rtl9617c_ni_fe_add_port(lan->dev.parent,sim,0);if(ret)goto unregister;
 INIT_LIST_HEAD(&block.cb_list);INIT_LIST_HEAD(&off.cb_list);
 ret=rtl9617c_ni_fe_setup_tc(lan->dev.parent,sim,TC_SETUP_FT,&off);
 if(ret)goto remove;
 callback=list_first_entry(&off.cb_list,struct flow_block_cb,list);
 list_splice_tail_init(&off.cb_list,&block.cb_list);
 rule=flow_rule_alloc(5);if(!rule){ret=-ENOMEM;goto unbind;}
 skb_flow_dissector_init(&dissector,keys,ARRAY_SIZE(keys));
 rule->match.dissector=&dissector;rule->match.key=&key;rule->match.mask=&mask;
 key.meta.ingress_ifindex=sim->ifindex;
 key.basic.n_proto=htons(ETH_P_IP);key.basic.ip_proto=IPPROTO_UDP;
 mask.basic.n_proto=htons(0xffff);mask.basic.ip_proto=255;
 key.ipv4.src=htonl(0xc6120001);key.ipv4.dst=htonl(0xc6120002);
 mask.ipv4.src=mask.ipv4.dst=htonl(~0U);
 key.ports.src=htons(55000);key.ports.dst=htons(55001);
 mask.ports.src=mask.ports.dst=htons(65535);
 memcpy(ethernet,peer,6);memcpy(ethernet+6,bridge->dev_addr,6);
 for(unsigned int i=0;i<3;i++)rule->action.entries[i]=(struct flow_action_entry){
  .id=FLOW_ACTION_MANGLE,.mangle={.htype=FLOW_ACT_MANGLE_HDR_TYPE_ETH,.offset=i*4,
   .mask=0,.val=get_unaligned((u32 *)(ethernet+i*4))}};
 rule->action.entries[3]=(struct flow_action_entry){.id=FLOW_ACTION_MANGLE,
  .mangle={.htype=FLOW_ACT_MANGLE_HDR_TYPE_IP4,.offset=16,.mask=0,.val=htonl(0xc0a801d4)}};
 rule->action.entries[4]=(struct flow_action_entry){.id=FLOW_ACTION_REDIRECT,.dev=radio};
 ret=flow_command(FLOW_CLS_REPLACE);if(ret)goto free_rule;
 rtnl_unlock();
 pr_info("PON_SIM PON source7 LLID0 UDP198.18.0.1:55000->198.18.0.2:55001 DNAT192.168.1.212 ap5 installed\n");
 return 0;
free_rule:kfree(rule);
unbind:
 INIT_LIST_HEAD(&off.cb_list);
 off.command=FLOW_BLOCK_UNBIND;
 rtl9617c_ni_fe_setup_tc(lan->dev.parent,sim,TC_SETUP_FT,&off);
 list_del(&callback->list);flow_block_cb_free(callback);
remove:rtl9617c_ni_fe_remove_port(lan->dev.parent,sim);
unregister:unregister_netdevice(sim);
out:rtnl_unlock();free_netdev(sim);dev_put(bridge);dev_put(radio);dev_put(lan);pr_info("PON_SIM init ret=%d\n",ret);return ret;
}
static void __exit sim_exit(void)
{
 struct flow_block_offload off={.command=FLOW_BLOCK_UNBIND,
  .binder_type=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,.net=&init_net,.block=&block};
 INIT_LIST_HEAD(&off.cb_list);
 rtnl_lock();flow_command(FLOW_CLS_STATS);flow_command(FLOW_CLS_DESTROY);
 rtl9617c_ni_fe_setup_tc(lan->dev.parent,sim,TC_SETUP_FT,&off);
 list_del(&callback->list);flow_block_cb_free(callback);
 rtl9617c_ni_fe_remove_port(lan->dev.parent,sim);unregister_netdevice(sim);
 rtnl_unlock();free_netdev(sim);kfree(rule);dev_put(bridge);dev_put(radio);dev_put(lan);
}
module_init(sim_init);
module_exit(sim_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 isolated PON tuple hardware-flow simulation");
