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

static void fe_mcast_pe_forget_port(struct rtl_fe *fe,struct net_device *netdev)
{
 unsigned int i;
 unsigned long flags;
 for(i=0;i<2;i++)if(fe->mc_pe[i].netdev==netdev) {
  spin_lock_irqsave(&fe->mc_lock,flags);
  fe_mcast_pe_retire(fe->mc_pe[i].table);
  fe->mc_pe[i].table=NULL;fe->mc_pe[i].netdev=NULL;
  spin_unlock_irqrestore(&fe->mc_lock,flags);
  dev_put(netdev);
 }
}

void rtl_fe_wifi_mcast_detach(struct rtl_fe *fe)
{
 unsigned int i;
 mutex_lock(&fe->lock);
 fe_mcast_invalidate(fe);
 for(i=0;i<2;i++)if(fe->mc_pe[i].netdev)
  fe_mcast_pe_forget_port(fe,fe->mc_pe[i].netdev);
 mutex_unlock(&fe->lock);
}

int rtl_fe_wifi_mcast_table(struct rtl_fe *fe,struct net_device *netdev,
 unsigned int destination,struct rtl_mc_pe_table *table,bool bind)
{
 struct rtl_fe_port *port;
 unsigned long flags;
 unsigned int slot;
 int ret=0;

 if(!fe || !table || !netdev || destination<18 || destination>19)return -EINVAL;
 slot=destination-18;
 mutex_lock(&fe->lock);
 if(bind) {
  port=fe_port(fe,netdev);
  if(!port || !port->wifi_id){ret=-ENODEV;goto out;}
  if(fe->mc_pe[slot].table) {
   if(fe->mc_pe[slot].table!=table || fe->mc_pe[slot].netdev!=netdev)ret=-EBUSY;
   goto out;
  }
  if(READ_ONCE(table->epoch)){ret=-EBUSY;goto out;}
 } else {
  if(!fe->mc_pe[slot].table)goto out;
  if(fe->mc_pe[slot].table!=table || fe->mc_pe[slot].netdev!=netdev){ret=-EPERM;goto out;}
 }
 fe_mcast_invalidate(fe);
 spin_lock_irqsave(&fe->mc_lock,flags);
 fe_mcast_pe_retire(table);
 fe->mc_pe[slot].table=bind?table:NULL;
 fe->mc_pe[slot].netdev=bind?netdev:NULL;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(bind)dev_hold(netdev);
 else dev_put(netdev);
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

 if(!fe || !policy || destination<18 || destination>19 || !macid || macid>127 || !generation ||
    policy->qos>1 || policy->tagged_tid>7 || policy->reserved ||
    (policy->flags&(BIT(16)|BIT(18)))!=(BIT(16)|BIT(18)) ||
    (policy->flags&~(BIT(16)|BIT(18)|BIT(23))) || (policy->key&65535))return -EINVAL;
 for(i=0;i<64;i++)if(policy->dscp_tid[i]>7)return -EINVAL;
 for(i=0;i<8;i++)if(policy->priority_tid[i]>7)return -EINVAL;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(!port || !port->wifi_id || fe->mc_pe[destination-18].netdev!=netdev)goto out;
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
