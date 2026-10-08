// SPDX-License-Identifier: GPL-2.0-or-later
/* Native AF_PACKET EPON OAM transport, from RTL8277C GPL EPON DAL. */
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/rtnetlink.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-ni.h"
#include "rtl9617c-pon-oam-format.h"

struct rtl_oam_port {
	struct rtl9617c_pon *pon;
	unsigned int llid;
};

static ssize_t llid_index_show(struct device *device, struct device_attribute *attribute, char *buf)
{
	struct rtl_oam_port *port = netdev_priv(to_net_dev(device));

	return sysfs_emit(buf, "%u\n", port->llid);
}
static DEVICE_ATTR_RO(llid_index);

static struct attribute *rtl_oam_attrs[] = {
	&dev_attr_llid_index.attr,
	NULL,
};
static const struct attribute_group rtl_oam_group = {
	.attrs = rtl_oam_attrs,
};

static struct rtl9617c_pon *rtl_oam_pon(struct net_device *dev)
{
	return ((struct rtl_oam_port *)netdev_priv(dev))->pon;
}

static int rtl_oam_open(struct net_device *dev)
{
	struct rtl9617c_pon *pon = rtl_oam_pon(dev);
	int ret = rtl9617c_ni_open(pon->ni_dev);

	if (ret)
		return ret;
	ret = rtl9617c_ni_oam_configure(pon->ni_dev);
	if (ret) {
		rtl9617c_ni_close(pon->ni_dev, dev);
		return ret;
	}
	netif_start_queue(dev);
	return 0;
}

static int rtl_oam_stop(struct net_device *dev)
{
	struct rtl9617c_pon *pon = rtl_oam_pon(dev);

	netif_stop_queue(dev);
	rtl9617c_ni_close(pon->ni_dev, dev);
	return 0;
}

static netdev_tx_t rtl_oam_transmit(struct sk_buff *skb, struct net_device *dev)
{
	struct rtl9617c_pon *pon = rtl_oam_pon(dev);
	u8 prefix[RTL_OAM_PREFIX_BYTES], ethernet[15];
	unsigned int llid = ((struct rtl_oam_port *)netdev_priv(dev))->llid;
	struct rtl9617c_ni_frame frame = {
		.header_high = BIT(13),
		.header_low = 7 | (15 << 3) | (17 << 9) | BIT(29),
		.prefix = prefix,
		.prefix_length = RTL_OAM_PREFIX_BYTES,
	};

	if (skb->len < sizeof(ethernet) || skb->len > 1522 ||
	    skb_copy_bits(skb, 0, ethernet, sizeof(ethernet)) ||
	    !rtl_oam_is_frame(ethernet, sizeof(ethernet))) {
		dev->stats.tx_dropped++;
		dev_kfree_skb_any(skb);
		return NETDEV_TX_OK;
	}
	rtl_oam_prefix(prefix, llid);
	return rtl9617c_ni_transmit(pon->ni_dev, dev, skb, &frame);
}

static const struct net_device_ops rtl_oam_ops = {
	.ndo_open = rtl_oam_open,
	.ndo_stop = rtl_oam_stop,
	.ndo_start_xmit = rtl_oam_transmit,
	.ndo_validate_addr = eth_validate_addr,
};

static void rtl_oam_receive(struct rtl9617c_ni_endpoint *endpoint, struct napi_struct *napi,
			    struct sk_buff *skb, unsigned int source)
{
	struct rtl9617c_pon *pon = container_of(endpoint, struct rtl9617c_pon, oam_endpoint);
	struct net_device *dev;
	unsigned int llid, offset = rtl_oam_receive_offset(skb->data, skb->len, &llid);

	if (!offset) {
		endpoint->netdev->stats.rx_dropped++;
		dev_kfree_skb_any(skb);
		return;
	}
	if (llid == 63 || (pon->llid_count == 1 && (llid == 8 || llid == 10 || llid == 11)))
		llid = 0;
	dev = llid < 32 ? rcu_dereference(pon->oam_devices[llid]) : NULL;
	if (!dev || !netif_running(dev)) {
		endpoint->netdev->stats.rx_dropped++;
		dev_kfree_skb_any(skb);
		return;
	}
	WRITE_ONCE(pon->last_oam_llid, llid);
	WRITE_ONCE(pon->last_oam_source, source);
	skb_pull(skb, offset);
	skb->priority = llid << 16 | 7;
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += skb->len;
	skb->protocol = eth_type_trans(skb, dev);
	napi_gro_receive(napi, skb);
}

static void rtl_oam_wake(struct rtl9617c_ni_endpoint *endpoint)
{
	struct rtl9617c_pon *pon = container_of(endpoint, struct rtl9617c_pon, oam_endpoint);
	unsigned int i;

	for (i = 0; i < 32; i++) {
		struct net_device *dev = rcu_dereference(pon->oam_devices[i]);

		if (dev && netif_running(dev) && netif_queue_stopped(dev))
			netif_wake_queue(dev);
	}
}

void rtl_pon_oam_carrier(struct rtl9617c_pon *pon)
{
	u32 status = readl(pon->mac + 0x10);
	bool synced = !(status & BIT(0)) &&
		(status & (pon->mode == RTL_PON_EPON_1G ? BIT(2) : BIT(1)));
	unsigned int i;

	rcu_read_lock();
	for (i = 0; i < 32; i++) {
		struct net_device *dev = rcu_dereference(pon->oam_devices[i]);

		if (!dev)
			continue;
		if (pon->traffic_enabled && pon->mpcp_enabled && synced && (pon->registered & BIT(i)))
			netif_carrier_on(dev);
		else
			netif_carrier_off(dev);
	}
	rcu_read_unlock();
}

void rtl_pon_oam_address(struct rtl9617c_pon *pon)
{
	unsigned int i;

	ASSERT_RTNL();
	lockdep_assert_held(&pon->control_lock);
	for (i = 0; i < 32; i++) {
		struct net_device *dev = rtnl_dereference(pon->oam_devices[i]);
		u8 mac[ETH_ALEN];

		if (dev) {
			ether_addr_copy(mac, pon->onu_mac);
			mac[5] += i; /* aal_mpcp_mac_addr_get: MAC-index increments the final byte. */
			netdev_lock(dev);
			eth_hw_addr_set(dev, mac);
			dev->addr_assign_type = NET_ADDR_SET;
			call_netdevice_notifiers(NETDEV_CHANGEADDR, dev);
			netdev_unlock(dev);
		}
	}
}

static struct net_device *rtl_oam_allocate(struct rtl9617c_pon *pon, unsigned int index)
{
	struct net_device *dev = alloc_etherdev(sizeof(struct rtl_oam_port));
	struct rtl_oam_port *port;
	u8 mac[ETH_ALEN];

	if (!dev)
		return NULL;
	port = netdev_priv(dev);
	port->pon = pon;
	port->llid = index;
	SET_NETDEV_DEV(dev, pon->dev);
	snprintf(dev->name, sizeof(dev->name), "pon-oam%u", index);
	dev->netdev_ops = &rtl_oam_ops;
	dev->sysfs_groups[0] = &rtl_oam_group;
	dev->max_mtu = ETH_DATA_LEN;
	dev->flags |= IFF_NOARP;
	if (is_valid_ether_addr(pon->onu_mac)) {
		ether_addr_copy(mac, pon->onu_mac);
		mac[5] += index;
		eth_hw_addr_set(dev, mac);
	} else {
		eth_hw_addr_random(dev);
	}
	netif_carrier_off(dev);
	return dev;
}

int rtl_pon_oam_resize(struct rtl9617c_pon *pon, unsigned int count)
{
	struct net_device *created[32] = {};
	unsigned int i;
	int ret = 0;

	rtnl_lock();
	for (i = 0; i < count; i++) {
		if (rtnl_dereference(pon->oam_devices[i]))
			continue;
		created[i] = rtl_oam_allocate(pon, i);
		if (!created[i]) {
			ret = -ENOMEM;
			goto undo;
		}
		ret = register_netdevice(created[i]);
		if (ret) {
			free_netdev(created[i]);
			created[i] = NULL;
			goto undo;
		}
		rcu_assign_pointer(pon->oam_devices[i], created[i]);
	}
	for (i = count; i < 32; i++) {
		struct net_device *dev = rtnl_dereference(pon->oam_devices[i]);

		if (!dev)
			continue;
		rcu_assign_pointer(pon->oam_devices[i], NULL);
		synchronize_net();
		unregister_netdevice(dev);
		free_netdev(dev);
	}
	rtnl_unlock();
	return 0;
undo:
	for (i = 0; i < count; i++) {
		if (!created[i])
			continue;
		rcu_assign_pointer(pon->oam_devices[i], NULL);
		synchronize_net();
		unregister_netdevice(created[i]);
		free_netdev(created[i]);
	}
	rtnl_unlock();
	return ret;
}

int rtl_pon_oam_init(struct rtl9617c_pon *pon)
{
	struct net_device *dev = rtl_oam_allocate(pon, 0);
	int ret;

	if (!dev)
		return -ENOMEM;
	pon->oam_endpoint.netdev = dev;
	pon->oam_endpoint.receive = rtl_oam_receive;
	pon->oam_endpoint.wake = rtl_oam_wake;
	rcu_assign_pointer(pon->oam_devices[0], dev);
	ret = rtl9617c_ni_pon_attach(pon->ni_dev, &pon->oam_endpoint);
	if (ret)
		goto free;
	ret = register_netdev(dev);
	if (ret) {
		rtl9617c_ni_pon_detach(pon->ni_dev);
		goto free;
	}
	dev_info(pon->dev, "%s: native per-LLID EPON OAM transport via NI CPU0/VP8\n", dev->name);
	return 0;
free:
	rcu_assign_pointer(pon->oam_devices[0], NULL);
	free_netdev(dev);
	return ret;
}

void rtl_pon_oam_remove(struct rtl9617c_pon *pon)
{
	rtl9617c_ni_pon_detach(pon->ni_dev);
	rtl_pon_oam_resize(pon, 0);
}
