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
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of_mdio.h>
#include <linux/of_net.h>
#include <linux/phy.h>
#include <linux/platform_device.h>
#include <linux/rcupdate.h>
#include <linux/unaligned.h>

#include "rtl9617c-xram-format.h"
#include "rtl9617c-dma.h"
#include "rtl9617c-qm.h"
#include "rtl9617c-ni.h"
#include "rtl9617c-fe.h"

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
	spinlock_t rx_lock;
	bool rx_active;
	u32 port;
	bool tx_pending;
	unsigned int tx_length;
	u8 tx_buffer[2048];
	bool dma_tx;
	struct rtl_dma_tx dma;
	bool qm_rx;
	struct rtl_qm_rx qm;
	int rx_irq;
	bool irq_requested;
	unsigned int engine_users;
	struct rtl_fe *fe;
};

int rtl9617c_ni_fe_add_port(struct device *dev,struct net_device *netdev,unsigned int llid)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 return rtl_fe_add_port(priv->fe,netdev,7,32+llid,llid);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_add_port);
void rtl9617c_ni_fe_remove_port(struct device *dev,struct net_device *netdev)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 rtl_fe_remove_port(priv->fe,netdev);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_remove_port);
int rtl9617c_ni_fe_setup_tc(struct device *dev,struct net_device *netdev,
 enum tc_setup_type type,void *data)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 return rtl_fe_setup_tc(priv->fe,netdev,type,data);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_setup_tc);
int rtl9617c_ni_fe_enable(struct device *dev)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 return rtl_fe_enable(priv->fe);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_enable);
int rtl9617c_ni_fe_disable(struct device *dev)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 return rtl_fe_disable(priv->fe);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_disable);
ssize_t rtl9617c_ni_fe_state(struct device *dev,char *buffer)
{
 struct rtl_xram *priv=netdev_priv(dev_get_drvdata(dev));
 return rtl_fe_state(priv->fe,buffer);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_fe_state);

int rtl9617c_ni_report_configure(struct device *dev, unsigned int low, unsigned int high)
{
	struct net_device *netdev = dev_get_drvdata(dev);
	struct rtl_xram *priv;

	if (!netdev)
		return -EPROBE_DEFER;
	priv = netdev_priv(netdev);
	writel((high << 14) | low, priv->ni + 0x96d4);
	return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_report_configure);

u32 rtl9617c_ni_report_state(struct device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	return readl(priv->ni + 0x96d4);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_report_state);

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

	if (priv->qm_rx)
		return;
	/* Taurus's management filter uses mode 1 for promiscuous receive. */
	rtl_update(priv, XRAM_CFG, RX_PROMISC, FIELD_PREP(RX_PROMISC, accept_all));
}

static int rtl_poll(struct napi_struct *napi, int budget)
{
	struct rtl_xram *priv = container_of(napi, struct rtl_xram, napi);
	struct net_device *dev = priv->netdev;
	int packets = 0;

	if (priv->qm_rx) {
		unsigned long flags;

		packets = rtl_qm_rx_poll(&priv->qm, napi, budget);
		/* Complete and rearm form one critical section with engine shutdown. */
		spin_lock_irqsave(&priv->rx_lock, flags);
		if (packets < budget && napi_complete_done(napi, packets) && priv->rx_active)
			rtl_update(priv, 0x6110, 0xff, 0xff);
		spin_unlock_irqrestore(&priv->rx_lock, flags);
		return packets;
	}

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

	if (!priv->qm_rx || READ_ONCE(priv->qm.refill.pending))
		napi_schedule(&priv->napi);
	spin_lock(&priv->tx_lock);
	if (priv->dma_tx) {
		struct rtl9617c_ni_endpoint *endpoint;

		rtl_dma_tx_complete(&priv->dma, priv->netdev);
		rcu_read_lock();
		endpoint = rcu_dereference(priv->qm.pon);
		if (endpoint && rtl_dma_tx_available(&priv->dma))
			endpoint->wake(endpoint);
		rcu_read_unlock();
	} else if (priv->tx_pending && rtl_pointer(priv, TX_READ) == rtl_pointer(priv, TX_WRITE)) {
		priv->tx_pending = false;
		priv->netdev->stats.tx_packets++;
		priv->netdev->stats.tx_bytes += priv->tx_length;
		netif_wake_queue(priv->netdev);
	}
	spin_unlock(&priv->tx_lock);
	hrtimer_forward_now(timer, ms_to_ktime(1));
	return HRTIMER_RESTART;
}

static irqreturn_t rtl_rx_irq(int irq, void *data)
{
	struct rtl_xram *priv = data;

	rtl_update(priv, 0x6110, 0xff, 0);
	napi_schedule_irqoff(&priv->napi);
	return IRQ_HANDLED;
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
	if (priv->dma_tx) {
		netdev_tx_t result = rtl_dma_tx_frame(&priv->dma, skb, dev, NULL);

		spin_unlock_irqrestore(&priv->tx_lock, flags);
		return result;
	}
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

static int rtl_engine_start(struct rtl_xram *priv)
{
	struct net_device *dev = priv->netdev;
	u32 config;
	int ret;

	if (priv->dma_tx) {
		/* The bootloader's XRAM path leaves the normal LAN scheduler off. */
		rtl_update(priv, 0x2208, BIT(30) | BIT(31), BIT(31));
		writel(0x08008021, priv->ni + 0x2210);
		rtl_update(priv, 0x2300, BIT(31) | BIT(priv->port),
			   BIT(31) | BIT(priv->port));
		rtl_update(priv, 0x2304 + 12 * priv->port, 0xff, 0xff);
		ret = rtl_dma_tx_start(&priv->dma);
		if (ret)
			return ret;
	}
	if (priv->qm_rx) {
		ret = rtl_qm_rx_start(&priv->qm, priv->port);
		if (ret) {
			rtl_dma_tx_stop(&priv->dma);
			return ret;
		}
		rtl_update(priv, 0xa5f8 + PORT_STRIDE * priv->port,
			   BIT(28) | GENMASK(21, 16) | BIT(31),
			   BIT(28) | (16 << 16));
	}

	rtl_update(priv, GLB_STATIC, PORT_TO_CPU, priv->dma_tx ? 0xf : priv->port);
	rtl_mac_address(priv);
	writel((RTL_XRAM_RX_END - 1) << 16, priv->ni + RX_ADDR);
	writel((RTL_XRAM_TX_END - 1) << 16 | RTL_XRAM_TX_START, priv->ni + TX_ADDR);
	config = readl(priv->ni + XRAM_CFG) & ~RX_PROMISC;
	writel(config | CPU_DISABLE, priv->ni + XRAM_CFG);
	udelay(100);
	writel(priv->qm_rx ? config | CPU_DISABLE : config & ~CPU_DISABLE,
	       priv->ni + XRAM_CFG);
	rtl_rx_mode(dev);
	rtl_update(priv, PORT_RX + priv->port * PORT_STRIDE, BIT(0), BIT(0));
	rtl_update(priv, PORT_TX + priv->port * PORT_STRIDE, BIT(0), BIT(0));
	priv->tx_pending = false;
	napi_enable(&priv->napi);
	if (priv->qm_rx) {
		unsigned long flags;

		spin_lock_irqsave(&priv->rx_lock, flags);
		priv->rx_active = true;
		rtl_update(priv, 0x6110, 0xff, 0xff);
		spin_unlock_irqrestore(&priv->rx_lock, flags);
	}
	hrtimer_start(&priv->timer, ms_to_ktime(1), HRTIMER_MODE_REL);
	return 0;
}

static void rtl_engine_stop(struct rtl_xram *priv)
{
	unsigned long flags;

	hrtimer_cancel(&priv->timer);
	if (priv->qm_rx) {
		spin_lock_irqsave(&priv->rx_lock, flags);
		priv->rx_active = false;
		rtl_update(priv, 0x6110, 0xff, 0);
		spin_unlock_irqrestore(&priv->rx_lock, flags);
		synchronize_irq(priv->rx_irq);
	}
	napi_disable(&priv->napi);
	if (priv->qm_rx) {
		/* A completing poll may release NAPI ownership before dropping rx_lock. */
		spin_lock_irqsave(&priv->rx_lock, flags);
		rtl_qm_rx_stop(&priv->qm);
		spin_unlock_irqrestore(&priv->rx_lock, flags);
	}
	if (priv->dma_tx)
		rtl_dma_tx_stop(&priv->dma);
	rtl_update(priv, XRAM_CFG, CPU_DISABLE, CPU_DISABLE);
	return;
}

static int rtl_engine_get(struct rtl_xram *priv)
{
	int ret;

	/* Both netdev open/stop paths are serialized by RTNL. */
	if (priv->engine_users) {
		priv->engine_users++;
		return 0;
	}
	ret = rtl_engine_start(priv);
	if (!ret)
		priv->engine_users = 1;
	return ret;
}

static void rtl_engine_put(struct rtl_xram *priv)
{
	if (!--priv->engine_users)
		rtl_engine_stop(priv);
}

static int rtl_open(struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);
	int ret = rtl_engine_get(priv);

	if (ret)
		return ret;
	phy_start(dev->phydev);
	netif_start_queue(dev);
	return 0;
}

static int rtl_stop(struct net_device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev);

	netif_stop_queue(dev);
	phy_stop(dev->phydev);
	rtl_engine_put(priv);
	return 0;
}

int rtl9617c_ni_pon_attach(struct device *dev, struct rtl9617c_ni_endpoint *endpoint)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	if (!priv->qm_rx || !priv->dma_tx)
		return -EOPNOTSUPP;
	rcu_assign_pointer(priv->qm.pon, endpoint);
	return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_pon_attach);

void rtl9617c_ni_pon_detach(struct device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	rcu_assign_pointer(priv->qm.pon, NULL);
	synchronize_net();
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_pon_detach);

int rtl9617c_ni_open(struct device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return rtl_engine_get(priv);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_open);

void rtl9617c_ni_close(struct device *dev, struct net_device *owner)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	unsigned long flags;

	rtl_fe_flush_port(priv->fe,owner);
	spin_lock_irqsave(&priv->tx_lock, flags);
	rtl_dma_tx_forget(&priv->dma, owner);
	spin_unlock_irqrestore(&priv->tx_lock, flags);
	rtl_engine_put(priv);
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_close);

netdev_tx_t rtl9617c_ni_transmit(struct device *dev, struct net_device *owner,
			       struct sk_buff *skb, const struct rtl9617c_ni_frame *frame)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	unsigned long flags;
	netdev_tx_t result;

	spin_lock_irqsave(&priv->tx_lock, flags);
	result = rtl_dma_tx_frame(&priv->dma, skb, owner, frame);
	spin_unlock_irqrestore(&priv->tx_lock, flags);
	return result;
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_transmit);

int rtl9617c_ni_oam_configure(struct device *dev)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	static const unsigned int index[] = {15, 0x8f, 0x4f, 0xcf};
	u32 status;
	unsigned int i;
	int ret;

	/* aal_port: NI7 ninth queue (LDPID15) enters the OAM physical port12. */
	for (i = 0; i < ARRAY_SIZE(index); i++) {
		writel(12, priv->ni + 0x1658);
		writel(BIT(31) | BIT(30) | index[i], priv->ni + 0x1654);
		ret = readl_poll_timeout(priv->ni + 0x1654, status, !(status & BIT(31)), 1, 100000);
		if (ret)
			return ret;
	}
	writel(0xff | (6 << 16), priv->ni + 0x2304 + 12 * 12);
	rtl_update(priv, 0x2300, BIT(12), BIT(12));
	return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_ni_oam_configure);

static int rtl_setup_tc(struct net_device *netdev,enum tc_setup_type type,void *data)
{
 struct rtl_xram *priv=netdev_priv(netdev);
 return rtl_fe_setup_tc(priv->fe,netdev,type,data);
}
static const struct net_device_ops rtl_netdev_ops = {
 .ndo_setup_tc = rtl_setup_tc,
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

static ssize_t dma_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return priv->dma_tx ? rtl_dma_tx_state(&priv->dma, buf) : sysfs_emit(buf, "XRAM TX\n");
}
static DEVICE_ATTR_RO(dma_state);

static ssize_t pipeline_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return sysfs_emit(buf, "l2tm_es=%08x l2tm_port0=%08x qm_eq=%08x ni_internal=%08x ni_nitx=%08x ni_qm=%08x rx_hdr=%08x/%08x/%08x rx_pkts=%u rx_out=%08x/%08x l2qm_free=%08x\n",
		readl(priv->ni + 0x2300), readl(priv->ni + 0x2304),
		readl(priv->ni + 0x2208), readl(priv->ni + 0xa1bc), readl(priv->ni + 0xa1f4),
		readl(priv->ni + 0xa120), readl(priv->ni + 0xa088),
		readl(priv->ni + 0xa08c), readl(priv->ni + 0xa090), readl(priv->ni + 0xa094),
		readl(priv->ni + 0xa10c), readl(priv->ni + 0xa110), readl(priv->ni + 0x2234));
}
static DEVICE_ATTR_RO(pipeline_state);

static ssize_t qm_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return priv->qm_rx ? rtl_qm_rx_state(&priv->qm, buf) : sysfs_emit(buf, "XRAM RX\n");
}
static DEVICE_ATTR_RO(qm_state);

static ssize_t qm_trace_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	return rtl_qm_rx_trace(&priv->qm, buf);
}

static ssize_t qm_trace_store(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	priv->qm.trace_count = 0;
	return count;
}
static DEVICE_ATTR_RW(qm_trace);

static ssize_t qm_pool_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	return rtl_qm_rx_pool(&priv->qm, buf);
}
static DEVICE_ATTR_RO(qm_pool);

static ssize_t pon_deepq_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return rtl_qm_deepq_state(&priv->qm, buf);
}
static DEVICE_ATTR_RO(pon_deepq);

static ssize_t qm_wait_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	unsigned int value = FIELD_GET(GENMASK(27, 26), readl(priv->ni + 0x6108));

	return sysfs_emit(buf, "%u\n", value);
}

static ssize_t qm_wait_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	unsigned int value;
	int ret = kstrtouint(buf, 0, &value);

	if (ret)
		return ret;
	if (value > 3)
		return -EINVAL;
	rtl_update(priv, 0x6108, GENMASK(27, 26), FIELD_PREP(GENMASK(27, 26), value));
	return count;
}
static DEVICE_ATTR_RW(qm_wait);

static ssize_t qm_es_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	return sysfs_emit(buf, "%08x\n", readl(priv->ni + 0x6108));
}

static ssize_t qm_es_store(struct device *dev, struct device_attribute *attr,
			  const char *buf, size_t count)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	u32 value;
	int ret = kstrtou32(buf, 0, &value);
	if (ret)
		return ret;
	writel(value, priv->ni + 0x6108);
	return count;
}
static DEVICE_ATTR_RW(qm_es);

static ssize_t qm_buffer_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	return sysfs_emit(buf, "%08x\n", readl(priv->ni + 0x68b0));
}

static ssize_t qm_buffer_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	u32 value;
	int ret = kstrtou32(buf, 0, &value);
	if (ret)
		return ret;
	writel(value, priv->ni + 0x68b0);
	return count;
}
static DEVICE_ATTR_RW(qm_buffer);

/* Direct ingress to CPU0 isolates NI/QM from the classification pipeline. */
static ssize_t rx_bypass_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));

	return sysfs_emit(buf, "%08x\n", readl(priv->ni + 0xa5f8 + PORT_STRIDE * priv->port));
}

static ssize_t rx_bypass_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(dev));
	bool enabled;
	int ret = kstrtobool(buf, &enabled);

	if (ret)
		return ret;
	rtl_update(priv, 0xa5f8 + PORT_STRIDE * priv->port,
		   BIT(28) | GENMASK(21, 16) | BIT(31),
		   enabled ? BIT(28) | (16 << 16) : 0);
	return count;
}
static DEVICE_ATTR_RW(rx_bypass);

static struct attribute *rtl_native_attrs[] = {
	&dev_attr_xram_state.attr,
	&dev_attr_dma_state.attr,
	&dev_attr_pipeline_state.attr,
	&dev_attr_qm_state.attr,
	&dev_attr_qm_trace.attr,
	&dev_attr_qm_pool.attr,
	&dev_attr_pon_deepq.attr,
	&dev_attr_qm_wait.attr,
	&dev_attr_qm_es.attr,
	&dev_attr_qm_buffer.attr,
	&dev_attr_rx_bypass.attr,
	NULL,
};
static umode_t rtl_native_visible(struct kobject *kobj, struct attribute *attr, int index)
{
	struct rtl_xram *priv = netdev_priv(dev_get_drvdata(kobj_to_dev(kobj)));

	if (!priv->qm_rx &&
	    (attr == &dev_attr_qm_pool.attr || attr == &dev_attr_pon_deepq.attr || attr == &dev_attr_qm_trace.attr ||
	     attr == &dev_attr_qm_wait.attr || attr == &dev_attr_qm_es.attr ||
	     attr == &dev_attr_qm_buffer.attr))
		return 0;
	return attr->mode;
}

static const struct attribute_group rtl_native_group = {
	.attrs = rtl_native_attrs,
	.is_visible = rtl_native_visible,
};

static const struct attribute_group *rtl_native_groups[] = {
	&rtl_native_group,
	NULL,
};

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
	spin_lock_init(&priv->rx_lock);
	netif_napi_add(netdev, &priv->napi, rtl_poll);
	hrtimer_setup(&priv->timer, rtl_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	phy = of_parse_phandle(dev->of_node, "phy-handle", 0);
	if (!of_phy_connect(netdev, phy, rtl_adjust_link, 0, PHY_INTERFACE_MODE_GMII)) {
		of_node_put(phy);
		ret = -EPROBE_DEFER;
		goto del_napi;
	}
	of_node_put(phy);
	priv->dma_tx = of_property_read_bool(dev->of_node, "realtek,dma-tx");
	if (priv->dma_tx) {
		priv->dma.registers = devm_platform_ioremap_resource(pdev, 2);
		if (IS_ERR(priv->dma.registers)) {
			ret = PTR_ERR(priv->dma.registers);
			goto disconnect_phy;
		}
		priv->dma.port = priv->port;
		priv->dma.vp = 8;
		ret = rtl_dma_tx_alloc(dev, &priv->dma);
		if (ret)
			goto disconnect_phy;
	}
	priv->qm_rx = of_property_read_bool(dev->of_node, "realtek,qm-rx");
	if (priv->qm_rx) {
		priv->qm.ni = priv->ni;
		priv->qm.axi_reorder = devm_platform_ioremap_resource(pdev, 3);
		if (IS_ERR(priv->qm.axi_reorder)) {
			ret = PTR_ERR(priv->qm.axi_reorder);
			goto free_dma;
		}
		priv->qm.block_reset = devm_platform_ioremap_resource(pdev, 4);
		if (IS_ERR(priv->qm.block_reset)) {
			ret = PTR_ERR(priv->qm.block_reset);
			goto free_dma;
		}
		ret = rtl_qm_rx_alloc(dev, &priv->qm);
		if (ret)
			goto free_dma;
		priv->rx_irq = platform_get_irq(pdev, 0);
		if (priv->rx_irq < 0) {
			ret = priv->rx_irq;
			goto free_dma;
		}
		rtl_update(priv, 0x6110, 0xff, 0);
		ret = request_irq(priv->rx_irq, rtl_rx_irq, 0, "rtl9617c-rx0", priv);
		if (ret)
			goto free_dma;
		priv->irq_requested = true;
	}
	if(priv->qm_rx) {
		priv->fe=rtl_fe_allocate(dev,priv->ni);
		if(IS_ERR(priv->fe)){ret=PTR_ERR(priv->fe);priv->fe=NULL;goto free_dma;}
		ret=rtl_fe_add_port(priv->fe,netdev,priv->port,priv->port,0);
		if(ret)goto free_dma;
	}
	ret = register_netdev(netdev);
	if (ret)
		goto free_dma;
	platform_set_drvdata(pdev, netdev);
	ret = sysfs_create_groups(&dev->kobj, rtl_native_groups);
	if (ret)
		goto unregister_netdev;
	dev_info(dev, "%s: port %u %s, PHY %08x, MAC %pM\n",
		 netdev->name, priv->port, priv->qm_rx ? "native NI/DMA" : "management XRAM",
		 netdev->phydev->phy_id, netdev->dev_addr);
	return 0;

unregister_netdev:
	unregister_netdev(netdev);
free_dma:
	rtl_fe_free(priv->fe);
	if (priv->irq_requested)
		free_irq(priv->rx_irq, priv);
	if (priv->qm_rx && priv->qm.memory)
		rtl_qm_rx_free(dev, &priv->qm);
	if (priv->dma_tx)
		rtl_dma_tx_free(dev, &priv->dma);
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

	sysfs_remove_groups(&pdev->dev.kobj, rtl_native_groups);
	unregister_netdev(dev);
	rtl_fe_free(priv->fe);
	if (priv->irq_requested)
		free_irq(priv->rx_irq, priv);
	if (priv->qm_rx)
		rtl_qm_rx_free(&pdev->dev, &priv->qm);
	if (priv->dma_tx)
		rtl_dma_tx_free(&pdev->dev, &priv->dma);
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
