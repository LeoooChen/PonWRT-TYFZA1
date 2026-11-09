/* SPDX-License-Identifier: GPL-2.0-only */
/* ni_test_lock serializes binding; table storage survives every NI restart
 * and is freed only after FE detachment and pe_probe_stop's context ACK. */
static bool ni_mcast_bound(struct rtl_ax_tx *tx,struct net_device *netdev)
{
 if(!netdev)return false;
 for(unsigned int i=0;i<12;i++)if(tx->mcast_dev[i]==netdev)return true;
 return false;
}

static int ni_mcast_bind(struct rtl_ax_tx *tx,struct net_device *netdev)
{
 unsigned int slot=12;
 int ret;
 if(!netdev || !tx->mcast_table)return -EINVAL;
 if(get_unaligned_le32(tx->pe.memory+PE_PROBE_MAILBOX+RTL_MC_PE_CAP_MAILBOX*4)!=RTL_MC_PE_CAPABILITY)
  return -EOPNOTSUPP;
 for(unsigned int i=0;i<12;i++) {
  if(tx->mcast_dev[i]==netdev){slot=i;break;}
  if(!tx->mcast_dev[i] && slot==12)slot=i;
 }
 if(slot==12)return -ENOSPC;
 /* NETDEV_DOWN can detach FE ownership without stopping the shared NI. */
 ret=rtl9617c_wifi_mcast_table(netdev,16+tx->ni.cpu,tx->mcast_table,true);
 if(!ret && !tx->mcast_dev[slot]){dev_hold(netdev);tx->mcast_dev[slot]=netdev;}
 return ret;
}

/* Called during VIF removal with wiphy held. A failed detach retains its
 * netdev/table reference for whole-radio teardown to retry. */
int rtl_ax_tx_mcast_unbind_vif(struct rtl_ax_tx *tx,struct net_device *netdev)
{
 int ret=0;
 if(!netdev)return 0;
 mutex_lock(&ni_test_lock);
 for(unsigned int i=0;i<12;i++)if(tx->mcast_dev[i]==netdev) {
  WRITE_ONCE(tx->mcast_table->epoch,0);dma_wmb();
  ret=rtl9617c_wifi_mcast_table(netdev,16+tx->ni.cpu,tx->mcast_table,false);
  if(!ret){
   for(unsigned int j=0;j<RTL_MC_PE_BCMC_COUNT;j++)if(tx->bcmc_dev[j]==netdev)tx->bcmc_dev[j]=NULL;
   dev_put(netdev);tx->mcast_dev[i]=NULL;
  }
  break;
 }
 mutex_unlock(&ni_test_lock);
 return ret;
}

static int ni_mcast_unbind(struct rtl_ax_tx *tx)
{
 int ret;
 if(!tx->mcast_table)return 0;
 WRITE_ONCE(tx->mcast_table->epoch,0);
 dma_wmb();
 for(unsigned int i=0;i<12;i++)if(tx->mcast_dev[i]) {
  ret=rtl9617c_wifi_mcast_table(tx->mcast_dev[i],16+tx->ni.cpu,tx->mcast_table,false);
  if(ret)return ret;
  for(unsigned int j=0;j<RTL_MC_PE_BCMC_COUNT;j++)if(tx->bcmc_dev[j]==tx->mcast_dev[i])tx->bcmc_dev[j]=NULL;
  dev_put(tx->mcast_dev[i]);tx->mcast_dev[i]=NULL;
 }
 return 0;
}

static int ni_mcast_release(struct rtl_ax_tx *tx)
{
 int ret;
 mutex_lock(&ni_test_lock);
 ret=ni_mcast_unbind(tx);
 mutex_unlock(&ni_test_lock);
 return ret;
}

int rtl_ax_tx_mcast_policy(struct rtl_ax_tx *tx,struct ieee80211_vif *vif,unsigned int macid)
{
 struct rtl9617c_wifi_policy policy;
 u64 generation;
 unsigned long flags;
 u8 address[ETH_ALEN];
 int ret;

 if(!macid || macid>127)return -EINVAL;
 mutex_lock(&ni_test_lock);
 if(!tx->pe_active || !tx->ni.route_peers[macid] || !tx->peer_authorized[macid] ||
    !ni_mcast_bound(tx,ieee80211_vif_to_wdev(vif)->netdev)){ret=-ENOLINK;goto out;}
 ether_addr_copy(address,tx->pe.memory+0xe400+macid*8);
 generation=rtl9617c_bridge_wifi_policy_generation();
 ret=ieee80211_rtl9617c_mcast_policy(vif,address,&policy);
 if(ret) {
  if(ret==-EOPNOTSUPP)WRITE_ONCE(tx->mcast_policy_attempt[macid],generation);
  goto out;
 }
 spin_lock_irqsave(&tx->lock,flags);
 policy.key=get_unaligned_le32(tx->pe.memory+0xd000+macid*32+20)&0xffff0000U;
 policy.flags=get_unaligned_le32(tx->pe.memory+0xd000+macid*32+16)&(BIT(16)|BIT(18)|BIT(23));
 if(!(policy.flags&BIT(18)) || (!(policy.flags&BIT(16)) && policy.key) ||
    ((get_unaligned_le32(tx->pe.memory+0xd000+macid*32+8)>>16)&63)!=(policy.qos?26:24) ||
    !ether_addr_equal(address,tx->pe.memory+0xe400+macid*8))ret=-ESTALE;
 spin_unlock_irqrestore(&tx->lock,flags);
 if(ret)goto out;
 ret=rtl9617c_wifi_mcast_policy(ieee80211_vif_to_wdev(vif)->netdev,address,16+tx->ni.cpu,macid,&policy,generation);
 if(!ret)WRITE_ONCE(tx->mcast_policy_attempt[macid],generation);
out:
 mutex_unlock(&ni_test_lock);
 return ret;
}
