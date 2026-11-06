/* SPDX-License-Identifier: GPL-2.0-only */
/* ni_test_lock serializes binding; table storage survives every NI restart
 * and is freed only after FE detachment and pe_probe_stop's context ACK. */
static int ni_mcast_bind(struct rtl_ax_tx *tx,struct net_device *netdev)
{
 int ret;
 if(get_unaligned_le32(tx->pe.memory+PE_PROBE_MAILBOX+RTL_MC_PE_CAP_MAILBOX*4)!=RTL_MC_PE_CAPABILITY)
  return -EOPNOTSUPP;
 /* The existing NI route can serve peers on another VIF. Its multicast
  * binding remains with the first VIF; do not block ordinary peer setup. */
 if(tx->mcast_dev && tx->mcast_dev!=netdev)return 0;
 /* NETDEV_DOWN may have detached the FE binding while NI stayed owned. */
 ret=rtl9617c_wifi_mcast_table(netdev,16+tx->ni.cpu,tx->mcast_table,true);
 if(!ret && !tx->mcast_dev){dev_hold(netdev);tx->mcast_dev=netdev;}
 return ret;
}

static int ni_mcast_unbind(struct rtl_ax_tx *tx)
{
 int ret;
 if(!tx->mcast_dev)return 0;
 WRITE_ONCE(tx->mcast_table->epoch,0);
 dma_wmb();
 ret=rtl9617c_wifi_mcast_table(tx->mcast_dev,16+tx->ni.cpu,tx->mcast_table,false);
 if(ret)return ret;
 dev_put(tx->mcast_dev);tx->mcast_dev=NULL;
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
    tx->mcast_dev!=ieee80211_vif_to_wdev(vif)->netdev){ret=-ENOLINK;goto out;}
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
 if((policy.flags&(BIT(16)|BIT(18)))!=(BIT(16)|BIT(18)) ||
    ((get_unaligned_le32(tx->pe.memory+0xd000+macid*32+8)>>16)&63)!=(policy.qos?26:24) ||
    !ether_addr_equal(address,tx->pe.memory+0xe400+macid*8))ret=-ESTALE;
 spin_unlock_irqrestore(&tx->lock,flags);
 if(ret)goto out;
 ret=rtl9617c_wifi_mcast_policy(tx->mcast_dev,address,16+tx->ni.cpu,macid,&policy,generation);
 if(!ret)WRITE_ONCE(tx->mcast_policy_attempt[macid],generation);
out:
 mutex_unlock(&ni_test_lock);
 return ret;
}
