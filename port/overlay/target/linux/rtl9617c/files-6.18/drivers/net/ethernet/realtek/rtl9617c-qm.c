// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Taurus L3QM CPU0/EPP64 receive path.
 * Register sequences: Cortina-Access 2015 GPL-2.0+ aal_l3qm.c; ingress
 * classification: Realtek 2020 GPL-2.0+ aal_l2_classification.c, from
 * GPL-for-GP3000 5090fbcb6ba743dd7b1314811ef557bad0460147.
 * CPU rings: 1024 bytes per VOQ, 8-byte commands, as TYFZA1 ca-ne.ko.
 */
#include <linux/bitfield.h>
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/unaligned.h>
#include "rtl9617c-qm.h"

#define EQ_ID 4
#define EQ_CFG (0x61e8 + EQ_ID * 20)
#define EQ_PROFILE (0x6128 + EQ_ID * 4)
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
#define EPP_READ 0x7100
#define EPP_WRITE 0x7000
#define EPP_BASE 0x7200
#define CPU_PUSH 0x636c
#define CPU_READY 0x6368

static int rtl_qm_indirect(struct rtl_qm_rx *rx, unsigned int reg, u32 value)
{
	u32 status;

	writel(value, rx->ni + reg);
	return readl_poll_timeout(rx->ni + reg, status, !(status & BIT(31)), 1, 100000);
}

static int rtl_qm_attribute(struct rtl_qm_rx *rx, unsigned int entry, u32 value)
{
	writel(value, rx->ni + 0x66ac);
	return rtl_qm_indirect(rx, 0x66a4, BIT(31) | BIT(30) | entry);
}

static int rtl_qm_push(struct rtl_qm_rx *rx, u32 address)
{
	u32 status;
	int ret;

	ret = readl_poll_timeout(rx->ni + CPU_READY, status, status & BIT(31), 1, 10000);
	if (ret)
		return ret;
	writel(address | EQ_ID, rx->ni + CPU_PUSH);
	return 0;
}

static int rtl_qm_ingress(struct rtl_qm_rx *rx, unsigned int port)
{
	/* ILPB and ICLS values from the Taurus CPU rule; CPU0 target is 0x42. */
	static const u32 ilpb[] = {0xc1000b80, 0xc1800deb, 0x1800a363, 0x00108003, 1};
	static const u32 key[] = {0x00007ef0, 0, 0, 0, 0, 0x02800000, 0x0001fbc0,
				 0, 0, 0, 0, 0x0a000000, 0x00000a40};
	static const u32 fib[] = {0, 0, 0x0003c000, 0x42, 0};
	unsigned int i;
	int ret;

	writel(port, rx->ni + 0x11e0);
	ret = rtl_qm_indirect(rx, 0x11dc, BIT(31) | BIT(30) | port);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(ilpb); i++)
		writel(ilpb[i], rx->ni + 0x11f8 - 4 * i);
	ret = rtl_qm_indirect(rx, 0x11e4, BIT(31) | BIT(30) | port);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(key); i++)
		writel(key[i], rx->ni + 0x1b40 - 4 * i);
	ret = rtl_qm_indirect(rx, 0x1b0c, BIT(31) | BIT(30));
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(fib); i++)
		writel(fib[i], rx->ni + 0x1b58 - 4 * i);
	ret = rtl_qm_indirect(rx, 0x1b44, BIT(31) | BIT(30));
	if (ret)
		return ret;
	/* Vendor mapping: logical CPU port 16 uses PDPID9, distinct from deep-Q8. */
	writel(9, rx->ni + 0x1658);
	ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | 16);
	if (ret)
		return ret;
	ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | 0x90);
	if (ret)
		return ret;
	ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | 0x50);
	if (ret)
		return ret;
	ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | 0xd0);
	if (ret)
		return ret;
	/* PONCLE TCAM: match this LAN source port and redirect it to CPU0. */
	writel(~(port << 6), rx->ni + 0x1e10);
	writel(~0U, rx->ni + 0x1e0c);
	writel(~0U, rx->ni + 0x1e08);
	ret = rtl_qm_indirect(rx, 0x1e04, BIT(31) | BIT(30));
	if (ret)
		return ret;
	writel(~0xfc0U | (port << 6), rx->ni + 0x1e10);
	writel(~0U, rx->ni + 0x1e0c);
	writel(~0U, rx->ni + 0x1e08);
	ret = rtl_qm_indirect(rx, 0x1e04, BIT(31) | BIT(30) | 512);
	if (ret)
		return ret;
	writel(0, rx->ni + 0x1e20);
	writel((3 << 5) | (16 << 8), rx->ni + 0x1e1c);
	writel(BIT(6), rx->ni + 0x1e18);
	ret = rtl_qm_indirect(rx, 0x1e14, BIT(31) | BIT(30));
	if (ret)
		return ret;
	writel(BIT(1), rx->ni + 0x1428);
	writel(0, rx->ni + 0x1424);
	ret = rtl_qm_indirect(rx, 0x1420, BIT(31) | BIT(30) | 16);
	if (ret)
		return ret;
	/* NI downstream demultiplexer: logical CPU ports 16..23 enter L3QM. */
	writel(readl(rx->ni + 0xa1dc) & ~0xffffU, rx->ni + 0xa1dc);
	writel(readl(rx->ni + 0xa1ec) & ~0xffffU, rx->ni + 0xa1ec);
	/* Preserve parser operation while admitting the native untagged LAN. */
	writel(readl(rx->ni + 0x1404) | BIT(0) | BIT(1), rx->ni + 0x1404);
	return 0;
}

int rtl_qm_rx_alloc(struct device *dev, struct rtl_qm_rx *rx)
{
	rx->memory = dma_alloc_coherent(dev, RTL_QM_MEMORY_BYTES, &rx->memory_dma, GFP_KERNEL);
	return rx->memory ? 0 : -ENOMEM;
}

int rtl_qm_rx_start(struct rtl_qm_rx *rx, unsigned int port)
{
	void __iomem *ni = rx->ni;
	unsigned int i;
	u32 value;
	int ret;

	writel(readl(ni + RMU_CTRL) & ~BIT(31), ni + RMU_CTRL);
	writel(readl(ni + ES_CTRL) & ~BIT(31), ni + ES_CTRL);
	writel(0, ni + 0x6110);
	writel(0, ni + 0x6114);
	memset(rx->memory, 0, RTL_QM_MEMORY_BYTES);
	memset(rx->read, 0, sizeof(rx->read));
	/* EQ4, 256 buffers, 2KiB buffer size, CPU refill, CCI pool addressing. */
	writel(1, ni + EQ_CFG);
	writel(RTL_QM_BUFFERS << 16, ni + EQ_CFG + 4);
	writel(5 | BIT(3) | (24 << 8), ni + EQ_CFG + 8);
	writel(0x61008060, ni + EQ_CFG + 12);
	writel(2, ni + EQ_CFG + 16);
	writel(EQ_ID | (EQ_ID << 4), ni + EQ_PROFILE);
	writel(EQ_ID, ni + 0x6148);
	writel(0x00020002, ni + 0x61c8); /* 32-byte first/rest headroom */
	writel(0x05102013, ni + 0x66d4);
	writel(BIT(EQ_ID), ni + 0x63a8);
	writel(0, ni + 0x63a8);
	writel(0, ni + 0x66d4);
	ret = rtl_qm_attribute(rx, EQ_ID, 0x0a008060);
	if (ret)
		return ret;
	ret = rtl_qm_attribute(rx, 48, 0x0a008060);
	if (ret)
		return ret;
	value = readl(ni + 0x6000);
	writel((value & ~(BIT(11) | BIT(13))) | 0xff, ni + 0x6000);
	writel(readl(ni + 0x68f4) | BIT(2), ni + 0x68f4);
	writel(0, ni + 0x6558);
	writel((64 << 8) | 0xf2, ni + 0x657c + EQ_ID * 4);
	for (i = 0; i < RTL_QM_VOQS; i++) {
		writel(EQ_ID, ni + 0x65a4 + i * 4);
		writel(rx->memory_dma + i * RTL_QM_RING_BYTES, ni + EPP_BASE + i * 4);
		writel(0, ni + EPP_READ + i * 4);
	}
	for (i = 0; i < RTL_QM_BUFFERS; i++) {
		ret = rtl_qm_push(rx, rx->memory_dma + RTL_QM_RING_BYTES * RTL_QM_VOQS +
				  i * RTL_QM_BUFFER_BYTES);
		if (ret)
			return ret;
	}
	writel(0xff, ni + 0x63c4);
	writel(readl(ni + 0xa1bc) & ~0xffU, ni + 0xa1bc);
	ret = rtl_qm_ingress(rx, port);
	if (ret)
		return ret;
	/* Enable both CPU forwarding and the queue-manager fabric connection. */
	writel(readl(ni + 0x2300) | BIT(8) | BIT(9), ni + 0x2300);
	writel(0xff | (6 << 16), ni + 0x2304 + 12 * 8);
	writel(0xff | (6 << 16), ni + 0x2304 + 12 * 9);
	/* CRC is stripped by QM; CPU0 dequeue and normal NI input are enabled. */
	writel(0x8062ff01, ni + ES_CTRL);
	writel(readl(ni + RMU_CTRL) | BIT(31), ni + RMU_CTRL);
	return 0;
}

void rtl_qm_rx_stop(struct rtl_qm_rx *rx)
{
	writel(readl(rx->ni + RMU_CTRL) & ~BIT(31), rx->ni + RMU_CTRL);
	writel(readl(rx->ni + ES_CTRL) & ~BIT(31), rx->ni + ES_CTRL);
}

void rtl_qm_rx_free(struct device *dev, struct rtl_qm_rx *rx)
{
	dma_free_coherent(dev, RTL_QM_MEMORY_BYTES, rx->memory, rx->memory_dma);
}

int rtl_qm_rx_poll(struct rtl_qm_rx *rx, struct napi_struct *napi, int budget)
{
	struct net_device *dev = napi->dev;
	unsigned int cos;
	int count = 0;

	for (cos = 0; cos < RTL_QM_VOQS && count < budget; cos++) {
		unsigned int wr = readl(rx->ni + EPP_WRITE + cos * 4) & 0xffffff;

		while (rx->read[cos] != wr && count < budget) {
			void *entry = rx->memory + cos * RTL_QM_RING_BYTES + rx->read[cos];
			u32 command, length_word, address, offset, h0, h1;
			unsigned int length, header;
			struct sk_buff *skb;

			dma_rmb();
			command = get_unaligned_le32(entry);
			length_word = get_unaligned_le32(entry + 4);
			address = command & 0xffffff80;
			rx->last_address = command;
			rx->last_size = length_word;
			offset = address - lower_32_bits(rx->memory_dma);
			count++;
			if (offset < RTL_QM_RING_BYTES * RTL_QM_VOQS ||
			    offset >= RTL_QM_MEMORY_BYTES || (command & 0xf) != EQ_ID) {
				dev->stats.rx_errors++;
				goto consume;
			}
			h0 = get_unaligned_be32(rx->memory + offset + 32);
			h1 = get_unaligned_be32(rx->memory + offset + 36);
			rx->last_header[0] = h0;
			rx->last_header[1] = h1;
			header = 8 + ((h0 & BIT(31)) ? 8 : 0);
			length = (h1 >> 15) & 0x3fff;
			if (h0 & BIT(31))
				length -= 8;
			if ((command & (BIT(4) | BIT(6))) != (BIT(4) | BIT(6)) ||
			    length < ETH_HLEN || length > 1522) {
				dev->stats.rx_errors++;
				goto refill;
			}
			skb = napi_alloc_skb(napi, length);
			if (!skb) {
				dev->stats.rx_dropped++;
				goto refill;
			}
			memcpy(skb_put(skb, length), rx->memory + offset + 32 + header, length);
			skb->protocol = eth_type_trans(skb, dev);
			dev->stats.rx_packets++;
			dev->stats.rx_bytes += length;
			napi_gro_receive(napi, skb);
refill:
			dma_wmb();
			rtl_qm_push(rx, address);
consume:
			rx->read[cos] = (rx->read[cos] + 8) & (RTL_QM_RING_BYTES - 1);
			writel(rx->read[cos], rx->ni + EPP_READ + cos * 4);
			wr = readl(rx->ni + EPP_WRITE + cos * 4) & 0xffffff;
		}
	}
	return count;
}

ssize_t rtl_qm_rx_state(struct rtl_qm_rx *rx, char *buf)
{
	writel(0x82, rx->ni + 0x105c);
	return sysfs_emit(buf, "memory=%pad rmu=%08x es=%08x status=%08x eqreq=%08x pushrdy=%08x epp=%08x wr0=%u rd0=%u last=%08x/%08x hdra=%08x/%08x monitor=%08x ni_demux=%08x rxcount=%u txcount=%u no_buffer=%u fe_drop=%u lenerr=%u\n",
		&rx->memory_dma, readl(rx->ni + RMU_CTRL), readl(rx->ni + ES_CTRL),
		readl(rx->ni + 0x6850), readl(rx->ni + 0x6328 + EQ_ID * 4),
		readl(rx->ni + CPU_READY), readl(rx->ni + 0x659c),
		readl(rx->ni + EPP_WRITE), readl(rx->ni + EPP_READ),
		rx->last_address, rx->last_size, rx->last_header[0], rx->last_header[1],
		readl(rx->ni + 0x1060), readl(rx->ni + 0xa1dc), readl(rx->ni + 0x67d8),
		readl(rx->ni + 0x67ec), readl(rx->ni + 0x6818), readl(rx->ni + 0x681c),
		readl(rx->ni + 0x6824));
}
