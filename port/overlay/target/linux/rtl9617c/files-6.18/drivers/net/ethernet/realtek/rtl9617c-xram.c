// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * RTL9617C management XRAM Ethernet path, ported to Linux netdev/NAPI/phylib.
 * Packet format and sequences: Realtek Semiconductor Corp. 2022, GPL-2.0+,
 * GPL-for-GP3000 5090fbcb6ba743dd7b1314811ef557bad0460147,
 * bootloader/u-boot-2022.10/board/realtek_10G_PON/common/eth_ni.c.
 * This first LAN path retains the PHY setup performed by the stock bootloader.
 */
#include <linux/bitfield.h>
#include <linux/crc32.h>
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/hrtimer.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of_mdio.h>
#include <linux/of_net.h>
#include <linux/phy.h>
#include <linux/platform_device.h>
#include <linux/unaligned.h>

#include "rtl9617c-xram-format.h"

#define GLB_STATIC 0xa01c
#define PORT_TO_CPU GENMASK(3, 0)
#define MAC_ADDR0 0xa024
#define MAC_ADDR1 0xa028
#define PORT_STATIC 0xa5c0
#define PORT_RX 0xa5c8
#define PORT_TX 0xa5d4
#define PORT_STRIDE 0x90
#define RX_ADDR 0xaa20
#define TX_ADDR 0xaa24
#define XRAM_CFG 0xaa28
#define RX_READ 0xaa2c
#define RX_WRITE 0xaa30
#define TX_WRITE 0xaa44
#define TX_READ 0xaa48
#define POINTER_MASK GENMASK(9, 0)
#define CPU_DISABLE (BIT(0) | BIT(9))
#define RX_PROMISC GENMASK(15, 14)
#define XR_OWN BIT(31)
#define PKT_SIZE GENMASK(13, 0)
#define PKT_VALID BIT(19)
#define PKT_ERRORS (GENMASK(23, 20) | BIT(25) | BIT(26))

struct rtl_xram {
	struct net_device *netdev;
	void __iomem *ni;
	void __iomem *xram;
	struct napi_struct napi;
	struct hrtimer timer;
	spinlock_t tx_lock;
	u32 port;
	bool tx_pending;
	unsigned int tx_length;
	u8 tx_buffer[2048];
};

static unsigned int rtl_pointer(struct rtl_xram *priv, unsigned int reg)
{
	return readl(priv->ni + reg) & POINTER_MASK;
}

static void rtl_update(struct rtl_xram *priv, unsigned int reg, u32 mask, u32 value)
{
	writel((readl(priv->ni + reg) & ~mask) | value, priv->ni + reg);
}

static void rtl_mac_address(struct rtl_xram *priv)
{
	const u8 *mac = priv->netdev->dev_addr;

	writel(get_unaligned_be32(mac), priv->ni + MAC_ADDR0);
	rtl_update(priv, MAC_ADDR1, 0xff, mac[4]);
	rtl_update(priv, PORT_STATIC + priv->port * PORT_STRIDE,
		   GENMASK(31, 24), (u32)mac[5] << 24);
}

static void rtl_adjust_link(struct net_device *dev)
{
	phy_print_status(dev->phydev);
}

static void rtl_rx_mode(struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);
	bool accept_all = (dev->flags & (IFF_PROMISC | IFF_ALLMULTI)) ||
			  !netdev_mc_empty(dev);

	/* Taurus's management filter uses mode 1 for promiscuous receive. */
	rtl_update(priv, XRAM_CFG, RX_PROMISC, FIELD_PREP(RX_PROMISC, accept_all));
}

static int rtl_poll(struct napi_struct *napi, int budget)
{
	struct rtl_xram *priv = container_of(napi, struct rtl_xram, napi);
	struct net_device *dev = priv->netdev;
	int packets = 0;

	while (packets < budget) {
		unsigned int rd = rtl_pointer(priv, RX_READ);
		unsigned int wr = rtl_pointer(priv, RX_WRITE);
		unsigned int offset, next, size, copied;
		struct sk_buff *skb;
		u32 header, status;

		if (rd == wr)
			break;
		header = readl(priv->xram + rd * 8 + 4);
		if (header & XR_OWN)
			break;
		next = header & POINTER_MASK;
		if (next >= RTL_XRAM_RX_END || next == rd) {
			dev->stats.rx_errors++;
			writel(wr, priv->ni + RX_READ);
			break;
		}
		offset = rtl_xram_word_next(rd * 8 + 4, 0, RTL_XRAM_RX_END * 8);
		status = readl(priv->xram + offset);
		size = FIELD_GET(PKT_SIZE, status);
		packets++;
		if (!(status & PKT_VALID) || (status & PKT_ERRORS) ||
		    size < ETH_HLEN + ETH_FCS_LEN || size > 1522) {
			dev->stats.rx_errors++;
			writel(next, priv->ni + RX_READ);
			continue;
		}
		size -= ETH_FCS_LEN;
		skb = napi_alloc_skb(napi, size);
		if (!skb) {
			dev->stats.rx_dropped++;
			writel(next, priv->ni + RX_READ);
			continue;
		}
		offset = rtl_xram_word_next(offset, 0, RTL_XRAM_RX_END * 8);
		offset = rtl_xram_word_next(offset, 0, RTL_XRAM_RX_END * 8);
		for (copied = 0; copied < size; copied += 4) {
			__le32 word = cpu_to_le32(readl(priv->xram + offset));

			memcpy(skb_put(skb, min(4U, size - copied)), &word,
			       min(4U, size - copied));
			offset = rtl_xram_word_next(offset, 0, RTL_XRAM_RX_END * 8);
		}
		writel(next, priv->ni + RX_READ);
		skb->protocol = eth_type_trans(skb, dev);
		dev->stats.rx_packets++;
		dev->stats.rx_bytes += size;
		napi_gro_receive(napi, skb);
	}
	if (packets < budget)
		napi_complete_done(napi, packets);
	return packets;
}

static enum hrtimer_restart rtl_timer(struct hrtimer *timer)
{
	struct rtl_xram *priv = container_of(timer, struct rtl_xram, timer);

	napi_schedule(&priv->napi);
	spin_lock(&priv->tx_lock);
	if (priv->tx_pending && rtl_pointer(priv, TX_READ) == rtl_pointer(priv, TX_WRITE)) {
		priv->tx_pending = false;
		priv->netdev->stats.tx_packets++;
		priv->netdev->stats.tx_bytes += priv->tx_length;
		netif_wake_queue(priv->netdev);
	}
	spin_unlock(&priv->tx_lock);
	hrtimer_forward_now(timer, ms_to_ktime(1));
	return HRTIMER_RESTART;
}

static netdev_tx_t rtl_xmit(struct sk_buff *skb, struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);
	unsigned int pointer, offset, wire, i;
	unsigned long flags;
	u32 crc;

	if (skb->len < ETH_HLEN || skb->len > 1522) {
		dev->stats.tx_dropped++;
		dev_kfree_skb_any(skb);
		return NETDEV_TX_OK;
	}
	spin_lock_irqsave(&priv->tx_lock, flags);
	pointer = rtl_pointer(priv, TX_WRITE);
	if (priv->tx_pending || rtl_pointer(priv, TX_READ) != pointer) {
		netif_stop_queue(dev);
		spin_unlock_irqrestore(&priv->tx_lock, flags);
		return NETDEV_TX_BUSY;
	}
	wire = rtl_xram_wire_length(skb->len);
	memset(priv->tx_buffer, 0, ALIGN(8 + wire, 4));
	skb_copy_bits(skb, 0, priv->tx_buffer + 8, skb->len);
	crc = crc32_le(~0, priv->tx_buffer + 8, wire - 4) ^ ~0;
	put_unaligned_le32(crc, priv->tx_buffer + 8 + wire - 4);
	offset = pointer * 8 + 4;
	writel(rtl_xram_tx_header(rtl_xram_tx_next(pointer, wire), wire),
	       priv->xram + offset);
	offset = rtl_xram_word_next(offset, RTL_XRAM_TX_START * 8, RTL_XRAM_TX_END * 8);
	for (i = 0; i < wire + 8; i += 4) {
		writel(get_unaligned_le32(priv->tx_buffer + i), priv->xram + offset);
		offset = rtl_xram_word_next(offset, RTL_XRAM_TX_START * 8, RTL_XRAM_TX_END * 8);
	}
	priv->tx_pending = true;
	priv->tx_length = skb->len;
	netif_stop_queue(dev);
	wmb();
	writel(rtl_xram_tx_next(pointer, wire), priv->ni + TX_WRITE);
	spin_unlock_irqrestore(&priv->tx_lock, flags);
	dev_consume_skb_any(skb);
	return NETDEV_TX_OK;
}

static int rtl_open(struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);
	u32 config;

	rtl_update(priv, GLB_STATIC, PORT_TO_CPU, priv->port);
	rtl_mac_address(priv);
	writel((RTL_XRAM_RX_END - 1) << 16, priv->ni + RX_ADDR);
	writel((RTL_XRAM_TX_END - 1) << 16 | RTL_XRAM_TX_START, priv->ni + TX_ADDR);
	config = readl(priv->ni + XRAM_CFG) & ~RX_PROMISC;
	writel(config | CPU_DISABLE, priv->ni + XRAM_CFG);
	udelay(100);
	writel(config & ~CPU_DISABLE, priv->ni + XRAM_CFG);
	rtl_rx_mode(dev);
	rtl_update(priv, PORT_RX + priv->port * PORT_STRIDE, BIT(0), BIT(0));
	rtl_update(priv, PORT_TX + priv->port * PORT_STRIDE, BIT(0), BIT(0));
	priv->tx_pending = false;
	napi_enable(&priv->napi);
	phy_start(dev->phydev);
	netif_start_queue(dev);
	hrtimer_start(&priv->timer, ms_to_ktime(1), HRTIMER_MODE_REL);
	return 0;
}

static int rtl_stop(struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);

	netif_stop_queue(dev);
	hrtimer_cancel(&priv->timer);
	phy_stop(dev->phydev);
	napi_disable(&priv->napi);
	rtl_update(priv, XRAM_CFG, CPU_DISABLE, CPU_DISABLE);
	return 0;
}

static const struct net_device_ops rtl_netdev_ops = {
	.ndo_open = rtl_open,
	.ndo_stop = rtl_stop,
	.ndo_start_xmit = rtl_xmit,
	.ndo_set_rx_mode = rtl_rx_mode,
	.ndo_set_mac_address = eth_mac_addr,
	.ndo_validate_addr = eth_validate_addr,
};

static ssize_t xram_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct net_device *netdev = dev_get_drvdata(dev);
	struct rtl_xram *priv = netdev_priv(netdev);

	return sysfs_emit(buf, "port=%u cfg=%08x static=%08x rx=%u/%u tx=%u/%u pending=%u\n",
		priv->port, readl(priv->ni + XRAM_CFG), readl(priv->ni + GLB_STATIC),
		rtl_pointer(priv, RX_READ), rtl_pointer(priv, RX_WRITE),
		rtl_pointer(priv, TX_READ), rtl_pointer(priv, TX_WRITE), priv->tx_pending);
}
static DEVICE_ATTR_RO(xram_state);

static int rtl_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *phy;
	struct net_device *netdev;
	struct rtl_xram *priv;
	u8 mac[ETH_ALEN];
	u32 value;
	int ret;

	netdev = alloc_etherdev(sizeof(*priv));
	if (!netdev)
		return -ENOMEM;
	SET_NETDEV_DEV(netdev, dev);
	priv = netdev_priv(netdev);
	priv->netdev = netdev;
	priv->ni = devm_platform_ioremap_resource(pdev, 0);
	priv->xram = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(priv->ni) || IS_ERR(priv->xram)) {
		ret = IS_ERR(priv->ni) ? PTR_ERR(priv->ni) : PTR_ERR(priv->xram);
		goto free_netdev;
	}
	ret = of_property_read_u32(dev->of_node, "realtek,port", &priv->port);
	if (ret)
		goto free_netdev;
	value = readl(priv->ni + MAC_ADDR0);
	put_unaligned_be32(value, mac);
	mac[4] = readl(priv->ni + MAC_ADDR1);
	mac[5] = readl(priv->ni + PORT_STATIC + priv->port * PORT_STRIDE) >> 24;
	eth_hw_addr_set(netdev, mac);
	netdev->netdev_ops = &rtl_netdev_ops;
	netdev->max_mtu = ETH_DATA_LEN;
	spin_lock_init(&priv->tx_lock);
	netif_napi_add(netdev, &priv->napi, rtl_poll);
	hrtimer_setup(&priv->timer, rtl_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	phy = of_parse_phandle(dev->of_node, "phy-handle", 0);
	if (!of_phy_connect(netdev, phy, rtl_adjust_link, 0, PHY_INTERFACE_MODE_GMII)) {
		of_node_put(phy);
		ret = -EPROBE_DEFER;
		goto del_napi;
	}
	of_node_put(phy);
	ret = register_netdev(netdev);
	if (ret)
		goto disconnect_phy;
	platform_set_drvdata(pdev, netdev);
	ret = device_create_file(dev, &dev_attr_xram_state);
	if (ret)
		goto unregister_netdev;
	dev_info(dev, "%s: port %u management XRAM, PHY %08x, MAC %pM\n",
		 netdev->name, priv->port, netdev->phydev->phy_id, netdev->dev_addr);
	return 0;

unregister_netdev:
	unregister_netdev(netdev);
disconnect_phy:
	phy_disconnect(netdev->phydev);
del_napi:
	netif_napi_del(&priv->napi);
free_netdev:
	free_netdev(netdev);
	return ret;
}

static void rtl_remove(struct platform_device *pdev)
{
	struct net_device *dev = platform_get_drvdata(pdev);
	struct rtl_xram *priv = netdev_priv(dev);

	device_remove_file(&pdev->dev, &dev_attr_xram_state);
	unregister_netdev(dev);
	phy_disconnect(dev->phydev);
	netif_napi_del(&priv->napi);
	free_netdev(dev);
}

static const struct of_device_id rtl_match[] = {
	{ .compatible = "realtek,rtl9617c-xram-eth" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_match);

static struct platform_driver rtl_driver = {
	.probe = rtl_probe,
	.remove = rtl_remove,
	.driver = { .name = "rtl9617c-xram", .of_match_table = rtl_match },
};
module_platform_driver(rtl_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RTL9617C management XRAM Ethernet");
