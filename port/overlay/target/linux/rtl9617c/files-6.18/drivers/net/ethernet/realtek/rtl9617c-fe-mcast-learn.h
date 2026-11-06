/* SPDX-License-Identifier: GPL-2.0-only */
/* Included after the bridge frame helpers. Transactions contain numeric
 * ifindices/tickets, never pointers into a later-freed skb or bridge port. */
static bool fe_mcast_hooks_clear(struct net *net)
{
#ifdef CONFIG_NETFILTER_FAMILY_BRIDGE
 unsigned int hook,i;
 for(hook=0;hook<NF_INET_NUMHOOKS;hook++) {
  const struct nf_hook_entries *entries=rcu_dereference(net->nf.hooks_bridge[hook]);
  if(!entries)continue;
  for(i=0;i<entries->num_hook_entries;i++)
   if(entries->hooks[i].hook!=fe_bridge_capture && entries->hooks[i].hook!=fe_bridge_forwarded)return false;
 }
#endif
 return true;
}

static bool fe_mcast_port_clear(struct net_device *dev)
{
 unsigned int q;
 /* A sampled packet cannot authorize bypassing a classful/action qdisc.
  * Check the actual per-queue disciplines beneath an mq root too. */
 for(q=0;q<dev->num_tx_queues;q++) {
  const struct Qdisc *disc=rtnl_dereference(netdev_get_tx_queue(dev,q)->qdisc_sleeping);
  if(strcmp(disc->ops->id,"noqueue") && strcmp(disc->ops->id,"pfifo_fast") &&
     strcmp(disc->ops->id,"fq_codel"))return false;
 }
#ifdef CONFIG_NET_XGRESS
 if(rcu_access_pointer(dev->tcx_ingress) || rcu_access_pointer(dev->tcx_egress))return false;
#endif
#ifdef CONFIG_NETFILTER_INGRESS
 {
  const struct nf_hook_entries *entries;
  bool clear=true;
  unsigned int i;
  rcu_read_lock();
  entries=rcu_dereference(dev->nf_hooks_ingress);
  if(entries)for(i=0;i<entries->num_hook_entries;i++)
   if(entries->hooks[i].hook!=fe_bridge_wire)clear=false;
  rcu_read_unlock();
  if(!clear)return false;
 }
#endif
#ifdef CONFIG_NETFILTER_EGRESS
 if(rcu_access_pointer(dev->nf_hooks_egress))return false;
#endif
 return true;
}

static bool fe_mcast_path_clear(struct net_device *dev)
{
 for(;;) {
  if(!fe_mcast_port_clear(dev))return false;
  if(!is_vlan_dev(dev))return true;
  dev=vlan_dev_real_dev(dev);
 }
}

#include "rtl9617c-pon-service.h"

/* Numeric group IDs are never reused while packets may still carry them.
 * The same short lock protects host lookup and withdrawal; no RX pointer
 * escapes the lock. Core tickets distinguish driver owner incarnations. */
static void fe_mcast_host_remove(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_fe_mc_host *host;
 unsigned long flags;
 if(!flow->mc_host)return;
 spin_lock_irqsave(&fe->mc_lock,flags);
 host=flow->mc_host;
 if(fe->mc_hosts[flow->mc_group]==host)fe->mc_hosts[flow->mc_group]=NULL;
 flow->mc_host=NULL;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 put_net(host->net);kfree(host);
}
static int fe_mcast_host_publish(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct rtl_fe_mc_host *prototype)
{
 struct rtl_fe_mc_host *host=kmemdup(prototype,sizeof(*host),GFP_KERNEL);
 unsigned long flags;
 int ret=0;
 if(!host)return -ENOMEM;
 get_net(host->net);
 spin_lock_irqsave(&fe->mc_lock,flags);
 if(fe->mc_gate.writers || fe->mc_gate.epoch!=host->epoch || fe->mc_hosts[flow->mc_group])ret=-ESTALE;
 else {fe->mc_hosts[flow->mc_group]=host;flow->mc_host=host;}
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(ret){put_net(host->net);kfree(host);}
 return ret;
}
static int fe_mcast_host_receive(void *context,struct sk_buff *skb,unsigned int group,unsigned int source)
{
 struct rtl_fe_bridge *bridge=context;
 struct rtl_fe *fe=bridge->fe;
 struct rtl_fe_mc_host host,*entry;
 struct rtl9617c_bridge_frame parsed={},*saved;
 struct net_device *input,*logical,*brdev;
 unsigned long flags;
 int ret=-ESTALE;
 if(!group || group>=RTL_FE_MC_GROUPS)return -EINVAL;
 spin_lock_irqsave(&fe->mc_lock,flags);
 entry=fe->mc_hosts[group];
 if(!READ_ONCE(bridge->stopping) && rtl_fe_enabled(fe) && entry &&
    fe->mc_gate.active && !fe->mc_gate.writers && entry->epoch==fe->mc_gate.epoch) {
  host=*entry;get_net(host.net);ret=0;
 }
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(ret)return ret;
 input=dev_get_by_index_rcu(host.net,host.input.ingress);
 logical=dev_get_by_index_rcu(host.net,host.input.port);
 brdev=dev_get_by_index_rcu(host.net,host.bridge);
 if(source!=host.source || (source==7 && (!READ_ONCE(fe->pon_requested) || input!=fe->itu_netdev)) || (source==6 && (!READ_ONCE(fe->fttr_admitted) || input!=fe->fttr_netdev)) || !input || !logical || !brdev ||
    !netif_running(input) || netdev_master_upper_dev_get_rcu(logical)!=brdev)goto stale;
 /* QM gives us the complete Ethernet frame before eth_type_trans. */
 skb_reset_mac_header(skb);__skb_pull(skb,ETH_HLEN);
 ret=fe_bridge_frame(skb,&parsed);__skb_push(skb,ETH_HLEN);
 if(ret)goto out;
 if(!ether_addr_equal(parsed.source,host.input.source) ||
    !ether_addr_equal(parsed.destination,host.input.destination) ||
    parsed.protocol!=host.input.protocol || parsed.ip_protocol!=host.input.ip_protocol ||
    parsed.source_port!=host.input.source_port || parsed.destination_port!=host.input.destination_port ||
    parsed.tags!=host.input.tags || memcmp(parsed.tag,host.input.tag,sizeof(parsed.tag)) ||
    memcmp(parsed.ip_source,host.input.ip_source,sizeof(parsed.ip_source)) ||
    memcmp(parsed.ip_destination,host.input.ip_destination,sizeof(parsed.ip_destination)))goto stale;
 saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(!saved){ret=-ENOMEM;goto out;}
 *saved=host.input;saved->multicast_host_group=group;saved->multicast_host_owner=host.owner;
 saved->multicast_epoch=host.epoch;skb->dev=input;skb->rtl9617c_fttr_ingress=source==6;ret=0;goto out;
stale:
 ret=-ESTALE;
out:
 put_net(host.net);return ret;
}
static int fe_mcast_host_local(void *context,struct sk_buff *skb,struct net_device *brdev,u16 vid)
{
 struct rtl_fe_bridge *bridge=context;
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct rtl_fe_mc_host *host;
 unsigned long flags;
 int ret=-ESTALE;
 if(!saved || !saved->multicast_host_group || saved->multicast_host_group>=RTL_FE_MC_GROUPS)return ret;
 spin_lock_irqsave(&bridge->fe->mc_lock,flags);
 host=bridge->fe->mc_hosts[saved->multicast_host_group];
 if(!READ_ONCE(bridge->stopping) && rtl_fe_enabled(bridge->fe) && host &&
    bridge->fe->mc_gate.active && !bridge->fe->mc_gate.writers &&
    host->epoch==bridge->fe->mc_gate.epoch && host->epoch==saved->multicast_epoch &&
    (host->source!=6 || READ_ONCE(bridge->fe->fttr_admitted)) &&
    (host->source!=7 || READ_ONCE(bridge->fe->pon_requested)) &&
    host->owner==saved->multicast_host_owner && host->net==dev_net(brdev) &&
    host->bridge==brdev->ifindex && host->input.port==skb->dev->ifindex && host->input.vid==vid)ret=1;
 spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
 return ret;
}

static struct fe_mcast_packet *fe_mcast_pending(struct rtl_fe_bridge *bridge,u64 ticket)
{
 struct fe_mcast_packet *packet=bridge->multicast_pending[ticket%FE_MCAST_PENDING];
 return ticket && packet && packet->transaction==ticket?packet:NULL;
}

static u64 fe_mcast_capture_begin(void *context,struct sk_buff *skb,bool local,u64 ticket)
{
 struct rtl_fe_bridge *bridge=context;
 struct rtl_fe *fe=bridge->fe;
 const struct rtl9617c_bridge_frame *wire=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct rtl9617c_bridge_frame *saved;
 struct fe_mcast_packet *packet;
 struct net_device *brdev;
 unsigned long flags;

 if(READ_ONCE(bridge->stopping) || !rtl_fe_enabled(fe) || !wire || !wire->port ||
    wire->epoch!=(u32)atomic_read(&bridge->epoch) || wire->multicast_host_group || wire->ip_protocol!=IPPROTO_UDP ||
    !is_multicast_ether_addr(wire->destination) || is_broadcast_ether_addr(wire->destination) ||
    skb_is_gso(skb))return 0;
 brdev=netdev_master_upper_dev_get_rcu(skb->dev);
 if(!brdev || !netif_is_bridge_master(brdev) || !fe_mcast_hooks_clear(dev_net(brdev)))return 0;
 packet=kzalloc(sizeof(*packet),GFP_ATOMIC);
 if(!packet)return 0;
 packet->input=*wire;packet->input.multicast_transaction=0;
 packet->net=get_net(dev_net(brdev));packet->bridge=brdev->ifindex;packet->local=local;
 saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(!saved){put_net(packet->net);kfree(packet);return 0;}
 spin_lock_irqsave(&fe->mc_lock,flags);
 if(fe->mc_gate.writers || fe->mc_gate.error || fe->mc_gate.epoch!=packet->input.multicast_epoch ||
    bridge->multicast_pending[ticket%FE_MCAST_PENDING]) {
  spin_unlock_irqrestore(&fe->mc_lock,flags);put_net(packet->net);kfree(packet);return 0;
 }
 packet->epoch=fe->mc_gate.epoch;packet->transaction=ticket;
 bridge->multicast_pending[ticket%FE_MCAST_PENDING]=packet;
 saved->multicast_transaction=ticket;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 return ticket;
}

static void fe_mcast_capture_select(void *context,struct sk_buff *skb,struct net_device *output)
{
 struct rtl_fe_bridge *bridge=context;
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct fe_mcast_packet *packet;
 unsigned long flags;
 unsigned int i;

 if(!saved || !saved->multicast_transaction)return;
 spin_lock_irqsave(&bridge->fe->mc_lock,flags);
 packet=fe_mcast_pending(bridge,saved->multicast_transaction);
 if(packet && !packet->complete) {
  for(i=0;i<packet->count;i++)if(packet->output[i].port==output->ifindex)break;
  if(i!=packet->count || i==RTL_FE_MC_ACTIONS)packet->failed=true;
  else packet->output[packet->count++].port=output->ifindex;
 }
 spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
}

static void fe_mcast_capture_output(void *context,struct sk_buff *skb)
{
 struct rtl_fe_bridge *bridge=context;
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct rtl9617c_bridge_frame frame={};
 struct fe_mcast_packet *packet;
 struct net_device *physical;
 unsigned long flags;
 unsigned int i;
 int ret;

 if(!saved || !saved->multicast_transaction)return;
 /* br_dev_queue_push_xmit has restored the Ethernet header. The existing
  * bridge parser expects skb->data just beyond it. */
 __skb_pull(skb,ETH_HLEN);
 ret=fe_bridge_frame(skb,&frame);
 __skb_push(skb,ETH_HLEN);
 physical=ret?NULL:fe_bridge_physical(skb->dev,&frame,skb->priority);
 spin_lock_irqsave(&bridge->fe->mc_lock,flags);
 packet=fe_mcast_pending(bridge,saved->multicast_transaction);
 if(!packet || packet->complete)goto unlock;
 for(i=0;i<packet->count;i++)if(packet->output[i].port==skb->dev->ifindex)break;
 if(i==packet->count || packet->output[i].seen || !physical ||
    !ether_addr_equal(frame.source,packet->input.source) ||
    !ether_addr_equal(frame.destination,packet->input.destination) ||
    frame.protocol!=packet->input.protocol || frame.ip_protocol!=packet->input.ip_protocol ||
    frame.source_port!=packet->input.source_port || frame.destination_port!=packet->input.destination_port ||
    memcmp(frame.ip_source,packet->input.ip_source,sizeof(frame.ip_source)) ||
    memcmp(frame.ip_destination,packet->input.ip_destination,sizeof(frame.ip_destination))) {
  packet->failed=true;goto unlock;
 }
 packet->output[i].frame=frame;packet->output[i].physical=physical->ifindex;
 packet->output[i].seen=true;packet->outputs++;
unlock:
 spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
}

static void fe_mcast_capture_end(void *context,u64 transaction,bool complete)
{
 struct rtl_fe_bridge *bridge=context;
 struct fe_mcast_packet *packet;
 unsigned long flags;

 spin_lock_irqsave(&bridge->fe->mc_lock,flags);
 packet=fe_mcast_pending(bridge,transaction);
 if(packet) {
  packet->complete=true;
  packet->failed|=!complete || (!packet->count && !packet->local) || packet->outputs!=packet->count ||
   packet->epoch!=bridge->fe->mc_gate.epoch || bridge->fe->mc_gate.writers;
 }
 spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
 if(packet && !READ_ONCE(bridge->stopping))mod_delayed_work(system_wq,&bridge->work,0);
}

static void fe_mcast_purge(struct rtl_fe_bridge *bridge)
{
 unsigned int i;
 for(i=0;i<FE_MCAST_PENDING;i++)if(bridge->multicast_pending[i]) {
  put_net(bridge->multicast_pending[i]->net);
  kfree(bridge->multicast_pending[i]);bridge->multicast_pending[i]=NULL;
 }
}

/* PON CPU RX starts with priority0; tagged frames derive PCP in its parser.
 * Keep the observed wire/logical images separate, checking every unkeyed DSCP
 * variant through the very same GEM and local-service/UNI selection. */
static int fe_mcast_pon_input(struct rtl_fe *fe,const struct rtl9617c_bridge_frame *frame)
{
 struct rtl_evto_packet input={.count=frame->tags,.protocol=frame->protocol},expected={.count=frame->pon_tags};
 unsigned int i,dscps;
 if(!fe->pon_requested || !fe->itu_paths || frame->pon_generation!=fe->itu_paths->generation ||
    frame->pon_gem<8 || frame->pon_gem>255 || frame->tags>2 || frame->pon_tags>2)return -ESTALE;
 for(i=0;i<frame->tags;i++)input.tag[i]=(struct rtl_evto_tag){frame->tag[i].protocol,frame->tag[i].tci};
 for(i=0;i<frame->pon_tags;i++)expected.tag[i]=(struct rtl_evto_tag){frame->pon_tag[i].protocol,frame->pon_tag[i].tci};
 if(input.count)input.priority=input.tag[0].tci>>13;
 if(frame->protocol==ETH_P_IP && (frame->source_port==67 || frame->source_port==68) &&
    (frame->destination_port==67 || frame->destination_port==68))input.criteria=1;
 if(frame->protocol==ETH_P_IPV6 && (frame->source_port==546 || frame->source_port==547) &&
    (frame->destination_port==546 || frame->destination_port==547))input.criteria=2;
 dscps=(fe_evto_dependencies(fe->itu_paths)&1)?64:1;
 for(i=0;i<dscps;i++) {
  const struct rtl_itu_path *path;
  struct rtl_evto_packet output;
  input.dscp=i;
  rcu_read_lock();
  path=rtl_pon_itu_receive_path(fe->itu_paths,fe->itu_netdev,frame->pon_gem,&input,&output);
  rcu_read_unlock();
  if(!path || !fe_evto_equal(&output,&expected) || output.priority!=frame->pon_priority)return -EOPNOTSUPP;
 }
 return 0;
}

/* RTNL and the FE mutex pin the netdev/port owners. The short gate lock is
 * taken only for epoch and final publication; no indirect IO runs under it. */
/* Apply the same OMCI/EVTO selection as CPU PON TX to each final replica.
 * UDP keys omit DSCP and untagged skb priority: all such variants must agree. */
static int fe_mcast_pon_replica(struct rtl_fe *fe,struct net_device *physical,
 const struct rtl9617c_bridge_frame *frame,struct rtl_fe_mc_replica *replica)
{
 struct rtl_evto_packet logical={.count=frame->tags,.protocol=frame->protocol};
 struct fe_evto_result result;
 unsigned int i;
 int ret;
 if(!fe->pon_requested || !fe->itu_paths || physical!=fe->itu_netdev)return -EOPNOTSUPP;
 for(i=0;i<frame->tags;i++)logical.tag[i]=(struct rtl_evto_tag){frame->tag[i].protocol,frame->tag[i].tci};
 if(logical.count)logical.priority=logical.tag[0].tci>>13;
 rcu_read_lock();
 logical.service_entry=rtl_pon_itu_service_entry(physical,&logical);
 rcu_read_unlock();
 ret=fe_evto_invariant(fe->itu_paths,&logical,false,true,false,0,&result);
 if(ret)return ret;
 if(result.path->multicast || !result.path->tcont || result.path->tcont>31 ||
    result.path->index<8 || result.path->queue>7)return -EOPNOTSUPP;
 replica->destination=32+result.path->tcont;replica->gem=result.path->index;
 replica->queue=result.path->queue;replica->tags=result.output.count;
 for(i=0;i<replica->tags;i++) {
  replica->tag[i].protocol=result.output.tag[i].tpid;
  replica->tag[i].tci=result.output.tag[i].tci;
 }
 return 0;
}

static int fe_mcast_learn(struct rtl_fe_bridge *bridge,const struct fe_mcast_packet *packet)
{
 struct rtl_fe *fe=bridge->fe;
 struct rtl_fe_rule data={.bridge=true,.multicast_ip=true,.multicast_learned=true,.mc_epoch=packet->epoch,.vlan_exact=true};
 struct rtl_fe_mc_rule *mc;
 struct rtl_fe_mc_host host={};
 struct rtl_fe_flow *flow,*next,*existing=NULL;
 struct net_device *brdev,*input;
 struct rtl_dq_io io=fe_io(fe);
 unsigned int i,j;
 unsigned long flags;
 int ret=0;

 if(packet->failed || packet->input.epoch!=(u32)atomic_read(&bridge->epoch))return -EOPNOTSUPP;
 spin_lock_irqsave(&fe->mc_lock,flags);
 if(packet->epoch!=fe->mc_gate.epoch || fe->mc_gate.writers || fe->mc_gate.error)ret=-ESTALE;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(ret)return ret;
 brdev=__dev_get_by_index(packet->net,packet->bridge);
 input=__dev_get_by_index(packet->net,packet->input.port);
 if(!brdev || !netif_is_bridge_master(brdev) || !input ||
    netdev_master_upper_dev_get(input)!=brdev || br_port_get_stp_state(input)!=BR_STATE_FORWARDING ||
    br_fdb_find_port(brdev,packet->input.source,packet->input.vid)!=input)return -ESTALE;
 data.input=fe_port(fe,__dev_get_by_index(packet->net,packet->input.ingress));
 if(!data.input || (data.input->source>4 && data.input->netdev!=fe->fttr_netdev && data.input->netdev!=fe->itu_netdev) ||
    data.input->wifi_id || data.input->llid)return -EOPNOTSUPP;
 if(data.input->netdev==fe->itu_netdev) {
  if(data.input->source!=7)return -EOPNOTSUPP;
  ret=fe_mcast_pon_input(fe,&packet->input);if(ret)return ret;
 }
 if(data.input->netdev==fe->fttr_netdev) {
  if(!fe->fttr_admitted || data.input->source!=6 || !is_vlan_dev(input) ||
     vlan_dev_real_dev(input)!=fe->fttr_netdev || !packet->input.tags ||
     packet->input.tag[0].protocol!=ETH_P_8021Q ||
     (packet->input.tag[0].tci&VLAN_VID_MASK)!=vlan_dev_vlan_id(input) ||
     !vlan_dev_vlan_id(input) || vlan_dev_vlan_id(input)==4095)return -EOPNOTSUPP;
 }
 /* This producer implements bridge forwarding. Packet-program/filter
  * offload needs an explicit TC rule and cannot be inferred from one frame. */
 if(!fe_mcast_path_clear(input) || !fe_mcast_port_clear(data.input->netdev) ||
    !fe_mcast_port_clear(brdev))return -EOPNOTSUPP;
 /* One shared learned template: retire all previous generations before any
  * opening can make them visible again. A failed withdrawal keeps it closed. */
 list_for_each_entry_safe(flow,next,&fe->flows,list)if(flow->learned && flow->replica_count) {
  if(flow->deleting || flow->mc_epoch!=packet->epoch) {
   ret=fe_delete(fe,flow);if(ret)return ret;
  } else if(flow->input==data.input && flow->ingress_pol==packet->input.pon_gem && flow->bridge_index==packet->bridge &&
    flow->ethernet_protocol==packet->input.protocol && flow->bridge_vid==packet->input.vid &&
    ether_addr_equal(flow->ethernet_source,packet->input.source) &&
    ether_addr_equal(flow->destination,packet->input.destination) &&
    flow->bridge_tags==packet->input.tags &&
    flow->source_port==packet->input.source_port && flow->destination_port==packet->input.destination_port &&
    !memcmp(flow->bridge_tag,packet->input.tag,sizeof(flow->bridge_tag))) {
   bool same=true;
   for(i=0;i<4;i++)if(get_unaligned_be32(flow->ip_source+i*4)!=packet->input.ip_source[i] ||
     get_unaligned_be32(flow->ip_destination+i*4)!=packet->input.ip_destination[i])same=false;
   if(same) {
    existing=flow;
   }
  }
 }
 mc=kzalloc(sizeof(*mc),GFP_KERNEL);if(!mc)return -ENOMEM;
 data.multicast=mc;
 for(i=0;i<packet->count;i++) {
  struct net_device *dev=__dev_get_by_index(packet->net,packet->output[i].port);
  struct net_device *physical=__dev_get_by_index(packet->net,packet->output[i].physical);
  const struct rtl9617c_bridge_frame *frame=&packet->output[i].frame;
  struct rtl_fe_port *port;
  struct rtl_fe_mc_replica *replica=&mc->replica[mc->count];
  bool shared=dev==fe->fttr_mcast,pon=physical && physical==fe->itu_netdev;
  if(!dev || !physical || !netif_running(dev) || !netif_carrier_ok(dev) ||
     netdev_master_upper_dev_get(dev)!=brdev || br_port_get_stp_state(dev)!=BR_STATE_FORWARDING) {
   ret=-ESTALE;goto out;
  }
  if(!fe_mcast_path_clear(dev) || !fe_mcast_port_clear(physical)){ret=-EOPNOTSUPP;goto out;}
  /* The native parent drops duplicate data sent via member UNI VLANs.
   * Preserve that established policy instead of adding a second replica. */
  if(physical==fe->fttr_netdev && dev->group==0x46544d55 &&
     rtl_fttr_mcast_ready(fe->fttr_mcast) && netdev_master_upper_dev_get(fe->fttr_mcast)==brdev &&
     (frame->destination[0]==1 || frame->destination[0]==0x33))continue;
  if(shared) {
   physical=fe->fttr_netdev;
   if(!physical || !fe_mcast_port_clear(physical)){ret=-EOPNOTSUPP;goto out;}
  }
  /* The common PE acts before NI replication. Oversized IP datagrams
   * return to CPU16, where Linux applies each output's own MTU decision. */
  if(!data.mtu || dev->mtu<data.mtu)data.mtu=dev->mtu;
  if(physical->mtu<data.mtu)data.mtu=physical->mtu;
  if(shared && physical->mtu>VLAN_HLEN && physical->mtu-VLAN_HLEN<data.mtu)
   data.mtu=physical->mtu-VLAN_HLEN;
  port=fe_port(fe,physical);
  if(!port || (port==data.input && !shared) || port->wifi_id || (!pon && port->destination>6) || port->destination==5 ||
     (physical==fe->fttr_netdev && !shared)){ret=-EOPNOTSUPP;goto out;}
  replica->destination=port->destination;replica->tags=frame->tags;
  for(j=0;j<frame->tags;j++) {
   replica->tag[j].protocol=frame->tag[j].protocol;replica->tag[j].tci=frame->tag[j].tci;
  }
  if(pon) {
   ret=fe_mcast_pon_replica(fe,physical,frame,replica);
   if(ret)goto out;
   /* Match CPU PON TX's 2000-byte Ethernet limit after all EVTO edits. */
   if(data.mtu>2000U-ETH_HLEN-replica->tags*VLAN_HLEN)
    data.mtu=2000U-ETH_HLEN-replica->tags*VLAN_HLEN;
  }
  if(shared) {
   if(frame->tags>1 || !rtl_fttr_mcast_ready(dev)){ret=-EOPNOTSUPP;goto out;}
   replica->tag[1]=replica->tag[0];replica->tag[0].protocol=ETH_P_8021Q;
   replica->tag[0].tci=4095;replica->tags++;
   if(data.input->netdev==fe->fttr_netdev) {
    replica->source_set=1;ether_addr_copy(replica->source,dev->dev_addr);
   }
  }
  mc->port[mc->count++]=port;
 }
 if(packet->local) {
  if(mc->count==RTL_FE_MC_ACTIONS){ret=-E2BIG;goto out;}
  host=(struct rtl_fe_mc_host){.input=packet->input,.net=packet->net,.bridge=packet->bridge,
   .epoch=packet->epoch,.owner=packet->transaction,.source=data.input->source};
  mc->host=&host;
  mc->replica[mc->count]=(struct rtl_fe_mc_replica){.destination=16,
   .host_group=existing?existing->mc_group:1};
  mc->port[mc->count++]=data.input;
  if(!data.mtu || brdev->mtu<data.mtu)data.mtu=brdev->mtu;
  /* Host copy uses the complete ingress wire frame. Larger packets must
   * take the ordinary P6 miss/segmented RX path before any replication. */
  if(data.mtu>RTL_QM_DATA_BYTES-16U-ETH_HLEN-packet->input.tags*VLAN_HLEN)
   data.mtu=RTL_QM_DATA_BYTES-16U-ETH_HLEN-packet->input.tags*VLAN_HLEN;
 }
 if(!mc->count || !data.mtu){ret=-EOPNOTSUPP;goto out;}
 if((io.read(io.context,0x351c)&(0x80000000U|0x3fU<<16))!=(0x80000000U|16U<<16)) {
  ret=-EUCLEAN;goto out;
 }
 if(existing) {
  unsigned int bitmap[2]={},words[3],a;
  for(i=0;i<mc->count;i++) {
   if(mc->replica[i].source_set) {
    unsigned int m;
    for(m=0;m<8;m++)if(fe->multicast.macs[m].ready) {
     unsigned int lo=fe->multicast.macs[m].words[0],hi=fe->multicast.macs[m].words[1];
     const unsigned char *mac=mc->replica[i].source;
     if(lo==((unsigned int)mac[2]<<24|(unsigned int)mac[3]<<16|mac[4]<<8|mac[5]) &&
        (hi&65535)==((unsigned int)mac[0]<<8|mac[1]))break;
    }
    if(m==8)break;
    mc->replica[i].source_index=m;
   }
   ret=rtl_fe_mc_replica_encode(&io,&mc->replica[i],words);if(ret)goto out;
   for(a=0;a<RTL_FE_MC_ACTIONS;a++)if(fe->multicast.actions[a].ready &&
     rtl_fe_mc_equal(words,fe->multicast.actions[a].words,3))break;
   if(a==RTL_FE_MC_ACTIONS || (bitmap[a/32]&(1U<<(a%32))))break;
   bitmap[a/32]|=1U<<(a%32);
  }
  if(i==mc->count && existing->replica_count==mc->count && existing->mtu_owned &&
     fe->mtus[existing->mtu_index].size==data.mtu &&
     rtl_fe_mc_equal(bitmap,fe->multicast.groups[existing->mc_group].bitmap,2))goto out;
  /* A complete packet disagreed despite the same key/generation. Close
   * the learned profile, retire it and require another complete decision. */
  fe_mcast_invalidate(fe);ret=-ESTALE;goto out;
 }
 data.output=mc->port[mc->count-1];data.ipv6=packet->input.protocol==ETH_P_IPV6;
 data.ethernet_protocol=packet->input.protocol;
 data.key=(struct rtl_fe_ipv4_key){.source=packet->input.ip_source[0],.destination=packet->input.ip_destination[0],
  .source_port=packet->input.source_port,.destination_port=packet->input.destination_port,
  .protocol=packet->input.ip_protocol,.ingress=data.input->source,.llid=packet->input.pon_gem,.mask=RTL_FE_MC_LEARNED_MASK};
 memcpy(data.key6.source,packet->input.ip_source,sizeof(data.key6.source));
 memcpy(data.key6.destination,packet->input.ip_destination,sizeof(data.key6.destination));
 data.action.bridge=1;data.action.check_mask=RTL_FE_MC_LEARNED_MASK;
 ether_addr_copy(data.source,packet->input.source);ether_addr_copy(data.destination,packet->input.destination);
 data.vlan_count=packet->input.tags;
 for(i=0;i<data.vlan_count;i++) {
  ret=fe_vlan_tpid(htons(packet->input.tag[i].protocol));if(ret<0)goto out;
  data.vlan[i]=(struct rtl_fe_vlan){.vid=packet->input.tag[i].tci&VLAN_VID_MASK,.tpid=ret};
  data.ingress_tci[i]=packet->input.tag[i].tci;
 }
 ret=fe_install(data.input,data,0);
 if(ret){fe_mcast_invalidate(fe);goto out;}
 flow=list_last_entry(&fe->flows,struct rtl_fe_flow,list);
 rb_erase(&flow->cookie_node,&fe->cookies);flow->cookie=(unsigned long)flow;
 flow->learned=true;flow->mc_epoch=packet->epoch;
 flow->bridge_index=packet->bridge;flow->bridge_vid=packet->input.vid;
 flow->bridge_ingress=packet->input.port;flow->bridge_tags=packet->input.tags;
 memcpy(flow->bridge_tag,packet->input.tag,sizeof(flow->bridge_tag));fe_index_flow(fe,flow);
 spin_lock_irqsave(&fe->mc_lock,flags);
 ret=rtl_fe_mc_gate_open(&io,&fe->mc_gate,packet->epoch);
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(ret)fe_delete(fe,flow);
out:
 kfree(mc);return ret;
}

static void fe_mcast_process(struct rtl_fe_bridge *bridge)
{
 unsigned int i;
 for(i=0;i<FE_MCAST_PENDING;i++) {
  struct fe_mcast_packet *packet;
  unsigned long flags;
  spin_lock_irqsave(&bridge->fe->mc_lock,flags);
  packet=bridge->multicast_pending[i];
  if(packet && packet->complete)bridge->multicast_pending[i]=NULL;
  else packet=NULL;
  spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
  if(!packet)continue;
  fe_mcast_learn(bridge,packet);put_net(packet->net);kfree(packet);
 }
}
