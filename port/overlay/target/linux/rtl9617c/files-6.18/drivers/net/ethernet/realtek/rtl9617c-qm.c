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
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/rcupdate.h>
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

static int rtl_qm_push(struct rtl_qm_rx *rx, u32 address, unsigned int eqid)
{
	u32 status;
	int ret;

	/* EQM must have a released BID; PUSH FIFO readiness alone is insufficient. */
	ret = readl_poll_timeout_atomic(rx->ni + 0x6328 + eqid * 4, status,
				       status & GENMASK(13, 0), 1, 10000);
	if (ret)
		return ret;
	ret = readl_poll_timeout_atomic(rx->ni + CPU_READY, status, status & BIT(31), 1, 10000);
	if (ret)
		return ret;
	writel(address | eqid, rx->ni + CPU_PUSH);
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

static void rtl_qm_axi_reorder(struct rtl_qm_rx *rx)
{
	void __iomem *base = rx->axi_reorder;

	/* GPL aal_l3qm_axi_reo_{rd,wr}_init: deep-Q and EPP AXI ID maps. */
	writel(15, base);
	writel(BIT(31) | 12, base + 4);
	writel(BIT(28), base + 8);
	writel(BIT(28), base + 12);
	writel(BIT(31) | 13, base + 16);
	writel(15, base + 0x400);
	writel(BIT(31) | 12, base + 0x404);
	writel(BIT(28), base + 0x408);
	writel(BIT(28), base + 0x40c);
	writel(BIT(31) | 13, base + 0x410);
	writel(2, base + 0x480);
	writel(BIT(31) | 8, base + 0x484);
	writel(BIT(28), base + 0x488);
	writel(BIT(28), base + 0x48c);
	writel(BIT(31) | 9, base + 0x490);
}

int rtl_qm_rx_start(struct rtl_qm_rx *rx, unsigned int port)
{
	void __iomem *ni = rx->ni;
	unsigned int i;
	u32 value;
	int ret;

	rx->lan_source = port;
	writel(readl(ni + RMU_CTRL) & ~BIT(31), ni + RMU_CTRL);
	writel(readl(ni + ES_CTRL) & ~BIT(31), ni + ES_CTRL);
	/* Native ownership starts with the same TQM reset as the stock Linux driver. */
	writel(readl(rx->block_reset) | BIT(5), rx->block_reset);
	udelay(1000);
	writel(readl(rx->block_reset) & ~BIT(5), rx->block_reset);
	ret = readl_poll_timeout(ni + 0x6850, value, value & BIT(30), 1, 100000);
	if (ret)
		return ret;
	writel(readl(ni + RMU_CTRL) & ~BIT(31), ni + RMU_CTRL);
	writel(readl(ni + ES_CTRL) & ~BIT(31), ni + ES_CTRL);
	writel(0, ni + 0x6110);
	writel(0, ni + 0x6114);
	/* Hardware write pointers survive ndo_stop/open; retire the old queue first. */
	for (i = 0; i < RTL_QM_VOQS; i++) {
		rx->read[i] = readl(ni + EPP_WRITE + i * 4) & (RTL_QM_RING_BYTES - 1);
		writel(rx->read[i], ni + EPP_READ + i * 4);
	}
	readl(ni + EPP_READ);
	rtl_qm_axi_reorder(rx);
	memset(rx->memory, 0, RTL_QM_MEMORY_BYTES);
	rx->trace_count = 0;
	/* Unused bootloader EQs must not retain overlapping BID ownership. */
	for (i = 0; i < 16; i++) {
		writel(0, ni + 0x61e8 + i * 20);
		writel(0, ni + 0x61ec + i * 20);
	}
	/* Distinct EQ4/EQ5: each has 256 buffers and its own BID range. */
	for (i = 0; i < 2; i++) {
		unsigned int cfg = EQ_CFG + i * 20;

		writel(1, ni + cfg);
		writel((RTL_QM_BUFFERS_PER_EQ << 16) | (i * RTL_QM_BUFFERS_PER_EQ), ni + cfg + 4);
		writel(5 | BIT(3) | (24 << 8), ni + cfg + 8);
		writel(0x61008060, ni + cfg + 12);
		writel(2, ni + cfg + 16);
	}
	writel(EQ_ID | ((EQ_ID + 1) << 4), ni + EQ_PROFILE);
	writel(EQ_ID, ni + 0x6148);
	writel(0x00020002, ni + 0x61c8); /* 32-byte first/rest headroom */
	writel(0x05102013, ni + 0x66d4);
	writel(0xffff, ni + 0x63a8);
	writel(0, ni + 0x63a8);
	writel(0, ni + 0x66d4);
	ret = rtl_qm_attribute(rx, EQ_ID, 0x0a008060);
	if (ret)
		return ret;
	ret = rtl_qm_attribute(rx, EQ_ID + 1, 0x0a008060);
	if (ret)
		return ret;
	ret = rtl_qm_attribute(rx, 48, 0x0a008060);
	if (ret)
		return ret;
	value = readl(ni + 0x6000);
	writel((value & ~(BIT(11) | BIT(13))) | 0xff, ni + 0x6000);
	writel(readl(ni + 0x68f4) | BIT(2), ni + 0x68f4);
	writel(0, ni + 0x6558);
	/* Size counts four 32-bit FIFO words, including in EPP64 mode. */
	writel(((RTL_QM_RING_BYTES / 16) << 8) | 0xf2,
	       ni + 0x657c + EQ_ID * 4);
	for (i = 0; i < RTL_QM_VOQS; i++) {
		writel(EQ_ID, ni + 0x65a4 + i * 4);
		writel(rx->memory_dma + i * RTL_QM_RING_BYTES, ni + EPP_BASE + i * 4);
	}
	for (i = 0; i < RTL_QM_BUFFERS; i++) {
		ret = rtl_qm_push(rx, rx->memory_dma + RTL_QM_RING_BYTES * RTL_QM_VOQS +
				  i * RTL_QM_BUFFER_BYTES, EQ_ID + i / RTL_QM_BUFFERS_PER_EQ);
		if (ret)
			return ret;
	}
	writel(0xff, ni + 0x63c4);
	writel(readl(ni + 0xa1bc) & ~0xffU, ni + 0xa1bc);
	ret = rtl_qm_ingress(rx, port);
	if (ret)
		return ret;
	/* The vendor credit table marks counter MSBs valid during initialization. */
	for (i = 0; i < 16; i++) {
		writel(0x8e308000, ni + 0x2db8);
		ret = rtl_qm_indirect(rx, 0x2db4, BIT(31) | BIT(30) | i);
		if (ret)
			return ret;
	}
	/* Enable both CPU forwarding and the queue-manager fabric connection. */
	writel(readl(ni + 0x2300) | BIT(8) | BIT(9), ni + 0x2300);
	writel(0xff | (6 << 16), ni + 0x2304 + 12 * 8);
	writel(0xff | (6 << 16), ni + 0x2304 + 12 * 9);
	/* CRC is stripped by QM; CPU0 dequeue and normal NI input are enabled. */
	/* Keep the reset EPP wait setting, as the vendor RMW sequence does. */
	writel(0x8462ff01, ni + ES_CTRL);
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
	u32 refill[NAPI_POLL_WEIGHT];
	unsigned int refill_count = 0;
	int cos;
	int count = 0;

	/* Service PON control traffic before bulk data, matching the stock RX order. */
	for (cos = RTL_QM_VOQS - 1; cos >= 0 && count < budget; cos--) {
		unsigned int wr = readl(rx->ni + EPP_WRITE + cos * 4) & 0xffffff;
		bool full = rx->read[cos] == wr && (readl(rx->ni + 0x659c) & BIT(cos));

		while ((rx->read[cos] != wr || full) && count < budget) {
			void *entry = rx->memory + cos * RTL_QM_RING_BYTES + rx->read[cos];
			u32 command, length_word, address, offset, h0, h1;
			unsigned int length, header;
			struct sk_buff *skb;
			bool refill_buffer = false;
			full = false;

			dma_rmb();
			command = get_unaligned_le32(entry);
			length_word = get_unaligned_le32(entry + 4);
			address = command & 0xffffff80;
			rx->last_address = command;
			rx->last_size = length_word;
			offset = address - lower_32_bits(rx->memory_dma);
			count++;
			if (offset < RTL_QM_RING_BYTES * RTL_QM_VOQS ||
			    offset >= RTL_QM_MEMORY_BYTES ||
			    ((command & 0xf) != EQ_ID && (command & 0xf) != EQ_ID + 1)) {
				dev->stats.rx_errors++;
				goto consume;
			}
			refill_buffer = true;
			h0 = get_unaligned_be32(rx->memory + offset + 32);
			h1 = get_unaligned_be32(rx->memory + offset + 36);
			rx->last_header[0] = h0;
			rx->last_header[1] = h1;
			header = 8 + ((h0 & BIT(31)) ? 8 : 0);
			length = (h1 >> 15) & 0x3fff;
			if (h0 & BIT(31))
				length -= 8;
			if ((command & (BIT(4) | BIT(6))) != (BIT(4) | BIT(6)) ||
			    length < ETH_HLEN || length > RTL_QM_BUFFER_BYTES - 32 - header) {
				dev->stats.rx_errors++;
				goto consume;
			}
			skb = napi_alloc_skb(napi, length);
			if (!skb) {
				dev->stats.rx_dropped++;
				goto consume;
			}
			memcpy(skb_put(skb, length), rx->memory + offset + 32 + header, length);
			if (FIELD_GET(GENMASK(14, 9), h1) != rx->lan_source) {
				struct rtl9617c_ni_endpoint *endpoint;
				unsigned int source = FIELD_GET(GENMASK(14, 9), h1);

				rcu_read_lock();
				endpoint = rcu_dereference(rx->pon);
				if (endpoint && (source == 7 || source >= 32))
					endpoint->receive(endpoint, napi, skb, source);
				else {
					dev->stats.rx_dropped++;
					dev_kfree_skb_any(skb);
				}
				rcu_read_unlock();
				goto consume;
			}
			if (!netif_running(dev)) {
				dev->stats.rx_dropped++;
				dev_kfree_skb_any(skb);
				goto consume;
			}
			if (rx->trace_count < ARRAY_SIZE(rx->trace) && length >= 46 &&
			    get_unaligned_be16(skb->data + 12) == ETH_P_IP &&
			    skb->data[23] == IPPROTO_UDP &&
			    get_unaligned_be16(skb->data + 36) == 18081) {
				u32 *trace = rx->trace[rx->trace_count++];

				trace[0] = rx->read[cos]; trace[1] = wr;
				trace[2] = command; trace[3] = length_word;
				trace[4] = h0; trace[5] = h1;
				trace[6] = get_unaligned_be32(skb->data + 42);
			}
			skb->protocol = eth_type_trans(skb, dev);
			dev->stats.rx_packets++;
			dev->stats.rx_bytes += length;
			napi_gro_receive(napi, skb);
consume:
			/* Retire the EPP/BID before returning its RAM to the hardware pool. */
			mb();
			rx->read[cos] = (rx->read[cos] + RTL_QM_COMMAND_BYTES) & (RTL_QM_RING_BYTES - 1);
			writel(rx->read[cos], rx->ni + EPP_READ + cos * 4);
			if (refill_buffer)
				refill[refill_count++] = command;
			wr = readl(rx->ni + EPP_WRITE + cos * 4) & 0xffffff;
		}
	}
	/* Vendor receive retires the whole EPP batch before refilling released BIDs. */
	for (cos = 0; cos < refill_count; cos++)
		if (rtl_qm_push(rx, refill[cos] & 0xffffff80, refill[cos] & 0xf))
			dev->stats.rx_dropped++;
	return count;
}

ssize_t rtl_qm_rx_state(struct rtl_qm_rx *rx, char *buf)
{
	writel(0x82, rx->ni + 0x105c);
	return sysfs_emit(buf, "memory=%pad rmu=%08x es=%08x status=%08x eqreq=%08x pushrdy=%08x epp=%08x wr0=%u rd0=%u last=%08x/%08x hdra=%08x/%08x monitor=%08x ni_demux=%08x rxcount=%u txcount=%u no_buffer=%u fe_drop=%u lenerr=%u td_global=%08x td_port=%08x td_priority=%08x\n",
		&rx->memory_dma, readl(rx->ni + RMU_CTRL), readl(rx->ni + ES_CTRL),
		readl(rx->ni + 0x6850), readl(rx->ni + 0x6328 + EQ_ID * 4),
		readl(rx->ni + CPU_READY), readl(rx->ni + 0x659c),
		readl(rx->ni + EPP_WRITE), readl(rx->ni + EPP_READ),
		rx->last_address, rx->last_size, rx->last_header[0], rx->last_header[1],
		readl(rx->ni + 0x1060), readl(rx->ni + 0xa1dc), readl(rx->ni + 0x67d8),
		readl(rx->ni + 0x67ec), readl(rx->ni + 0x6818), readl(rx->ni + 0x681c),
		readl(rx->ni + 0x6824), readl(rx->ni + 0x2d60), readl(rx->ni + 0x2d64),
		readl(rx->ni + 0x2d78));
}

ssize_t rtl_qm_rx_trace(struct rtl_qm_rx *rx, char *buf)
{
	ssize_t length = 0;
	unsigned int i;

	for (i = 0; i < rx->trace_count; i++) {
		u32 *t = rx->trace[i];
		length += scnprintf(buf + length, PAGE_SIZE - length,
			"%u rd=%u wr=%u epp=%08x/%08x hdr=%08x/%08x sequence=%u\n",
			i, t[0], t[1], t[2], t[3], t[4], t[5], t[6]);
	}
	return length;
}

ssize_t rtl_qm_rx_pool(struct rtl_qm_rx *rx, char *buf)
{
	ssize_t length = 0;
	unsigned int i;

	for (i = 0; i < 2; i++) {
		unsigned int cfg = EQ_CFG + i * 20;
		unsigned int monitor = 0x66d8 + (EQ_ID + i) * 16;
		length += scnprintf(buf + length, PAGE_SIZE - length,
			"eq%u cfg=%08x/%08x/%08x/%08x/%08x mon=%08x/%08x/%08x/%08x req=%08x\n",
			EQ_ID + i, readl(rx->ni + cfg), readl(rx->ni + cfg + 4),
			readl(rx->ni + cfg + 8), readl(rx->ni + cfg + 12), readl(rx->ni + cfg + 16),
			readl(rx->ni + monitor), readl(rx->ni + monitor + 4),
			readl(rx->ni + monitor + 8), readl(rx->ni + monitor + 12),
			readl(rx->ni + 0x6328 + (EQ_ID + i) * 4));
	}
	for (i = 0; i < 8; i++) {
		int ret = rtl_qm_indirect(rx, 0x66b0, BIT(31) | i);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length, "bid%u=%08x\n", i,
				    readl(rx->ni + 0x66b4));
	}
	return length;
}
