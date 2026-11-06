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
   if(entries->hooks[i].hook!=fe_bridge_wire && entries->hooks[i].hook!=rtl_wifi_ingress)clear=false;
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

/* VLAN netdevs materialize an existing metadata tag before ndo_start_xmit.
 * Match vlan_dev_hard_start_xmit's REORDER_HDR/protocol decision, including
 * a same-protocol tag which a no-reorder VLAN must leave in place. */
static struct net_device *fe_mcast_physical(struct net_device *dev,
 struct rtl9617c_bridge_frame *frame,u32 priority)
{
 while(is_vlan_dev(dev)) {
  unsigned int protocol=ntohs(vlan_dev_vlan_proto(dev));
  if(dev->features&(NETIF_F_HW_VLAN_CTAG_TX|NETIF_F_HW_VLAN_STAG_TX))return NULL;
  if((vlan_dev_priv(dev)->flags&VLAN_FLAG_REORDER_HDR) || !frame->tags || frame->tag[0].protocol!=protocol) {
   if(frame->tags==2)return NULL;
   frame->tag[1]=frame->tag[0];
   frame->tag[0]=(struct rtl9617c_bridge_tag){.protocol=protocol,
    .tci=vlan_dev_vlan_id(dev)|vlan_dev_get_egress_qos_mask(dev,priority)};
   frame->tags++;
  }
  dev=vlan_dev_real_dev(dev);
 }
 return dev;
}

/* RX priority is not a stored Hash key. Derive it from the actual native
 * ingress and last consumed VLAN, not from one sample's arbitrary skb value.
 * Raw wireless misses retain per-packet COS; their replicas preserve COS. */
static int fe_mcast_input_priority(struct rtl_fe *fe,struct rtl_fe_port *port,
 struct net_device *logical,const struct rtl9617c_bridge_frame *frame,u32 *priority,bool *from_cos)
{
 struct net_device *dev=logical;
 const struct rtl9617c_bridge_tag *tags=frame->tag;
 unsigned int count=frame->tags,depth=0;
 *priority=0;*from_cos=port->wifi_id!=0;
 if(port->netdev==fe->itu_netdev) {
  tags=frame->pon_tag;count=frame->pon_tags;*priority=frame->pon_priority;
 }
 while(is_vlan_dev(dev)){depth++;dev=vlan_dev_real_dev(dev);}
 if(dev!=port->netdev || depth>count)return -EOPNOTSUPP;
 if(!depth)return 0;
 dev=logical;
 for(unsigned int i=depth;i;i--) {
  if(tags[i-1].protocol!=ntohs(vlan_dev_vlan_proto(dev)) ||
     (tags[i-1].tci&VLAN_VID_MASK)!=vlan_dev_vlan_id(dev))return -EOPNOTSUPP;
  dev=vlan_dev_real_dev(dev);
 }
 *priority=READ_ONCE(vlan_dev_priv(logical)->ingress_priority_map[tags[depth-1].tci>>13]);
 *from_cos=false;
 return 0;
}

#include "rtl9617c-pon-service.h"

static bool fe_mcast_epon_port(struct rtl_fe *fe,const struct rtl_fe_port *port)
{
 return port && port->source==7 && port->netdev!=fe->itu_netdev &&
  READ_ONCE(fe->epon_admitted) && port->llid<fe->epon_paths.count &&
  (fe->epon_paths.active&BIT(port->llid)) && port->destination==32+port->llid;
}

#include "rtl9617c-fe-mcast-pe-learn.h"

/* Numeric group IDs are never reused while packets may still carry them.
 * The same short lock protects host lookup and withdrawal; no RX pointer
 * escapes the lock. Core tickets distinguish driver owner incarnations. */
static void fe_mcast_host_remove(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_fe_mc_host *host;
 unsigned long flags;
 if(!flow->replica_count)return;
 spin_lock_irqsave(&fe->mc_lock,flags);
 for(unsigned int i=1;i<RTL_FE_MC_GROUPS;i++)
  if(fe->mc_directs[i] && fe->mc_directs[i]->flow==flow) {
   unsigned int dest=fe->mc_directs[i]->pe_destination;
   if(dest && fe->mc_pe[dest-18].table)WRITE_ONCE(fe->mc_pe[dest-18].table->record[i].epoch,0);
   fe->mc_directs[i]->flow=NULL;
  }
 dma_wmb();
 host=flow->mc_host;
 if(fe->mc_hosts[flow->mc_group]==host)fe->mc_hosts[flow->mc_group]=NULL;
 flow->mc_host=NULL;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(host){put_net(host->net);kfree(host);}
}

/* A direct CPU replica needs a distinct immutable POL cookie. Reserve it
 * from the same quarantined namespace as local-copy groups. This consumes
 * one group ID per direct recipient until hardware retirement is proven. */
static int fe_mcast_direct_stage(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct rtl_fe_mc_rule *mc)
{
 unsigned int i,c;
 unsigned long flags;
 for(i=0;i<mc->count;i++)if(mc->direct[i].physical) {
  struct rtl_fe_mc_direct *entry=kmemdup(&mc->direct[i],sizeof(*entry),GFP_KERNEL);
  if(!entry)return -ENOMEM;
  for(c=1;c<RTL_FE_MC_GROUPS;c++)if(fe->multicast.groups[c].state==RTL_FE_MC_FREE)break;
  if(c==RTL_FE_MC_GROUPS){kfree(entry);return -ENOSPC;}
  /* The numeric cookie cannot later become a host-copy group. */
  fe->multicast.groups[c].state=RTL_FE_MC_RETIRED;
  entry->flow=flow;
  spin_lock_irqsave(&fe->mc_lock,flags);
  if(entry->pe_destination) {
   int ret=fe_mcast_pe_stage(fe,entry,c);
   if(ret){spin_unlock_irqrestore(&fe->mc_lock,flags);kfree(entry);return ret;}
  }
  fe->mc_directs[c]=entry;
  spin_unlock_irqrestore(&fe->mc_lock,flags);
  mc->replica[i].direct_cookie=c;
 }
 return 0;
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
 struct rtl_fe_mc_direct direct={};
 struct rtl9617c_bridge_frame parsed={},*saved;
 struct net_device *input,*logical,*brdev;
 unsigned long flags;
 int ret=-ESTALE;
 if(!group || group>=RTL_FE_MC_GROUPS)return -EINVAL;
 spin_lock_irqsave(&fe->mc_lock,flags);
 if(fe->mc_directs[group]) {
  direct=*fe->mc_directs[group];
  if(direct.pe_destination){spin_unlock_irqrestore(&fe->mc_lock,flags);return -ESTALE;}
  entry=direct.flow?direct.flow->mc_host:NULL;
 } else entry=fe->mc_hosts[group];
 if(!READ_ONCE(bridge->stopping) && rtl_fe_enabled(fe) && entry &&
    fe->mc_gate.active && !fe->mc_gate.writers && entry->epoch==fe->mc_gate.epoch &&
    (!entry->input.epon || (fe->epon_admitted && entry->input.pon_generation==fe->epon_paths.generation))) {
  host=*entry;get_net(host.net);ret=0;
 }
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(ret)return ret;
 input=dev_get_by_index_rcu(host.net,host.input.ingress);
 logical=dev_get_by_index_rcu(host.net,host.input.port);
 brdev=dev_get_by_index_rcu(host.net,host.bridge);
 if(source!=host.source || (source==7 && (!READ_ONCE(fe->pon_requested) ||
    (!host.input.epon && input!=fe->itu_netdev))) ||
    (source==6 && (!READ_ONCE(fe->fttr_admitted) || input!=fe->fttr_netdev)) || !input || !logical || !brdev ||
    !netif_running(input) || netdev_master_upper_dev_get_rcu(logical)!=brdev)goto stale;
 /* QM gives us the complete Ethernet frame before eth_type_trans. */
 skb_reset_mac_header(skb);__skb_pull(skb,ETH_HLEN);
 ret=fe_bridge_frame(skb,&parsed);__skb_push(skb,ETH_HLEN);
 if(ret)goto out;
 if(direct.physical) {
  struct net_device *output=dev_get_by_index_rcu(host.net,direct.output);
  struct net_device *physical=dev_get_by_index_rcu(host.net,direct.physical);
  if(!output || !physical || !netif_running(output) || !netif_carrier_ok(output) ||
     !netif_running(physical) || !netif_carrier_ok(physical) ||
     netdev_master_upper_dev_get_rcu(output)!=brdev || br_port_get_stp_state(output)!=BR_STATE_FORWARDING)goto stale;
  if(direct.wifi_id && (!physical->ieee80211_ptr || physical->ieee80211_ptr->iftype!=direct.iftype ||
     (physical->features&(NETIF_F_HW_VLAN_CTAG_TX|NETIF_F_HW_VLAN_STAG_TX))))goto stale;
  if(parsed.tags!=direct.frame.tags || memcmp(parsed.tag,direct.frame.tag,sizeof(parsed.tag)))goto stale;
  /* Other copies already reached their destinations. Re-enter the exact
   * physical port's normal TX with the final wire tags, never bridge RX. */
  input=physical;
 } else if(!host.local || parsed.tags!=host.input.tags || memcmp(parsed.tag,host.input.tag,sizeof(parsed.tag)))goto stale;
 if(!ether_addr_equal(parsed.source,host.input.source) ||
    !ether_addr_equal(parsed.destination,host.input.destination) ||
    parsed.protocol!=host.input.protocol || parsed.ip_protocol!=host.input.ip_protocol ||
    parsed.source_port!=host.input.source_port || parsed.destination_port!=host.input.destination_port ||
    memcmp(parsed.ip_source,host.input.ip_source,sizeof(parsed.ip_source)) ||
    memcmp(parsed.ip_destination,host.input.ip_destination,sizeof(parsed.ip_destination)))goto stale;
 if(direct.physical && !ether_addr_equal(direct.frame.destination,host.input.destination) &&
    skb_cow_head(skb,0)){ret=-ENOMEM;goto out;}
 saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
 if(!saved){ret=-ENOMEM;goto out;}
 if(direct.physical) {
  /* Parsing/allocation ran outside the short lock. A membership or port
   * writer during that work must revoke direct TX before enqueue. */
  spin_lock_irqsave(&fe->mc_lock,flags);
  entry=fe->mc_directs[group] && fe->mc_directs[group]->flow ? fe->mc_directs[group]->flow->mc_host:NULL;
  ret=entry && entry->owner==host.owner && entry->epoch==host.epoch &&
   !READ_ONCE(bridge->stopping) && rtl_fe_enabled(fe) && fe->mc_gate.active &&
   !fe->mc_gate.writers && fe->mc_gate.epoch==host.epoch ? 0:-ESTALE;
  spin_unlock_irqrestore(&fe->mc_lock,flags);
  if(ret)goto out;
 }
 *saved=host.input;saved->multicast_host_group=group;saved->multicast_host_owner=host.owner;
 saved->multicast_epoch=host.epoch;skb->dev=input;skb->rtl9617c_fttr_ingress=source==6;
 if(direct.physical){saved->wifi_id=direct.wifi_id;
  if(!direct.priority_from_cos)skb->priority=direct.priority;
  if(!ether_addr_equal(direct.frame.destination,host.input.destination))
   ether_addr_copy(eth_hdr(skb)->h_dest,direct.frame.destination);
  ret=direct.wifi_id?1:2;
 }else ret=0;
 goto out;
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
 if(!READ_ONCE(bridge->stopping) && rtl_fe_enabled(bridge->fe) && host && host->local &&
    bridge->fe->mc_gate.active && !bridge->fe->mc_gate.writers &&
    host->epoch==bridge->fe->mc_gate.epoch && host->epoch==saved->multicast_epoch &&
    (host->source!=6 || READ_ONCE(bridge->fe->fttr_admitted)) &&
    (host->source!=7 || (READ_ONCE(bridge->fe->pon_requested) && (!host->input.epon ||
     (READ_ONCE(bridge->fe->epon_admitted) && host->input.pon_generation==bridge->fe->epon_paths.generation)))) &&
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
    is_link_local_ether_addr(wire->destination) ||
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

static void fe_mcast_capture_select_addr(void *context,struct sk_buff *skb,struct net_device *output,const u8 *address)
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
  if(!address || is_broadcast_ether_addr(address))address=packet->input.destination;
  else if(!is_valid_ether_addr(address)){packet->failed=true;goto unlock;}
  for(i=0;i<packet->count;i++)if(packet->output[i].port==output->ifindex &&
     ether_addr_equal(packet->output[i].destination,address))break;
  if(i!=packet->count || i==RTL_FE_MC_ACTIONS)packet->failed=true;
  else {packet->output[i].port=output->ifindex;ether_addr_copy(packet->output[i].destination,address);packet->count++;}
 }
unlock:
 spin_unlock_irqrestore(&bridge->fe->mc_lock,flags);
}

static void fe_mcast_capture_select(void *context,struct sk_buff *skb,struct net_device *output)
{
 fe_mcast_capture_select_addr(context,skb,output,NULL);
}

static void fe_mcast_capture_output(void *context,struct sk_buff *skb)
{
 struct rtl_fe_bridge *bridge=context;
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 struct rtl9617c_bridge_frame frame={.ip_protocol=IPPROTO_UDP};
 struct rtl9617c_bridge_tag logical_tag[2];
 unsigned int logical_tags;
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
 logical_tags=frame.tags;memcpy(logical_tag,frame.tag,sizeof(logical_tag));
 physical=ret?NULL:fe_mcast_physical(skb->dev,&frame,skb->priority);
 spin_lock_irqsave(&bridge->fe->mc_lock,flags);
 packet=fe_mcast_pending(bridge,saved->multicast_transaction);
 if(!packet || packet->complete)goto unlock;
 for(i=0;i<packet->count;i++)if(packet->output[i].port==skb->dev->ifindex &&
    ether_addr_equal(packet->output[i].destination,frame.destination))break;
 if(i==packet->count || packet->output[i].seen || !physical ||
    !ether_addr_equal(frame.source,packet->input.source) ||
    frame.protocol!=packet->input.protocol || frame.ip_protocol!=packet->input.ip_protocol ||
    frame.source_port!=packet->input.source_port || frame.destination_port!=packet->input.destination_port ||
    memcmp(frame.ip_source,packet->input.ip_source,sizeof(frame.ip_source)) ||
    memcmp(frame.ip_destination,packet->input.ip_destination,sizeof(frame.ip_destination))) {
  packet->failed=true;goto unlock;
 }
 packet->output[i].frame=frame;packet->output[i].physical=physical->ifindex;
 packet->output[i].priority=skb->priority;packet->output[i].logical_tags=logical_tags;
 memcpy(packet->output[i].logical_tag,logical_tag,sizeof(logical_tag));
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
 if(!data.input || (data.input->source>4 && data.input->netdev!=fe->fttr_netdev && data.input->netdev!=fe->itu_netdev && !data.input->wifi_id && !fe_mcast_epon_port(fe,data.input)) ||
    (data.input->llid && !data.input->wifi_id && !packet->input.epon))return -EOPNOTSUPP;
 if(packet->input.epon) {
  if(!fe_mcast_epon_port(fe,data.input) || packet->input.epon_llid!=data.input->llid ||
     packet->input.pon_generation!=fe->epon_paths.generation || packet->input.pon_gem)return -ESTALE;
 } else if(fe_mcast_epon_port(fe,data.input))return -ESTALE;
 if(data.input->wifi_id) {
  if(data.input->source!=RTL_WIFI_SOURCE || packet->input.wifi_id!=data.input->wifi_id ||
     data.input->llid!=data.input->wifi_id || packet->input.pon_gem)return -ESTALE;
 }
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
  } else if(flow->input==data.input && flow->ingress_pol==(data.input->wifi_id?packet->input.wifi_id:packet->input.epon?packet->input.epon_llid:packet->input.pon_gem) && flow->bridge_index==packet->bridge &&
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
 host=(struct rtl_fe_mc_host){.input=packet->input,.net=packet->net,.bridge=packet->bridge,
  .epoch=packet->epoch,.owner=packet->transaction,.source=data.input->source,.local=packet->local};
 for(i=0;i<packet->count;i++) {
  struct net_device *dev=__dev_get_by_index(packet->net,packet->output[i].port);
  struct net_device *physical=__dev_get_by_index(packet->net,packet->output[i].physical);
  const struct rtl9617c_bridge_frame *frame=&packet->output[i].frame;
  struct rtl_fe_port *port;
  struct rtl_fe_mc_replica *replica=&mc->replica[mc->count];
  bool shared=dev==fe->fttr_mcast,pon=physical && physical==fe->itu_netdev;
  bool unicast=!ether_addr_equal(frame->destination,packet->input.destination);
  if(!dev || !physical || !netif_running(dev) || !netif_carrier_ok(dev) ||
     netdev_master_upper_dev_get(dev)!=brdev || br_port_get_stp_state(dev)!=BR_STATE_FORWARDING) {
   ret=-ESTALE;goto out;
  }
  if(!fe_mcast_path_clear(dev) || !fe_mcast_port_clear(physical)){ret=-EOPNOTSUPP;goto out;}
  if(unicast && (!is_valid_ether_addr(frame->destination) || shared)){ret=-EOPNOTSUPP;goto out;}
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
  if(!port || (port==data.input && !shared && !unicast) || (!pon && !port->wifi_id && !fe_mcast_epon_port(fe,port) && port->destination>6) || port->destination==5 ||
     (physical==fe->fttr_netdev && !shared && !unicast)){ret=-EOPNOTSUPP;goto out;}
  replica->destination=port->destination;replica->tags=frame->tags;
  for(j=0;j<frame->tags;j++) {
   replica->tag[j].protocol=frame->tag[j].protocol;replica->tag[j].tci=frame->tag[j].tci;
  }
  if(fe_mcast_epon_port(fe,port) && !unicast) {
   u32 priority;
   bool from_cos;
   ret=fe_mcast_input_priority(fe,data.input,input,&packet->input,&priority,&from_cos);if(ret)goto out;
   if(from_cos?packet->output[i].priority>7:packet->output[i].priority!=priority){ret=-EOPNOTSUPP;goto out;}
   if(from_cos && fe->epon_paths.queues!=8){ret=-EOPNOTSUPP;goto out;}
   for(unsigned int v=0;v<(from_cos?8U:1U);v++) {
    struct rtl9617c_bridge_frame candidate={.tags=packet->output[i].logical_tags};
    memcpy(candidate.tag,packet->output[i].logical_tag,sizeof(candidate.tag));
    if(fe_mcast_physical(dev,&candidate,from_cos?v:priority)!=physical || candidate.tags!=frame->tags ||
       memcmp(candidate.tag,frame->tag,sizeof(candidate.tag))){ret=-EOPNOTSUPP;goto out;}
   }
   replica->epon=1;replica->preserve_queue=from_cos;
   replica->queue=from_cos?0:priority&(fe->epon_paths.queues-1);
   if(from_cos?fe->epon_paths.queue_mask[port->llid]!=255:
      !(fe->epon_paths.queue_mask[port->llid]&BIT(replica->queue))){ret=-EOPNOTSUPP;goto out;}
   if(data.mtu>1522U-ETH_HLEN-frame->tags*VLAN_HLEN)data.mtu=1522U-ETH_HLEN-frame->tags*VLAN_HLEN;
  }
  if(port->wifi_id || unicast) {
   unsigned int c;
   u32 priority;
   bool from_cos;
   if(port->wifi_id && (port->source!=RTL_WIFI_SOURCE || port->wifi_id>RTL_WIFI_ID_MAX || port->llid!=port->wifi_id)){ret=-EOPNOTSUPP;goto out;}
   if(port->wifi_id && (!physical->ieee80211_ptr ||
      (physical->ieee80211_ptr->iftype!=NL80211_IFTYPE_AP && physical->ieee80211_ptr->iftype!=NL80211_IFTYPE_STATION) ||
      (physical->features&(NETIF_F_HW_VLAN_CTAG_TX|NETIF_F_HW_VLAN_STAG_TX)))){ret=-EOPNOTSUPP;goto out;}
   ret=fe_mcast_input_priority(fe,data.input,input,&packet->input,&priority,&from_cos);
   if(ret)goto out;
   if(from_cos?packet->output[i].priority>7:packet->output[i].priority!=priority){ret=-EOPNOTSUPP;goto out;}
   for(unsigned int v=0;v<(from_cos?8U:1U);v++) {
    struct rtl9617c_bridge_frame candidate={.tags=packet->output[i].logical_tags};
    memcpy(candidate.tag,packet->output[i].logical_tag,sizeof(candidate.tag));
    if(fe_mcast_physical(dev,&candidate,from_cos?v:priority)!=physical || candidate.tags!=frame->tags ||
       memcmp(candidate.tag,frame->tag,sizeof(candidate.tag))){ret=-EOPNOTSUPP;goto out;}
   }
   mc->direct[mc->count]=(struct rtl_fe_mc_direct){.frame=*frame,.output=dev->ifindex,
    .physical=physical->ifindex,.wifi_id=port->wifi_id,.priority=priority,.priority_from_cos=from_cos,
    .iftype=port->wifi_id?physical->ieee80211_ptr->iftype:0};
   replica->destination=16;replica->direct_cookie=mc->count+1;
   if(port->wifi_id && unicast &&
      !fe_mcast_pe_recipe(fe,port,&mc->direct[mc->count],data.input->source,packet->epoch))
    replica->destination=mc->direct[mc->count].pe_destination;
   if(existing) {
    for(c=1;c<RTL_FE_MC_GROUPS;c++) {
     const struct rtl_fe_mc_direct *d=fe->mc_directs[c];
     if(d && d->flow==existing && d->output==dev->ifindex && d->physical==physical->ifindex && d->wifi_id==port->wifi_id &&
        d->priority==priority && d->priority_from_cos==from_cos && d->iftype==mc->direct[mc->count].iftype &&
        ether_addr_equal(d->frame.destination,frame->destination) && d->frame.tags==frame->tags &&
        !memcmp(d->frame.tag,frame->tag,sizeof(frame->tag)) &&
        d->pe_destination==mc->direct[mc->count].pe_destination &&
        !memcmp(&d->pe_record,&mc->direct[mc->count].pe_record,sizeof(d->pe_record)))break;
    }
    if(c==RTL_FE_MC_GROUPS){fe_mcast_invalidate(fe);ret=-ESTALE;goto out;}
    replica->direct_cookie=c;
   }
   mc->host=&host;
   if(data.mtu>RTL_QM_DATA_BYTES-16U-ETH_HLEN-frame->tags*VLAN_HLEN)
    data.mtu=RTL_QM_DATA_BYTES-16U-ETH_HLEN-frame->tags*VLAN_HLEN;
   if(unicast && (port->source<=4 || fe_mcast_epon_port(fe,port)) &&
      data.mtu>1522U-ETH_HLEN-frame->tags*VLAN_HLEN)
    data.mtu=1522U-ETH_HLEN-frame->tags*VLAN_HLEN;
  }
  if(pon && !unicast) {
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
  .protocol=packet->input.ip_protocol,.ingress=data.input->source,.llid=data.input->wifi_id?packet->input.wifi_id:packet->input.epon?packet->input.epon_llid:packet->input.pon_gem,.mask=RTL_FE_MC_LEARNED_MASK};
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
 if(!fe->mc_gate.error && !fe->mc_gate.writers && fe->mc_gate.epoch==packet->epoch)
  fe_mcast_pe_open(fe,packet->epoch);
 ret=rtl_fe_mc_gate_open(&io,&fe->mc_gate,packet->epoch);
 if(ret)fe_mcast_pe_close(fe);
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
