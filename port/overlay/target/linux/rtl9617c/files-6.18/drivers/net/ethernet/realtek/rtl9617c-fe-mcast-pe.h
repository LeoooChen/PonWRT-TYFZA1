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

static void fe_mcast_pe_forget_port(struct rtl_fe *fe,struct net_device *netdev)
{
 unsigned int i;
 unsigned long flags;
 for(i=0;i<2;i++)if(fe->mc_pe[i].netdev==netdev) {
  spin_lock_irqsave(&fe->mc_lock,flags);
  WRITE_ONCE(fe->mc_pe[i].table->epoch,0);
  dma_wmb();
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
 WRITE_ONCE(table->epoch,0);
 dma_wmb();
 fe->mc_pe[slot].table=bind?table:NULL;
 fe->mc_pe[slot].netdev=bind?netdev:NULL;
 spin_unlock_irqrestore(&fe->mc_lock,flags);
 if(bind)dev_hold(netdev);
 else dev_put(netdev);
out:
 mutex_unlock(&fe->lock);
 return ret;
}
