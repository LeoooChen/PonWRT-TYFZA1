// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Taurus TX DMA-LSO, using the native Linux DMA API and netdev queues.
 * Hardware sequence/descriptor reference: Copyright (c) Cortina-Access 2015,
 * GPL-2.0+ ca-network-drivers 8b4f4882ff0e237a1fa558fb0838e4b680bd6bd0,
 * aal-77c/aal_ni.c and ni-drv-77c/ca_ni_tx.[ch].
 * The TYFZA1 configuration selects CCI: descriptor fetch uses upper byte 2.
 * The standard Linux DMA API still allocates the underlying RAM and buffers.
 * Stock asynchronous TX publishes the producer without acknowledging EMPTY.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/if_vlan.h>
#include <linux/iopoll.h>
#include <linux/ktime.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

#include "rtl9617c-dma.h"
#include "rtl9617c-wifi-format.h"

#define CTRL 0x000
#define CTRL_ENABLE BIT(0)
#define CTRL_RESET BIT(8)
#define AXI_USER_SELECT 0x014
#define AXI_USER_PATTERN1 0x01c
#define VP_BASE 0x100
#define VP_STRIDE 0xa0
#define VP_ENABLE 0x00
#define VP_BASE_ACCESS 0x04
#define VP_BASE_HIGH 0x08
#define VP_BASE_LOW 0x0c
#define VP_WRITE 0x18
#define VP_READ 0x1c
#define VP_IRQ_ENABLE 0x64
#define VP_MISC_IRQ 0x68
#define VP_HEADER_A 0x78
#define SS_CTRL 0x900
#define AXI_CONFIG 0xd80
#define AFT_CTRL 0xf00
#define MEMORY_BYTES (RTL_DMA_COUNT * (sizeof(struct rtl_dma_descriptor) + RTL_DMA_BUFFER_SIZE))

static void __iomem *rtl_dma_vp(struct rtl_dma_tx *tx)
{
	return tx->registers + VP_BASE + tx->vp * VP_STRIDE;
}

int rtl_dma_tx_alloc(struct device *dev, struct rtl_dma_tx *tx)
{
	int ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));

	if (ret)
		return ret;
	tx->memory = dma_alloc_coherent(dev, MEMORY_BYTES, &tx->memory_dma, GFP_KERNEL);
	if (!tx->memory)
		return -ENOMEM;
	tx->descriptors = tx->memory;
	return 0;
}

int rtl_dma_tx_start(struct rtl_dma_tx *tx)
{
	void __iomem *base = tx->registers;
	void __iomem *vp = rtl_dma_vp(tx);
	unsigned int i;
	u32 value;
	int ret;

	writel(readl(base + CTRL) | CTRL_RESET, base + CTRL);
	udelay(10);
	writel(readl(base + CTRL) & ~(CTRL_RESET | CTRL_ENABLE), base + CTRL);
	writel(0, vp + VP_ENABLE);
	writel(0, vp + VP_IRQ_ENABLE);
	writel(0, vp + VP_WRITE);
	memset(tx->memory, 0, MEMORY_BYTES);
	memset(tx->owners, 0, sizeof(tx->owners));
	tx->read = 0;
	tx->write = 0;
	writel(lower_32_bits(tx->memory_dma) | RTL_DMA_DEPTH, vp + VP_BASE_LOW);
	writel(2, vp + VP_BASE_HIGH);
	writel(BIT(31) | BIT(30), vp + VP_BASE_ACCESS);
	ret = readl_poll_timeout(vp + VP_BASE_ACCESS, value, !(value & BIT(31)), 1, 100000);
	if (ret)
		return ret;
	writel((tx->port << 3) | BIT(9), vp + VP_HEADER_A);
	/* Select the same descriptor-fetch AXI pattern as the Taurus driver. */
	value = readl(base + AXI_USER_SELECT);
	value &= ~(3U << (tx->vp * 2));
	value |= 1U << (tx->vp * 2);
	writel(value, base + AXI_USER_SELECT);
	writel(0x02008080, base + AXI_USER_PATTERN1);
	value = readl(base + AXI_CONFIG);
	value = (value & ~GENMASK(14, 0)) | 0x7aff;
	writel(value, base + AXI_CONFIG);
	/* Stock aal_ni_init_tx_dma_lso sets all five DMA read-reorder IDs. */
	for (i = 0; i < 5; i++)
		writel(15, tx->axi_reorder + i * 0x80);
	readl(tx->axi_reorder + 0x200);
	writel(readl(base + SS_CTRL) | BIT(31) | BIT(tx->vp), base + SS_CTRL);
	/* The direct mode consumes Ethernet bytes, and DMA-AFT/MAC supplies FCS. */
	writel((64 << 4) | BIT(0), base + AFT_CTRL);
	writel(BIT(0), vp + VP_ENABLE);
	writel(BIT(0) | (3 << 2) | BIT(4), base + CTRL);
	return 0;
}

void rtl_dma_tx_stop(struct rtl_dma_tx *tx)
{
	void __iomem *base = tx->registers;

	writel(0, rtl_dma_vp(tx) + VP_ENABLE);
	writel(readl(base + CTRL) & ~CTRL_ENABLE, base + CTRL);
	writel(readl(base + CTRL) | CTRL_RESET, base + CTRL);
	udelay(10);
	writel(readl(base + CTRL) & ~CTRL_RESET, base + CTRL);
}

void rtl_dma_tx_free(struct device *dev, struct rtl_dma_tx *tx)
{
	dma_free_coherent(dev, MEMORY_BYTES, tx->memory, tx->memory_dma);
}

void rtl_dma_tx_complete(struct rtl_dma_tx *tx, struct net_device *dev)
{
	unsigned int hardware_read = readl(rtl_dma_vp(tx) + VP_READ) & (RTL_DMA_COUNT - 1);

	/* Taurus prefetches descriptors; the stock completion path retains two. */
	while (((hardware_read - tx->read) & (RTL_DMA_COUNT - 1)) > 2) {
		struct net_device *owner = tx->owners[tx->read];

		if (owner) {
			owner->stats.tx_packets++;
			owner->stats.tx_bytes += tx->lengths[tx->read];
		}
		tx->owners[tx->read] = NULL;
		tx->read = (tx->read + 1) & (RTL_DMA_COUNT - 1);
	}
	if (netif_queue_stopped(dev) && rtl_dma_tx_available(tx))
		netif_wake_queue(dev);
}

void rtl_dma_tx_forget(struct rtl_dma_tx *tx, struct net_device *dev)
{
	unsigned int i;

	/* Buffers remain NI-owned; retire interface references before unregister. */
	for (i = 0; i < RTL_DMA_COUNT; i++)
		if (tx->owners[i] == dev)
			tx->owners[i] = NULL;
}

netdev_tx_t rtl_dma_tx_frame(struct rtl_dma_tx *tx, struct sk_buff *skb,
			   struct net_device *dev, const struct rtl9617c_ni_frame *frame)
{
	unsigned int next = (tx->write + 1) & (RTL_DMA_COUNT - 1);
	unsigned int offset = RTL_DMA_COUNT * sizeof(*tx->descriptors) +
			      tx->write * RTL_DMA_BUFFER_SIZE;
	unsigned int prefix = frame ? frame->prefix_length : 0;
	unsigned int length = (frame && frame->raw_payload ? skb->len : max(skb->len, 60U)) + prefix;
	u32 high = frame ? frame->header_high : BIT(13);
	u32 low = frame ? frame->header_low : (16 << 9) | (tx->port << 3) | BIT(29);

	rtl_dma_tx_complete(tx, dev);
	if (!rtl_dma_tx_available(tx)) {
		netif_stop_queue(dev);
		return NETDEV_TX_BUSY;
	}
	/* Native LSO parameters + Header A, with both checksum engines disabled. */
	memset(tx->memory + offset, 0, length + 16);
	put_unaligned_le32(0, tx->memory + offset);
	put_unaligned_le32(length, tx->memory + offset + 4);
	put_unaligned_le32(high, tx->memory + offset + 8);
	if (!(low & GENMASK(28, 15)))
		low |= length << 15;
	put_unaligned_le32(low, tx->memory + offset + 12);
	if (prefix)
		memcpy(tx->memory + offset + 16, frame->prefix, prefix);
	skb_copy_bits(skb, 0, tx->memory + offset + 16 + prefix, skb->len);
	tx->descriptors[tx->write].address = cpu_to_le32(tx->memory_dma + offset);
	tx->descriptors[tx->write].control = cpu_to_le32(0x61000002U | ((length + 16) << 8));
	tx->lengths[tx->write] = skb->len;
	tx->owners[tx->write] = dev;
	dma_wmb();
	tx->write = next;
	writel(next, rtl_dma_vp(tx) + VP_WRITE);
	dev_consume_skb_any(skb);
	return NETDEV_TX_OK;
}

/* On success NI owns a copy and consumes skb; errors leave skb with the
 * caller. This is RX reinjection, so it must not stop the radio's TX queue. */
int rtl_dma_tx_wifi(struct rtl_dma_tx *tx, struct net_device *dev,
		    struct sk_buff *skb, unsigned int id)
{
	unsigned int offset, length = max(skb->len, 60U);
	u64 begin = READ_ONCE(tx->profile) ? ktime_get_ns() : 0;

	if (!id || id > RTL_WIFI_ID_MAX || skb->len < ETH_HLEN ||
	    length > RTL_DMA_BUFFER_SIZE)
		return -EINVAL;
	if (skb_is_gso(skb) || skb_vlan_tag_present(skb) ||
	    skb->ip_summed == CHECKSUM_PARTIAL)
		return -EOPNOTSUPP;
	rtl_dma_tx_complete(tx, dev);
	if (!rtl_dma_tx_available(tx))
		return -EBUSY;
	offset = RTL_DMA_COUNT * sizeof(*tx->descriptors) +
		 tx->write * RTL_DMA_BUFFER_SIZE;
	if (skb_copy_bits(skb, 0, tx->memory + offset, skb->len))
		return -EFAULT;
	memset(tx->memory + offset + skb->len, 0, length - skb->len);
	tx->descriptors[tx->write].address = cpu_to_le32(tx->memory_dma + offset);
	tx->descriptors[tx->write].control = cpu_to_le32(rtl_wifi_lookup_control(id, length));
	/* Unlike ordinary TX there is no LSO prefix or software Header A. */
	tx->owners[tx->write] = NULL;
	dma_wmb();
	tx->write = (tx->write + 1) & (RTL_DMA_COUNT - 1);
	writel(tx->write, rtl_dma_vp(tx) + VP_WRITE);
	tx->wifi_packets++;
	tx->wifi_copy_bytes += skb->len;
	if (begin)
		tx->wifi_prepare_ns += ktime_get_ns() - begin;
	dev_consume_skb_any(skb);
	return 0;
}

ssize_t rtl_dma_tx_state(struct rtl_dma_tx *tx, char *buf)
{
	void __iomem *base = tx->registers;
	void __iomem *vp = rtl_dma_vp(tx);

	return sysfs_emit(buf, "vp=%u port=%u desc=%pad sw=%u/%u hw=%u/%u ctrl=%08x vpctl=%08x ss=%08x misc=%08x base_hi=%08x axistat=%08x\n",
		tx->vp, tx->port, &tx->memory_dma, tx->read, tx->write,
		readl(vp + VP_READ), readl(vp + VP_WRITE), readl(base + CTRL),
		readl(vp + VP_ENABLE), readl(base + SS_CTRL), readl(vp + VP_MISC_IRQ),
		readl(vp + VP_BASE_HIGH), readl(base + 0xd88));
}
