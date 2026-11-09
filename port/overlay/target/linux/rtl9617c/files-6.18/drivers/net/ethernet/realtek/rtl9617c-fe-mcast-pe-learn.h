/* SPDX-License-Identifier: GPL-2.0-only */
/* FE mutex held. Prepare a single immutable M2U recipe from the current
 * radio identity and Linux policy, without calling back into mac80211. */
static int fe_mcast_pe_recipe(struct rtl_fe *fe,struct rtl_fe_port *port,
 struct rtl_fe_mc_direct *direct,unsigned int source,u64 epoch)
{
 struct rtl_fe_wifi_peer *peer=fe_wifi_peer(fe,port,direct->frame.destination);
 struct rtl_mc_pe_record record={};
 const struct rtl9617c_wifi_policy *policy;
 unsigned int mode=RTL_MC_PE_TID_FIXED,tid=0;

 if(direct->iftype==NL80211_IFTYPE_AP && (direct->frame.destination[0]&1)) {
  unsigned int slot;
  struct rtl_fe_mc_binding *binding=NULL;
  if(!epoch || epoch>0xffffffffULL)return -ESTALE;
  for(slot=0;slot<2;slot++)if((binding=fe_mcast_pe_binding(fe,port->netdev,slot+18)))break;
  if(!binding || !binding->bcmc_policy_generation ||
     binding->bcmc_policy_generation!=rtl9617c_bridge_wifi_policy_generation())return -EOPNOTSUPP;
  policy=&binding->bcmc;
  record.epoch=epoch;record.control=RTL_MC_PE_BCMC|(source<<RTL_MC_PE_SOURCE_SHIFT);
  ether_addr_copy(record.destination,direct->frame.destination);
  ether_addr_copy(record.peer,port->netdev->dev_addr);
  record.key=policy->key;record.flags=policy->flags;
  record.bcmc.generation=policy->bcmc_generation;record.bcmc.sequence=policy->sequence;
  record.bcmc.key_generation=policy->key_generation;record.bcmc.rate=policy->rate;
  direct->pe_destination=slot+18;direct->pe_record=record;return 0;
 }
 if(!peer || (!is_valid_ether_addr(direct->frame.destination) &&
    !(direct->frame.destination[0]&1 && direct->iftype==NL80211_IFTYPE_STATION)) ||
    peer->destination<18 || peer->destination>19 || !peer->macid || peer->macid>127 ||
    !epoch || epoch>0xffffffffULL || !peer->policy_generation ||
    peer->policy_generation!=rtl9617c_bridge_wifi_policy_generation() ||
    !fe_mcast_pe_binding(fe,port->netdev,peer->destination))return -EOPNOTSUPP;
 policy=&peer->policy;
 if(!(policy->flags&BIT(18)) || (!(policy->flags&BIT(16)) && policy->key) ||
    (policy->flags&~(BIT(16)|BIT(18)|BIT(23))) || (policy->key&65535) ||
    policy->reserved)return -ESTALE;
 if((direct->frame.destination[0]&1) && !(policy->flags&BIT(23)))return -ESTALE;
 if(!policy->qos)tid=0;
 else if(!direct->priority_from_cos && direct->priority>=256 && direct->priority<=263)
  tid=policy->priority_tid[direct->priority-256];
 else if(direct->frame.tags)tid=policy->tagged_tid;
 else {mode=RTL_MC_PE_TID_DSCP;memcpy(record.dscp_tid,policy->dscp_tid,64);}
 record.epoch=epoch;record.control=peer->macid|(tid<<RTL_MC_PE_TID_SHIFT)|
  (mode<<RTL_MC_PE_MODE_SHIFT)|(source<<RTL_MC_PE_SOURCE_SHIFT);
 ether_addr_copy(record.destination,direct->frame.destination);
 ether_addr_copy(record.peer,peer->address);
 record.key=policy->key;record.flags=policy->flags;record.noack_map=policy->qos?policy->noack_map:0;record.qos=policy->qos;
 direct->pe_destination=peer->destination;direct->pe_record=record;
 return 0;
}

static int fe_mcast_pe_stage(struct rtl_fe *fe,const struct rtl_fe_mc_direct *direct,unsigned int cookie)
{
 struct rtl_mc_pe_table *table=fe->mc_pe[direct->pe_destination-18].table;
 /* control is the permanent tombstone even after epoch is revoked. */
 if(!table || table->record[cookie].control || fe->mc_gate.writers ||
    fe->mc_gate.epoch!=direct->pe_record.epoch)return -ESTALE;
 table->record[cookie]=direct->pe_record;
 fe->mc_pe[direct->pe_destination-18].owner[cookie]=direct->wifi_id;
 dma_wmb();
 return 0;
}

static void fe_mcast_pe_open(struct rtl_fe *fe,u64 epoch)
{
 unsigned int i;
 /* Caller already checked the gate and holds the same multicast lock. */
 if(!epoch || epoch>0xffffffffULL)return;
 dma_wmb();
 for(i=0;i<2;i++)if(fe->mc_pe[i].table)WRITE_ONCE(fe->mc_pe[i].table->epoch,(u32)epoch);
 dma_wmb();
}
