// SPDX-License-Identifier: GPL-2.0-or-later
/* Native AF_PACKET EPON OAM transport, from RTL8277C GPL EPON DAL. */
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/netdevice.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-ni.h"
#include "rtl9617c-pon-oam-format.h"

static struct rtl9617c_pon *rtl_oam_pon(struct net_device *dev)
{
	return *(struct rtl9617c_pon **)netdev_priv(dev);
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
	unsigned int llid = (skb->priority >> 16) & 0x3f;
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
	.ndo_set_mac_address = eth_mac_addr,
	.ndo_validate_addr = eth_validate_addr,
};

static void rtl_oam_receive(struct rtl9617c_ni_endpoint *endpoint, struct napi_struct *napi,
			    struct sk_buff *skb, unsigned int source)
{
	struct rtl9617c_pon *pon = container_of(endpoint, struct rtl9617c_pon, oam_endpoint);
	struct net_device *dev = endpoint->netdev;
	unsigned int llid, offset = rtl_oam_receive_offset(skb->data, skb->len, &llid);

	if (!offset || !netif_running(dev)) {
		dev->stats.rx_dropped++;
		dev_kfree_skb_any(skb);
		return;
	}
	if (llid == 8 || llid == 10 || llid == 11 || llid == 63)
		llid = 0;
	WRITE_ONCE(pon->last_oam_llid, llid);
	WRITE_ONCE(pon->last_oam_source, source);
	skb_pull(skb, offset);
	skb->priority = llid << 16 | 7;
	dev->stats.rx_packets++;
	dev->stats.rx_bytes += skb->len;
	skb->protocol = eth_type_trans(skb, dev);
	napi_gro_receive(napi, skb);
}

void rtl_pon_oam_carrier(struct rtl9617c_pon *pon)
{
	u32 status = readl(pon->mac + 0x10);
	bool synced = !(status & BIT(0)) &&
		(status & (pon->mode == RTL_PON_EPON_1G ? BIT(2) : BIT(1)));

	if (pon->traffic_enabled && pon->mpcp_enabled && synced && pon->registered)
		netif_carrier_on(pon->oam_endpoint.netdev);
	else
		netif_carrier_off(pon->oam_endpoint.netdev);
}

int rtl_pon_oam_init(struct rtl9617c_pon *pon)
{
	struct net_device *dev = alloc_etherdev(sizeof(pon));
	int ret;

	if (!dev)
		return -ENOMEM;
	*(struct rtl9617c_pon **)netdev_priv(dev) = pon;
	SET_NETDEV_DEV(dev, pon->dev);
	strscpy(dev->name, "pon-oam%d", sizeof(dev->name));
	dev->netdev_ops = &rtl_oam_ops;
	dev->max_mtu = ETH_DATA_LEN;
	dev->flags |= IFF_NOARP;
	eth_hw_addr_random(dev);
	netif_carrier_off(dev);
	pon->oam_endpoint.netdev = dev;
	pon->oam_endpoint.receive = rtl_oam_receive;
	ret = rtl9617c_ni_pon_attach(pon->ni_dev, &pon->oam_endpoint);
	if (ret)
		goto free;
	ret = register_netdev(dev);
	if (ret) {
		rtl9617c_ni_pon_detach(pon->ni_dev);
		goto free;
	}
	dev_info(pon->dev, "%s: native EPON OAM transport via NI CPU0/VP8\n", dev->name);
	return 0;
free:
	free_netdev(dev);
	return ret;
}

void rtl_pon_oam_remove(struct rtl9617c_pon *pon)
{
	rtl9617c_ni_pon_detach(pon->ni_dev);
	unregister_netdev(pon->oam_endpoint.netdev);
	free_netdev(pon->oam_endpoint.netdev);
}
