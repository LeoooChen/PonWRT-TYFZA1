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
#include "rtl9617c-pon-omci-format.h"

struct rtl_oam_port {
	struct rtl9617c_pon *pon;
	unsigned int llid;
	bool control;
	bool mac_configured;
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
	int ret;

	if (((struct rtl_oam_port *)netdev_priv(dev))->control &&
	    !rtl_pon_mode_is_epon(READ_ONCE(pon->mode)))
		return -EOPNOTSUPP;
	ret = rtl9617c_ni_open(pon->ni_dev);

	if (ret)
		return ret;
	ret = ((struct rtl_oam_port *)netdev_priv(dev))->control ?
		rtl9617c_ni_oam_configure(pon->ni_dev) : 0;
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

	if (!rtl_pon_mode_is_epon(READ_ONCE(pon->mode)) || !netif_carrier_ok(dev) ||
	    skb->len < sizeof(ethernet) || skb->len > 1522 ||
	    skb_copy_bits(skb, 0, ethernet, sizeof(ethernet)) ||
	    !rtl_oam_is_frame(ethernet, sizeof(ethernet))) {
		dev->stats.tx_dropped++;
		dev_kfree_skb_any(skb);
		return NETDEV_TX_OK;
	}
	rtl_oam_prefix(prefix, llid);
	return rtl9617c_ni_transmit(pon->ni_dev, dev, skb, &frame);
}

static netdev_tx_t rtl_data_transmit(struct sk_buff *skb, struct net_device *dev)
{
	struct rtl_oam_port *port = netdev_priv(dev);
	struct rtl9617c_pon *pon = port->pon;
	unsigned int queues = READ_ONCE(pon->llid_count) > 16 ? 4 : 8;
	struct rtl9617c_ni_frame frame = {
		.header_high = BIT(30), /* Business packets use the native deep queue. */
		.header_low = (skb->priority & (queues - 1)) | ((32 + port->llid) << 3) |
			      (16 << 9) | BIT(29),
	};

	if (!rtl_pon_mode_is_epon(READ_ONCE(pon->mode)))
		return rtl_pon_itu_data_transmit(pon, skb, dev);
	if (!netif_carrier_ok(dev) || skb->len < ETH_HLEN || skb->len > 1522) {
		dev->stats.tx_dropped++;
		dev_kfree_skb_any(skb);
		return NETDEV_TX_OK;
	}
	/* Ordinary Ethernet/VLAN bytes, without the OAM control container. */
	return rtl9617c_ni_transmit(pon->ni_dev, dev, skb, &frame);
}

static const struct net_device_ops rtl_oam_ops = {
	.ndo_open = rtl_oam_open,
	.ndo_stop = rtl_oam_stop,
	.ndo_start_xmit = rtl_oam_transmit,
	.ndo_validate_addr = eth_validate_addr,
};

static int rtl_data_set_address(struct net_device *dev, void *address)
{
 struct rtl_oam_port *port = netdev_priv(dev);
 int ret = eth_mac_addr(dev, address);

 if (!ret)
  port->mac_configured = true;
 return ret;
}

static int rtl_data_setup_tc(struct net_device *dev,enum tc_setup_type type,void *data)
{
 struct rtl_oam_port *port=netdev_priv(dev);
 return rtl9617c_ni_fe_setup_tc(port->pon->ni_dev,dev,type,data);
}
static const struct net_device_ops rtl_data_ops = {
 .ndo_setup_tc = rtl_data_setup_tc,
	.ndo_open = rtl_oam_open,
	.ndo_stop = rtl_oam_stop,
	.ndo_start_xmit = rtl_data_transmit,
	.ndo_set_mac_address = rtl_data_set_address,
	.ndo_validate_addr = eth_validate_addr,
};

static void rtl_oam_receive(struct rtl9617c_ni_endpoint *endpoint, struct napi_struct *napi,
			    struct sk_buff *skb, unsigned int source)
{
	struct rtl9617c_pon *pon = container_of(endpoint, struct rtl9617c_pon, oam_endpoint);
	struct net_device *dev;
	struct net_device *drop_owner = endpoint->netdev;
	unsigned int llid, offset, flow = source >> 8;

	if (!rtl_pon_mode_is_epon(READ_ONCE(pon->mode))) {
		if (rtl_omci_receive_offset(skb->data, skb->len))
			rtl_pon_omci_receive(pon, napi, skb, source);
		else
			rtl_pon_itu_data_receive(pon, napi, skb, source);
		return;
	}
	source &= 255;
	/* PDC preserves the physical PON source. Its disabled-policer POL_ID
	 * tags normal Ethernet frames with their LLID. OAM keeps its container. */
	if (source == 7 && !rtl_oam_receive_offset(skb->data, skb->len, &llid))
		source = flow < 32 ? 32 + flow : 255;
	if (source >= 32 && source < 64) {
		llid = source - 32;
		dev = rcu_dereference(pon->data_devices[llid]);
		if (dev)
			drop_owner = dev;
		if (!dev || !netif_running(dev) || !netif_carrier_ok(dev) || skb->len < ETH_HLEN)
			goto drop;
		dev->stats.rx_packets++;
		dev->stats.rx_bytes += skb->len;
		skb->protocol = eth_type_trans(skb, dev);
		napi_gro_receive(napi, skb);
		return;
	}
	offset = rtl_oam_receive_offset(skb->data, skb->len, &llid);
	if (!offset)
		goto drop;
	if (llid == 63 || (pon->llid_count == 1 && (llid == 8 || llid == 10 || llid == 11)))
		llid = 0;
	dev = llid < 32 ? rcu_dereference(pon->oam_devices[llid]) : NULL;
	if (dev)
		drop_owner = dev;
	if (!dev || !netif_running(dev))
		goto drop;
	WRITE_ONCE(pon->last_oam_llid, llid);
	WRITE_ONCE(pon->last_oam_source, source);
	skb_pull(skb, offset);
	skb->priority = llid << 16 | 7;
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += skb->len;
	skb->protocol = eth_type_trans(skb, dev);
	napi_gro_receive(napi, skb);
	return;
drop:
	drop_owner->stats.rx_dropped++;
	dev_kfree_skb_any(skb);
}

static void rtl_oam_wake(struct rtl9617c_ni_endpoint *endpoint)
{
	struct rtl9617c_pon *pon = container_of(endpoint, struct rtl9617c_pon, oam_endpoint);
	unsigned int i;

	if (pon->omci_device && netif_running(pon->omci_device) &&
	    netif_queue_stopped(pon->omci_device))
		netif_wake_queue(pon->omci_device);
	for (i = 0; i < 32; i++) {
		struct net_device *dev = rcu_dereference(pon->oam_devices[i]);

		if (dev && netif_running(dev) && netif_queue_stopped(dev))
			netif_wake_queue(dev);
		dev = rcu_dereference(pon->data_devices[i]);
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

		if (dev) {
			if (pon->traffic_enabled && pon->mpcp_enabled && synced && (pon->registered & BIT(i)))
				netif_carrier_on(dev);
			else
				netif_carrier_off(dev);
		}
		dev = rcu_dereference(pon->data_devices[i]);
		if (dev) {
			if (pon->traffic_enabled && pon->mpcp_enabled && pon->pdc_configured &&
			    pon->puc_configured && synced && (pon->registered & BIT(i)))
				netif_carrier_on(dev);
			else
				netif_carrier_off(dev);
		}
	}
	rcu_read_unlock();
}

static void rtl_port_address(struct net_device *dev, const u8 *base, unsigned int index)
{
	u8 mac[ETH_ALEN];

	ether_addr_copy(mac, base);
	mac[5] += index;
	netdev_lock(dev);
	eth_hw_addr_set(dev, mac);
	dev->addr_assign_type = NET_ADDR_SET;
	call_netdevice_notifiers(NETDEV_CHANGEADDR, dev);
	netdev_unlock(dev);
}

void rtl_pon_oam_address(struct rtl9617c_pon *pon)
{
	unsigned int i;

	ASSERT_RTNL();
	lockdep_assert_held(&pon->control_lock);
	for (i = 0; i < 32; i++) {
		struct net_device *dev = rtnl_dereference(pon->oam_devices[i]);
		if (dev)
			rtl_port_address(dev, pon->onu_mac, i);
		dev = rtnl_dereference(pon->data_devices[i]);
		if (dev && !((struct rtl_oam_port *)netdev_priv(dev))->mac_configured)
			rtl_port_address(dev, pon->onu_mac, i);
	}
}

static struct net_device *rtl_oam_allocate(struct rtl9617c_pon *pon, unsigned int index, bool control)
{
	struct net_device *dev = alloc_etherdev(sizeof(struct rtl_oam_port));
	struct rtl_oam_port *port;
	u8 mac[ETH_ALEN];

	if (!dev)
		return NULL;
	port = netdev_priv(dev);
	port->pon = pon;
	port->llid = index;
	port->control = control;
	SET_NETDEV_DEV(dev, pon->dev);
	snprintf(dev->name, sizeof(dev->name), control ? "pon-oam%u" : "pon-data%u", index);
	dev->netdev_ops = control ? &rtl_oam_ops : &rtl_data_ops;
	dev->sysfs_groups[0] = &rtl_oam_group;
	dev->max_mtu = ETH_DATA_LEN;
	if (control)
		dev->flags |= IFF_NOARP;
	if (is_valid_ether_addr(pon->onu_mac)) {
		ether_addr_copy(mac, pon->onu_mac);
		mac[5] += index;
		eth_hw_addr_set(dev, mac);
	} else {
		eth_hw_addr_random(dev);
	}
	netif_carrier_off(dev);
	if(!control && rtl9617c_ni_fe_add_port(pon->ni_dev,dev,index)) {
		free_netdev(dev);
		return NULL;
	}
	return dev;
}
static void rtl_oam_free(struct net_device *dev)
{
 struct rtl_oam_port *port=netdev_priv(dev);
 if(!port->control)rtl9617c_ni_fe_remove_port(port->pon->ni_dev,dev);
 free_netdev(dev);
}

int rtl_pon_oam_resize(struct rtl9617c_pon *pon, unsigned int count)
{
	struct net_device *created[2][32] = {};
	struct net_device __rcu **devices;
	unsigned int role, i;
	int ret = 0;

	rtnl_lock();
	for (role = 0; role < 2; role++) {
		devices = role ? pon->data_devices : pon->oam_devices;
		for (i = 0; i < count; i++) {
			if (rtnl_dereference(devices[i]))
				continue;
			created[role][i] = rtl_oam_allocate(pon, i, role == 0);
			if (!created[role][i]) {
				ret = -ENOMEM;
				goto undo;
			}
			ret = register_netdevice(created[role][i]);
			if (ret) {
				rtl_oam_free(created[role][i]);
				created[role][i] = NULL;
				goto undo;
			}
			rcu_assign_pointer(devices[i], created[role][i]);
		}
	}
	for (role = 0; role < 2; role++) {
		devices = role ? pon->data_devices : pon->oam_devices;
		for (i = count; i < 32; i++) {
			struct net_device *dev = rtnl_dereference(devices[i]);

			if (!dev)
				continue;
			rcu_assign_pointer(devices[i], NULL);
			synchronize_net();
			unregister_netdevice(dev);
			rtl_oam_free(dev);
		}
	}
	rtnl_unlock();
	return 0;
undo:
	for (role = 0; role < 2; role++) {
		devices = role ? pon->data_devices : pon->oam_devices;
		for (i = 0; i < count; i++) {
			if (!created[role][i])
				continue;
			rcu_assign_pointer(devices[i], NULL);
			synchronize_net();
			unregister_netdevice(created[role][i]);
			rtl_oam_free(created[role][i]);
		}
	}
	rtnl_unlock();
	return ret;
}

int rtl_pon_oam_init(struct rtl9617c_pon *pon)
{
	struct net_device *dev = rtl_oam_allocate(pon, 0, true);
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
	ret = rtl_pon_oam_resize(pon, 1);
	if (ret) {
		unregister_netdev(dev);
		rtl9617c_ni_pon_detach(pon->ni_dev);
		goto free;
	}
	dev_info(pon->dev, "%s: native per-LLID EPON OAM transport via NI CPU0/VP8\n", dev->name);
	return 0;
free:
	rcu_assign_pointer(pon->oam_devices[0], NULL);
	/* Optical IRQ readers remain active independently of NI attachment. */
	synchronize_net();
	rtl_oam_free(dev);
	return ret;
}

void rtl_pon_oam_remove(struct rtl9617c_pon *pon)
{
	rtl9617c_ni_pon_detach(pon->ni_dev);
	rtl_pon_itu_paths_remove(pon);
	rtl_pon_oam_resize(pon, 0);
}
