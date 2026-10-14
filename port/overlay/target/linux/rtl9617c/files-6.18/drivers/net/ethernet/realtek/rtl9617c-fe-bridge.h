/* SPDX-License-Identifier: GPL-2.0-only */
/* Linux bridge decisions feed the original MAC/VLAN Hash and packet editor. */
#include <linux/netfilter.h>
#include <linux/netfilter_bridge.h>
#include <linux/netfilter_netdev.h>
#include <linux/rtnetlink.h>
#include <linux/spinlock.h>
#include <net/net_namespace.h>
#include <net/switchdev.h>

#define FE_BRIDGE_PENDING 256

struct fe_bridge_packet {
 struct net *net;
 struct rtl9617c_bridge_frame input, output;
 unsigned int bridge, ingress, egress;
 u16 vid;
};

struct fe_bridge_wire_port {
 struct list_head list;
 struct nf_hook_ops hook;
};

struct rtl_fe_bridge {
 struct rtl_fe *fe;
 struct delayed_work work;
 struct notifier_block netdev, switchdev, blocking;
 struct pernet_operations pernet;
 struct nf_hook_ops hooks[2];
 spinlock_t pending_lock;
 unsigned int read, write;
 atomic_t epoch, flush;
 struct list_head wire_ports;
 bool stopping;
 struct fe_bridge_packet pending[FE_BRIDGE_PENDING];
};

/* There is one NI/FE complex on RTL9617C. Namespace hooks share that owner. */
static struct rtl_fe_bridge *fe_bridge_owner;

static struct net_device *fe_bridge_physical(struct net_device *dev,
                                           struct rtl9617c_bridge_frame *frame,u32 priority)
{
 while(is_vlan_dev(dev)) {
  if(frame->tags==2)return NULL;
  frame->tag[1]=frame->tag[0];
  frame->tag[0]=(struct rtl9617c_bridge_tag){.tci=vlan_dev_vlan_id(dev)|vlan_dev_get_egress_qos_mask(dev,priority),
   .protocol=ntohs(vlan_dev_vlan_proto(dev))};
  frame->tags++;
  dev=vlan_dev_real_dev(dev);
 }
 return dev;
}

static int fe_bridge_frame(struct sk_buff *skb,struct rtl9617c_bridge_frame *frame)
{
 struct vlan_hdr storage,*vlan;
 __be16 protocol=eth_hdr(skb)->h_proto;
 unsigned int offset=0;

 ether_addr_copy(frame->source,eth_hdr(skb)->h_source);
 ether_addr_copy(frame->destination,eth_hdr(skb)->h_dest);
 if(!is_valid_ether_addr(frame->source) || !is_valid_ether_addr(frame->destination))return -EINVAL;
 if(skb_vlan_tag_present(skb)) {
  frame->tag[0]=(struct rtl9617c_bridge_tag){.tci=skb_vlan_tag_get(skb),
   .protocol=ntohs(skb->vlan_proto)};
  frame->tags=1;
 }
 while(eth_type_vlan(protocol)) {
  if(frame->tags==2)return -E2BIG;
  vlan=skb_header_pointer(skb,offset,sizeof(storage),&storage);
  if(!vlan)return -EINVAL;
  frame->tag[frame->tags++]=(struct rtl9617c_bridge_tag){.tci=ntohs(vlan->h_vlan_TCI),
   .protocol=ntohs(protocol)};
  protocol=vlan->h_vlan_encapsulated_proto;
  offset+=sizeof(storage);
 }
 frame->protocol=ntohs(protocol);
 if(protocol==htons(ETH_P_PPP_SES)) {
  u8 storage[8],*pppoe=skb_header_pointer(skb,offset,sizeof(storage),storage);
  if(!pppoe || pppoe[0]!=0x11 || pppoe[1])return -EINVAL;
  /* LCP, authentication and keepalive traffic continue to visit Linux. */
  if(get_unaligned_be16(pppoe+6)!=PPP_IP && get_unaligned_be16(pppoe+6)!=PPP_IPV6)return -EOPNOTSUPP;
  frame->session=get_unaligned_be16(pppoe+2);
  frame->ppp_protocol=get_unaligned_be16(pppoe+6);
 }
 return 0;
}

static unsigned int fe_bridge_wire(void *private,struct sk_buff *skb,
                                  const struct nf_hook_state *state)
{
 struct rtl_fe_bridge *bridge=private;
 struct rtl9617c_bridge_frame frame={},*saved;
 const struct rtl9617c_bridge_frame *received;

 if(!rtl_fe_enabled(bridge->fe) || skb_is_gso(skb) || fe_bridge_frame(skb,&frame))return NF_ACCEPT;
 frame.ingress=state->in->ifindex;frame.epoch=atomic_read(&bridge->epoch);
 received=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(received && received->pon_gem) {
  frame.pon_gem=received->pon_gem;frame.pon_generation=received->pon_generation;
  frame.tags=received->tags;memcpy(frame.tag,received->tag,sizeof(frame.tag));
 }
 saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(saved)*saved=frame;
 return NF_ACCEPT;
}

static void fe_bridge_wire_port(struct rtl_fe_bridge *bridge,struct net_device *dev,bool add)
{
 struct fe_bridge_wire_port *port;
 int ret;

 list_for_each_entry(port,&bridge->wire_ports,list)
  if(port->hook.dev==dev) {
   if(add)return;
   nf_unregister_net_hook(dev_net(dev),&port->hook);
   list_del(&port->list);kfree(port);return;
  }
 if(!add)return;
 mutex_lock(&bridge->fe->lock);
 ret=fe_port(bridge->fe,dev)!=NULL;
 mutex_unlock(&bridge->fe->lock);
 if(!ret)return;
 port=kzalloc(sizeof(*port),GFP_KERNEL);
 if(!port)return;
 port->hook=(struct nf_hook_ops){.hook=fe_bridge_wire,.priv=bridge,.dev=dev,
  .pf=NFPROTO_NETDEV,.hooknum=NF_NETDEV_INGRESS,.priority=INT_MIN+1};
 ret=nf_register_net_hook(dev_net(dev),&port->hook);
 if(ret){kfree(port);return;}
 list_add_tail(&port->list,&bridge->wire_ports);
}

static unsigned int fe_bridge_capture(void *private,struct sk_buff *skb,
                                     const struct nf_hook_state *state)
{
 struct rtl_fe_bridge *bridge=private;
 struct rtl9617c_bridge_frame frame={},*saved;
 const struct rtl9617c_bridge_frame *wire;
 struct net_device *brdev;
 u16 protocol;

 if(!rtl_fe_enabled(bridge->fe) || skb_is_gso(skb) || !state->in ||
    fe_bridge_frame(skb,&frame))return NF_ACCEPT;
 frame.port=state->in->ifindex;
 brdev=netdev_master_upper_dev_get_rcu(state->in);
 if(!brdev || !netif_is_bridge_master(brdev))return NF_ACCEPT;
 if(br_vlan_enabled(brdev)) {
  br_vlan_get_proto(brdev,&protocol);
  if(frame.tags && frame.tag[0].protocol==protocol)frame.vid=frame.tag[0].tci&VLAN_VID_MASK;
  if(!frame.vid && br_vlan_get_pvid_rcu(state->in,&frame.vid))return NF_ACCEPT;
 }
 wire=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(!wire || wire->epoch!=(u32)atomic_read(&bridge->epoch))return NF_ACCEPT;
 frame.ingress=wire->ingress;frame.epoch=wire->epoch;
 frame.pon_gem=wire->pon_gem;frame.pon_generation=wire->pon_generation;
 frame.tags=wire->tags;
 memcpy(frame.tag,wire->tag,sizeof(frame.tag));
 saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(saved)*saved=frame;
 return NF_ACCEPT;
}

static unsigned int fe_bridge_forwarded(void *private,struct sk_buff *skb,
                                       const struct nf_hook_state *state)
{
 struct rtl_fe_bridge *bridge=private;
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct fe_bridge_packet packet={.net=state->net};
 struct net_device *brdev,*physical;
 unsigned int next;

 if(!saved || !saved->port || !state->out || saved->epoch!=(u32)atomic_read(&bridge->epoch) ||
    fe_bridge_frame(skb,&packet.output))return NF_ACCEPT;
 physical=fe_bridge_physical(state->out,&packet.output,skb->priority);
 brdev=netdev_master_upper_dev_get_rcu(state->out);
 if(!physical || !brdev || !netif_is_bridge_master(brdev))return NF_ACCEPT;
 packet.input=*saved;
 packet.output.port=state->out->ifindex;
 packet.egress=physical->ifindex;
 packet.ingress=saved->port;
 packet.bridge=brdev->ifindex;
 packet.vid=saved->vid;
 if(packet.input.protocol!=packet.output.protocol || packet.input.session!=packet.output.session ||
    packet.input.ppp_protocol!=packet.output.ppp_protocol ||
    !ether_addr_equal(packet.input.source,packet.output.source) ||
    !ether_addr_equal(packet.input.destination,packet.output.destination))return NF_ACCEPT;
 spin_lock_bh(&bridge->pending_lock);
 next=(bridge->write+1)%FE_BRIDGE_PENDING;
 if(next!=bridge->read) {
  get_net(packet.net);
  bridge->pending[bridge->write]=packet;
  bridge->write=next;
 }
 spin_unlock_bh(&bridge->pending_lock);
 mod_delayed_work(system_wq,&bridge->work,0);
 return NF_ACCEPT;
}

static bool fe_bridge_ports(struct net *net,unsigned int index,unsigned int ingress,
                            unsigned int egress,const u8 *source,const u8 *destination,u16 vid)
{
 struct net_device *brdev=__dev_get_by_index(net,index);
 struct net_device *in=__dev_get_by_index(net,ingress),*out=__dev_get_by_index(net,egress);

 if(!brdev || !in || !out || !netif_running(in) || !netif_running(out) ||
    !netif_carrier_ok(in) || !netif_carrier_ok(out) ||
    netdev_master_upper_dev_get(in)!=brdev || netdev_master_upper_dev_get(out)!=brdev ||
    br_port_get_stp_state(in)!=BR_STATE_FORWARDING || br_port_get_stp_state(out)!=BR_STATE_FORWARDING)
  return false;
 return br_fdb_find_port(brdev,source,vid)==in && br_fdb_find_port(brdev,destination,vid)==out;
}

static int fe_bridge_learn(struct rtl_fe_bridge *bridge,const struct fe_bridge_packet *packet)
{
 struct rtl_fe *fe=bridge->fe;
 struct rtl_fe_rule data={.bridge=true,.vlan_exact=true};
 struct rtl_fe_flow *flow;
 struct net_device *outdev=__dev_get_by_index(packet->net,packet->egress);
 unsigned int i;
 int ret;

 if(packet->input.epoch!=(u32)atomic_read(&bridge->epoch) ||
    !fe_bridge_ports(packet->net,packet->bridge,packet->ingress,
      packet->output.port,packet->input.source,packet->input.destination,packet->vid))
  return -ENOENT;
 data.input=fe_port(fe,__dev_get_by_index(packet->net,packet->input.ingress));
 data.output=fe_port(fe,outdev);
 if(!data.input || !data.output || data.input==data.output || packet->input.tags>2)return -EOPNOTSUPP;
 list_for_each_entry(flow,&fe->flows,list)
  if(flow->learned && flow->input==data.input && flow->output==data.output &&
     flow->ethernet_protocol==packet->input.protocol && flow->ingress_session==packet->input.session &&
     flow->bridge_ppp_protocol==packet->input.ppp_protocol &&
     flow->bridge_vid==packet->vid && ether_addr_equal(flow->ethernet_source,packet->input.source) &&
     ether_addr_equal(flow->destination,packet->input.destination) &&
     flow->bridge_tags==packet->input.tags &&
     !memcmp(flow->bridge_tag,packet->input.tag,sizeof(packet->input.tag)))return 0;
 data.key=(struct rtl_fe_ipv4_key){.ingress=data.input->source,.llid=data.input->llid,
  .mask=data.input->wifi_id?58:59};
 data.action=(struct rtl_fe_ipv4_action){.bridge=1,.destination=data.output->destination,
  .deep_queue=data.output->destination>=32,.check_mask=data.key.mask};
 data.ethernet_protocol=packet->input.protocol;
 data.pppoe_input=packet->input.protocol==ETH_P_PPP_SES;
 data.pppoe_session=packet->input.session;
 if(data.pppoe_input) {
  data.key.mask-=2;data.action.check_mask=data.key.mask;
  data.ppp_protocol_slot=fe->ppp_protocol_ids[packet->input.ppp_protocol==PPP_IPV6];
 }
 ether_addr_copy(data.source,packet->input.source);
 ether_addr_copy(data.destination,packet->input.destination);
 data.vlan_count=packet->input.tags;
 for(i=0;i<packet->input.tags;i++) {
  const struct rtl9617c_bridge_tag *tag=&packet->input.tag[i];
  ret=fe_vlan_tpid(htons(tag->protocol));
  if(ret<0)return ret;
  data.vlan[i]=(struct rtl_fe_vlan){.vid=tag->tci&VLAN_VID_MASK,.tpid=ret};
  data.ingress_tci[i]=tag->tci;
 }
 data.action.vlan_count=packet->output.tags;
 data.action.vlan_set=packet->input.tags!=packet->output.tags ||
  memcmp(packet->input.tag,packet->output.tag,sizeof(packet->input.tag));
 for(i=0;i<packet->output.tags;i++) {
  const struct rtl9617c_bridge_tag *tag=&packet->output.tag[i];
  ret=fe_vlan_tpid(htons(tag->protocol));
  if(ret<0)return ret;
  data.action.vlan[i]=(struct rtl_fe_vlan){.vid=tag->tci&VLAN_VID_MASK,.tpid=ret,
   .priority=tag->tci>>VLAN_PRIO_SHIFT,.dei=!!(tag->tci&VLAN_CFI_MASK)};
 }
 if(data.input->netdev==fe->itu_netdev) {
  struct rtl_evto_packet wire;
  struct fe_evto_result inverse;
  if(!packet->input.pon_gem || packet->input.pon_generation!=fe->itu_paths->generation)return -ENOENT;
  fe_evto_packet(&wire,data.vlan,data.vlan_count,data.ethernet_protocol);
  for(i=0;i<wire.count;i++)wire.tag[i].tci=data.ingress_tci[i];
  if(wire.count)wire.priority=wire.tag[0].tci>>13;
  ret=fe_evto_invariant(fe->itu_paths,&wire,false,true,true,packet->input.pon_gem,&inverse);
  if(ret)return ret;
  data.key.llid=packet->input.pon_gem;
 }
 if(data.output->netdev==fe->itu_netdev) {
  struct rtl_evto_packet logical;
  fe_evto_packet(&logical,data.action.vlan,data.action.vlan_count,data.ethernet_protocol);
  ret=fe_evto_upstream(&data,&logical,true);
  if(ret)return ret;
 }
 data.ethernet_bytes=ETH_HLEN+VLAN_HLEN*data.action.vlan_count+(data.pppoe_input?8:0);
 /* A live flow allocation is its unique cookie; clsact/flowtable own other cookies. */
 ret=fe_install(data.input,data,0);
 if(ret)return ret;
 flow=list_last_entry(&fe->flows,struct rtl_fe_flow,list);
 flow->cookie=(unsigned long)flow;flow->learned=true;
 flow->bridge_index=packet->bridge;flow->bridge_vid=packet->vid;
 flow->bridge_ingress=packet->ingress;flow->bridge_egress=packet->output.port;
 flow->bridge_tags=packet->input.tags;
 flow->bridge_ppp_protocol=packet->input.ppp_protocol;
 memcpy(flow->bridge_tag,packet->input.tag,sizeof(flow->bridge_tag));
 return 0;
}

static void fe_bridge_work(struct work_struct *work)
{
 struct rtl_fe_bridge *bridge=container_of(to_delayed_work(work),struct rtl_fe_bridge,work);
 struct rtl_fe *fe=bridge->fe;
 struct rtl_fe_flow *flow,*next;
 struct rtl_dq_io io=fe_io(fe);
 bool flush=atomic_xchg(&bridge->flush,0);
 bool active=false;
 unsigned int budget=FE_BRIDGE_PENDING;

 rtnl_lock();
 mutex_lock(&fe->lock);
 list_for_each_entry_safe(flow,next,&fe->flows,list) {
  struct net *net;
  unsigned int age;
  unsigned long lifetime;
  if(!flow->learned)continue;
  if(flow->deleting || flow->input->retired || flow->output->retired) {
   fe_delete(fe,flow);continue;
  }
  net=dev_net(flow->input->netdev);
  if(flush || !fe_bridge_ports(net,flow->bridge_index,flow->bridge_ingress,
      flow->bridge_egress,flow->ethernet_source,flow->destination,flow->bridge_vid)) {
   fe_delete(fe,flow);continue;
  }
  lifetime=clock_t_to_jiffies(br_get_ageing_time(__dev_get_by_index(net,flow->bridge_index)));
  if(rtl_fe_main_age_read(&io,flow->hash_index,&age))continue;
  if(age>1) {
   rtl_fe_main_age(&io,flow->hash_index,1);
   flow->lastused=jiffies;
   br_fdb_refresh_offload(__dev_get_by_index(net,flow->bridge_ingress),flow->ethernet_source,flow->bridge_vid,true);
   br_fdb_refresh_offload(__dev_get_by_index(net,flow->bridge_egress),flow->destination,flow->bridge_vid,false);
  } else if(lifetime && time_after(jiffies,flow->lastused+lifetime))fe_delete(fe,flow);
 }
 while(budget--) {
  struct fe_bridge_packet packet;
  spin_lock_bh(&bridge->pending_lock);
  if(bridge->read==bridge->write) {
   spin_unlock_bh(&bridge->pending_lock);break;
  }
  packet=bridge->pending[bridge->read];bridge->read=(bridge->read+1)%FE_BRIDGE_PENDING;
  spin_unlock_bh(&bridge->pending_lock);
  fe_bridge_learn(bridge,&packet);
  put_net(packet.net);
 }
 list_for_each_entry(flow,&fe->flows,list)
  if(flow->learned && flow->published){active=true;break;}
 mutex_unlock(&fe->lock);
 rtnl_unlock();
 if(active && !READ_ONCE(bridge->stopping))queue_delayed_work(system_wq,&bridge->work,HZ);
}

static int fe_bridge_netdev_event(struct notifier_block *notifier,unsigned long event,void *ptr)
{
 struct rtl_fe_bridge *bridge=container_of(notifier,struct rtl_fe_bridge,netdev);
 struct net_device *dev=netdev_notifier_info_to_dev(ptr);

 if(event==NETDEV_REGISTER || event==NETDEV_UP)fe_bridge_wire_port(bridge,dev,true);
 if(event==NETDEV_UNREGISTER)fe_bridge_wire_port(bridge,dev,false);
 switch(event) {
 case NETDEV_DOWN:case NETDEV_CHANGE:case NETDEV_CHANGEUPPER:
 case NETDEV_PRECHANGEUPPER:case NETDEV_CHANGEINFODATA:case NETDEV_CHANGEADDR:
  atomic_inc(&bridge->epoch);atomic_set(&bridge->flush,1);
  mod_delayed_work(system_wq,&bridge->work,0);break;
 }
 return NOTIFY_DONE;
}

static int fe_bridge_switch_event(struct notifier_block *notifier,unsigned long event,void *ptr)
{
 struct rtl_fe_bridge *bridge=fe_bridge_owner;

 if(event==SWITCHDEV_FDB_ADD_TO_DEVICE || event==SWITCHDEV_FDB_DEL_TO_DEVICE ||
    event==SWITCHDEV_PORT_OBJ_ADD || event==SWITCHDEV_PORT_OBJ_DEL || event==SWITCHDEV_PORT_ATTR_SET) {
  atomic_inc(&bridge->epoch);
  if(event!=SWITCHDEV_FDB_ADD_TO_DEVICE && event!=SWITCHDEV_FDB_DEL_TO_DEVICE)
   atomic_set(&bridge->flush,1);
  mod_delayed_work(system_wq,&bridge->work,0);
 }
 return NOTIFY_DONE;
}

static int fe_bridge_net_init(struct net *net)
{
 return nf_register_net_hooks(net,fe_bridge_owner->hooks,2);
}
static void fe_bridge_net_exit(struct net *net)
{
 nf_unregister_net_hooks(net,fe_bridge_owner->hooks,2);
}

static struct rtl_fe_bridge *fe_bridge_start(struct rtl_fe *fe)
{
 struct rtl_fe_bridge *bridge=kzalloc(sizeof(*bridge),GFP_KERNEL);
 int ret;

 if(!bridge)return ERR_PTR(-ENOMEM);
 bridge->fe=fe;
 INIT_LIST_HEAD(&bridge->wire_ports);
 spin_lock_init(&bridge->pending_lock);
 INIT_DELAYED_WORK(&bridge->work,fe_bridge_work);
 bridge->netdev.notifier_call=fe_bridge_netdev_event;
 bridge->switchdev.notifier_call=fe_bridge_switch_event;
 bridge->blocking.notifier_call=fe_bridge_switch_event;
 bridge->pernet.init=fe_bridge_net_init;bridge->pernet.exit=fe_bridge_net_exit;
 bridge->hooks[0]=(struct nf_hook_ops){.hook=fe_bridge_capture,.priv=bridge,
  .pf=NFPROTO_BRIDGE,.hooknum=NF_BR_PRE_ROUTING,.priority=INT_MIN};
 bridge->hooks[1]=(struct nf_hook_ops){.hook=fe_bridge_forwarded,.priv=bridge,
  .pf=NFPROTO_BRIDGE,.hooknum=NF_BR_POST_ROUTING,.priority=INT_MAX};
 fe_bridge_owner=bridge;
 ret=register_pernet_subsys(&bridge->pernet);
 if(ret)goto free;
 ret=register_switchdev_notifier(&bridge->switchdev);
 if(ret)goto pernet;
 ret=register_switchdev_blocking_notifier(&bridge->blocking);
 if(ret)goto switchdev;
 ret=register_netdevice_notifier(&bridge->netdev);
 if(ret)goto blocking;
 return bridge;
blocking:
 unregister_switchdev_blocking_notifier(&bridge->blocking);
switchdev:
 unregister_switchdev_notifier(&bridge->switchdev);
pernet:
 unregister_pernet_subsys(&bridge->pernet);
free:
 WRITE_ONCE(bridge->stopping,true);
 cancel_delayed_work_sync(&bridge->work);
 while(bridge->read!=bridge->write) {
  put_net(bridge->pending[bridge->read].net);
  bridge->read=(bridge->read+1)%FE_BRIDGE_PENDING;
 }
 fe_bridge_owner=NULL;kfree(bridge);return ERR_PTR(ret);
}

static void fe_bridge_stop(struct rtl_fe_bridge *bridge)
{
 struct fe_bridge_wire_port *port,*next;

 WRITE_ONCE(bridge->stopping,true);
 unregister_netdevice_notifier(&bridge->netdev);
 rtnl_lock();
 list_for_each_entry_safe(port,next,&bridge->wire_ports,list) {
  nf_unregister_net_hook(dev_net(port->hook.dev),&port->hook);
  list_del(&port->list);kfree(port);
 }
 rtnl_unlock();
 unregister_switchdev_blocking_notifier(&bridge->blocking);
 unregister_switchdev_notifier(&bridge->switchdev);
 unregister_pernet_subsys(&bridge->pernet);
 cancel_delayed_work_sync(&bridge->work);
 while(bridge->read!=bridge->write) {
  put_net(bridge->pending[bridge->read].net);
  bridge->read=(bridge->read+1)%FE_BRIDGE_PENDING;
 }
 fe_bridge_owner=NULL;kfree(bridge);
}
