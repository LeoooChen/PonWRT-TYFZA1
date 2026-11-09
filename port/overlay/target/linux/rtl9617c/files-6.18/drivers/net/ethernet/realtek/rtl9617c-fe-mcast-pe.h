/* SPDX-License-Identifier: GPL-2.0-only */
/* FE lock -> multicast lock; never call back into the radio's NI mutex.
 * The radio owns the allocation and joins PE before freeing it. FE only
 * keeps a borrowed pointer while the explicit binding is held. */
static void fe_mcast_pe_close(struct rtl_fe *fe)
{
 unsigned int i;
 for(i=0;i<2;i++)if(fe->mc_pe[i].table)
  WRITE_ONCE(fe->mc_pe[i].table->epoch,0);
 dma_wmb();
}

/* Unlike a temporary writer gate, detachment must permanently revoke every
 * record. The retained control word forbids cookie reuse after FE reload. */
static void fe_mcast_pe_retire(struct rtl_mc_pe_table *table)
{
 unsigned int i;
 WRITE_ONCE(table->epoch,0);
 dma_wmb();
 for(i=1;i<RTL_MC_PE_COOKIES;i++)WRITE_ONCE(table->record[i].epoch,0);
 dma_wmb();
}

static struct rtl_fe_mc_binding *fe_mcast_pe_binding(struct rtl_fe *fe,
 struct net_device *netdev,unsigned int destination)
{
 if(!netdev || destination<18 || destination>19)return NULL;
 for(unsigned int i=0;i<12;i++)
  if(fe->mc_pe[destination-18].vif[i].netdev==netdev)
   return &fe->mc_pe[destination-18].vif[i];
 return NULL;
}

static void fe_mcast_pe_forget_port(struct rtl_fe *fe,struct net_device *netdev)
{
 unsigned long flags;
 struct rtl_fe_port *port=fe_port(fe,netdev);
 for(unsigned int slot=0;slot<2;slot++) {
  struct rtl_fe_mc_binding *binding=fe_mcast_pe_binding(fe,netdev,slot+18);
  struct rtl_mc_pe_table *table=fe->mc_pe[slot].table;
  bool remains=false;
  if(!binding)continue;
  spin_lock_irqsave(&fe->mc_lock,flags);
  WRITE_ONCE(table->epoch,0);
  dma_wmb();
  for(unsigned int c=1;c<RTL_MC_PE_COOKIES;c++)
   if(port && fe->mc_pe[slot].owner[c]==port->wifi_id) {
    WRITE_ONCE(table->record[c].epoch,0);
    fe->mc_pe[slot].owner[c]=0;
   }
  memset(binding,0,sizeof(*binding));
  for(unsigned int i=0;i<12;i++)if(fe->mc_pe[slot].vif[i].netdev)remains=true;
  if(!remains) {
   fe_mcast_pe_retire(table);
   fe->mc_pe[slot].table=NULL;
  }
  spin_unlock_irqrestore(&fe->mc_lock,flags);
  dev_put(netdev);
 }
}

void rtl_fe_wifi_mcast_detach(struct rtl_fe *fe)
{
 mutex_lock(&fe->lock);
 fe_mcast_invalidate(fe);
 for(unsigned int i=0;i<2;i++)for(unsigned int j=0;j<12;j++)
  if(fe->mc_pe[i].vif[j].netdev)
   fe_mcast_pe_forget_port(fe,fe->mc_pe[i].vif[j].netdev);
 mutex_unlock(&fe->lock);
}

int rtl_fe_wifi_mcast_table(struct rtl_fe *fe,struct net_device *netdev,
 unsigned int destination,struct rtl_mc_pe_table *table,bool bind)
{
 struct rtl_fe_port *port;
 struct rtl_fe_mc_binding *binding;
 unsigned long flags;
 unsigned int slot;
 int ret=0;

 if(!fe || !table || !netdev || destination<18 || destination>19)return -EINVAL;
 slot=destination-18;
 mutex_lock(&fe->lock);
 binding=fe_mcast_pe_binding(fe,netdev,destination);
 if(!bind) {
  if(!binding)goto out;
  if(fe->mc_pe[slot].table!=table){ret=-EPERM;goto out;}
  fe_mcast_invalidate(fe);
  fe_mcast_pe_forget_port(fe,netdev);
  goto out;
 }
 port=fe_port(fe,netdev);
 if(!port || !port->wifi_id){ret=-ENODEV;goto out;}
 if(fe->mc_pe[slot].table && fe->mc_pe[slot].table!=table){ret=-EBUSY;goto out;}
 if(binding)goto out;
 if(fe->mc_pe[1-slot].table==table){ret=-EBUSY;goto out;}
 /* A logical VIF belongs to only one physical PE destination. */
 if(fe_mcast_pe_binding(fe,netdev,37-destination)){ret=-EBUSY;goto out;}
 if(!fe->mc_pe[slot].table && READ_ONCE(table->epoch)){ret=-EBUSY;goto out;}
 for(unsigned int i=0;i<12;i++)if(!fe->mc_pe[slot].vif[i].netdev) {
  binding=&fe->mc_pe[slot].vif[i];break;
 }
 if(!binding){ret=-ENOSPC;goto out;}
 fe_mcast_invalidate(fe);
 spin_lock_irqsave(&fe->mc_lock,flags);
 /* New ownership retires old driver/FE incarnations. Adding a VIF to an
  * existing table preserves every other VIF's immutable record/tombstone. */
 if(!fe->mc_pe[slot].table) {
  fe_mcast_pe_retire(table);
  memset(fe->mc_pe[slot].owner,0,sizeof(fe->mc_pe[slot].owner));
 }
 fe->mc_pe[slot].table=table;
 binding->netdev=netdev;binding->bcmc_policy_generation=0;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 dev_hold(netdev);
out:
 mutex_unlock(&fe->lock);
 return ret;
}

int rtl_fe_wifi_mcast_policy(struct rtl_fe *fe,struct net_device *netdev,const u8 *address,
 unsigned int destination,unsigned int macid,const struct rtl9617c_wifi_policy *policy,u64 generation)
{
 struct rtl_fe_wifi_peer *peer;
 struct rtl_fe_port *port;
 unsigned int i;
 unsigned long flags;
 int ret=-ENOLINK;

 if(fe && policy && !macid && destination>=18 && destination<=19 && generation) {
  struct rtl_fe_mc_binding *binding;
  if(!policy->bcmc_generation || !policy->sequence || policy->qos || policy->reserved ||
     policy->flags!=(BIT(18)|BIT(13)|BIT(14)|(policy->key_generation?BIT(15):0)) ||
     (policy->key&~(0x037f0000U|RTL_MC_PE_KEY_GCMP|RTL_MC_PE_KEY_256)) || (!policy->key_generation && policy->key) || policy->rate>11)return -EINVAL;
  mutex_lock(&fe->lock);
  port=fe_port(fe,netdev);
  binding=fe_mcast_pe_binding(fe,netdev,destination);
  if(!port || !port->wifi_id || !binding)goto out;
  if(!binding->bcmc_policy_generation || memcmp(&binding->bcmc,policy,sizeof(*policy)))
   fe_mcast_invalidate(fe);
  spin_lock_irqsave(&fe->mc_lock,flags);
  if(fe->mc_gate.writers || generation!=rtl9617c_bridge_wifi_policy_generation())ret=-ESTALE;
  else {binding->bcmc=*policy;binding->bcmc_policy_generation=generation;ret=0;}
  spin_unlock_irqrestore(&fe->mc_lock,flags);
  goto out;
 }
 if(!fe || !policy || destination<18 || destination>19 || !macid || macid>127 || !generation ||
    policy->qos>1 || policy->tagged_tid>7 || policy->reserved ||
    !(policy->flags&BIT(18)) || (!(policy->flags&BIT(16)) && policy->key) ||
    (policy->flags&~(BIT(16)|BIT(18)|BIT(23))) || (policy->key&65535))return -EINVAL;
 for(i=0;i<64;i++)if(policy->dscp_tid[i]>7)return -EINVAL;
 for(i=0;i<8;i++)if(policy->priority_tid[i]>7)return -EINVAL;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(!port || !port->wifi_id || !fe_mcast_pe_binding(fe,netdev,destination))goto out;
 list_for_each_entry(peer,&fe->wifi_peers,list)
  if(peer->port==port && peer->destination==destination && ether_addr_equal(peer->address,address)) {
   /* First policy availability must also replace a previously learned CPU
    * copy; that rule otherwise bypasses further bridge learning forever. */
   if(!peer->policy_generation || peer->macid!=macid || memcmp(&peer->policy,policy,sizeof(*policy)))
    fe_mcast_invalidate(fe);
   spin_lock_irqsave(&fe->mc_lock,flags);
   if(fe->mc_gate.writers || generation!=rtl9617c_bridge_wifi_policy_generation())ret=-ESTALE;
   else {
    peer->policy=*policy;peer->macid=macid;peer->policy_generation=generation;ret=0;
   }
   spin_unlock_irqrestore(&fe->mc_lock,flags);
   break;
  }
out:
 mutex_unlock(&fe->lock);
 return ret;
}
