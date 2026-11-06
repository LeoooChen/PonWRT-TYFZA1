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
