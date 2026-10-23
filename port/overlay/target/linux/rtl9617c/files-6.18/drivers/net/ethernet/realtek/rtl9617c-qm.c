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
#include <linux/ktime.h>
#include <linux/rcupdate.h>
#include <linux/unaligned.h>
#include "rtl9617c-qm.h"
#include "rtl9617c-rx-checksum.h"

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
#define CB_CTRL 0x950c

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

static int rtl_qm_pon_destination_map(struct rtl_qm_rx *rx)
{
	unsigned int port, flags;
	int ret;

	/* LDPID32..63 must enter PDPID8 (QM) for both my-MAC and deep-Q flags. */
	for (flags = 0; flags < 256; flags += 64)
		for (port = 32; port < 64; port++) {
			writel(8, rx->ni + 0x1658);
			ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | flags | port);
			if (ret)
				return ret;
		}
	return 0;
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

static int rtl_qm_try_push(void *context, unsigned int address, unsigned int eqid)
{
	struct rtl_qm_rx *rx = context;

	/* NAPI retains busy buffers; it never waits 10ms per buffer. The engine's
	 * 1ms timer schedules another poll even if every RX buffer is pending. */
	if (!(readl(rx->ni + 0x6328 + eqid * 4) & GENMASK(13, 0)) ||
	    !(readl(rx->ni + CPU_READY) & BIT(31)))
		return -EAGAIN;
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

	/* Original aal_port_init builds both my-MAC cases: ordinary LAN
	 * LDPID maps to its physical MAC,deep-buffer traffic enters PDPID8.
	 * Port0 previously worked with the reset default;ports1..3 need their
	 * own mapping rather than inheriting port0 as the egress destination. */
	for (i = 0; i < 256; i += 64) {
		writel(i & 64 ? 8 : port, rx->ni + 0x1658);
		ret = rtl_qm_indirect(rx, 0x1654, BIT(31) | BIT(30) | i | port);
		if (ret)
			return ret;
	}
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
	ret = rtl_qm_indirect(rx, 0x1e04, BIT(31) | BIT(30) | port);
	if (ret)
		return ret;
	writel(~0xfc0U | (port << 6), rx->ni + 0x1e10);
	writel(~0U, rx->ni + 0x1e0c);
	writel(~0U, rx->ni + 0x1e08);
	ret = rtl_qm_indirect(rx, 0x1e04, BIT(31) | BIT(30) | (512 + port));
	if (ret)
		return ret;
	writel(0, rx->ni + 0x1e20);
	writel((3 << 5) | (16 << 8), rx->ni + 0x1e1c);
	writel(BIT(6), rx->ni + 0x1e18);
	ret = rtl_qm_indirect(rx, 0x1e14, BIT(31) | BIT(30) | port);
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

static unsigned int rtl_qm_wifi_chunk_bytes(unsigned int chunk)
{
	return min_t(unsigned int, RTL_QM_PE_CHUNK_BUFFERS,
		RTL_QM_PE_BUFFERS - chunk * RTL_QM_PE_CHUNK_BUFFERS) * RTL_QM_BUFFER_BYTES;
}

int rtl_qm_rx_alloc(struct device *dev, struct rtl_qm_rx *rx)
{
	rx->device = dev;
	rx->memory = dma_alloc_noncoherent(dev, RTL_QM_MEMORY_BYTES, &rx->memory_dma,
		DMA_FROM_DEVICE, GFP_KERNEL);
	if (!rx->memory)
		return -ENOMEM;
	rx->ring_memory = dma_alloc_coherent(dev, RTL_QM_RING_BYTES * RTL_QM_VOQS,
		&rx->ring_dma, GFP_KERNEL);
	if (!rx->ring_memory)
		goto free_payload;
	rx->deepq_memory = dma_alloc_coherent(dev, RTL_DQ_MEMORY_BYTES, &rx->deepq_dma, GFP_KERNEL);
	if (!rx->deepq_memory) {
		dma_free_coherent(dev, RTL_QM_RING_BYTES * RTL_QM_VOQS, rx->ring_memory, rx->ring_dma);
		rx->ring_memory = NULL;
		goto free_payload;
	}
	/* CPU EQs publish individual buffer addresses; they need no contiguous pool. */
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++)
		for (unsigned int chunk = 0; chunk < RTL_QM_PE_CHUNKS; chunk++) {
			rx->wifi_memory[pool][chunk] = dma_alloc_coherent(dev,
				rtl_qm_wifi_chunk_bytes(chunk), &rx->wifi_dma[pool][chunk], GFP_KERNEL);
			if (!rx->wifi_memory[pool][chunk]) {
				rtl_qm_rx_free(dev, rx);
				rx->memory = NULL;
				return -ENOMEM;
			}
		}
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++) {
		rx->wifi_epp[pool] = dma_alloc_coherent(dev, 8 * RTL9617C_WIFI_EPP_BYTES,
			&rx->wifi_epp_dma[pool], GFP_KERNEL);
		if (!rx->wifi_epp[pool]) {
			rtl_qm_rx_free(dev, rx);
			rx->memory = NULL;
			return -ENOMEM;
		}
	}
	return 0;
free_payload:
	dma_free_noncoherent(dev, RTL_QM_MEMORY_BYTES, rx->memory, rx->memory_dma, DMA_FROM_DEVICE);
	rx->memory = NULL;
	return -ENOMEM;
}

static unsigned int rtl_dq_read(void *context, unsigned int reg)
{
	struct rtl_qm_rx *rx = context;

	return readl(rx->ni + reg);
}

static void rtl_dq_write(void *context, unsigned int reg, unsigned int value)
{
	struct rtl_qm_rx *rx = context;

	writel(value, rx->ni + reg);
}

static int rtl_dq_indirect(void *context, unsigned int reg, unsigned int value)
{
	return rtl_qm_indirect(context, reg, value);
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

static int rtl_qm_cpu_credits(struct rtl_qm_rx *rx)
{
	unsigned int profile, cpu, voq;
	u32 select = readl(rx->ni + 0x95a0);
	int ret;

	/* Stock aal_l3qm_init_empty_buffer: VOQ occupancy 7/8, 6/8;
	 * port free-buffer hysteresis 1/8, 1/64. Profile3 belongs to PON. */
	for (profile = 0; profile < 2; profile++) {
		unsigned int count = profile ? RTL_QM_PE_BUFFERS - 4 : RTL_QM_BUFFERS_PER_EQ;

		writel((count / 8) << 16 | (count / 64), rx->ni + 0x9580 + profile * 4);
		writel(0x3fff3fff, rx->ni + 0x9590 + profile * 4);
	}
	for (cpu = 0; cpu < 4; cpu++) {
		unsigned int count0, count1, eq_profile;

		if (cpu == 1)
			continue;
		profile = cpu ? 1 : 0;
		count0 = cpu ? RTL_QM_PE_BUFFERS - 4 : RTL_QM_BUFFERS_PER_EQ;
		count1 = cpu ? 0 : RTL_QM_BUFFERS_PER_EQ;
		eq_profile = cpu ? cpu + 4 : EQ_ID;
		/* CB scans these profiles even before a radio enables EPP dequeue. */
		if (cpu) {
			unsigned int eq = RTL_QM_PE_EQ + (3 - cpu);

			writel(eq | (15 << 4), rx->ni + 0x6128 + eq_profile * 4);
			writel(eq_profile, rx->ni + 0x6148 + cpu * 4);
		}
		writel(count0 | BIT(14) | (count1 << 16) | BIT(30), rx->ni + 0x95ac);
		ret = rtl_qm_indirect(rx, 0x95a8, BIT(31) | BIT(30) | cpu);
		if (ret)
			return ret;
		select = (select & ~(3U << (cpu * 2))) | (profile << (cpu * 2));
		/* TYFZA1 default_scfg sets both CPU EQ-profile thresholds to 12. */
		writel(12 | (12 << 16), rx->ni + 0x9560 + eq_profile * 4);
		for (voq = cpu * 8; voq < cpu * 8 + 8; voq++) {
			if (cpu)
				writel(eq_profile, rx->ni + 0x65a4 + voq * 4);
			/* CPU2 has stock private=12; CPU0/3 have private=3.
			 * Separate CPU3's native large pool from CPU0's thresholds. */
			writel(cpu == 3 ? 4 : profile, rx->ni + 0x95c4);
			ret = rtl_qm_indirect(rx, 0x95c0, BIT(31) | BIT(30) | voq);
			if (ret)
				return ret;
		}
	}
	for (profile = 0; profile < 3; profile++) {
		unsigned int count = profile ? RTL_QM_PE_BUFFERS - 4 : RTL_QM_BUFFERS_PER_EQ;

		/* Original CB init sets private credits; empty-buffer init changes
		 * only high/low occupancy. Group fields retain their reset value0. */
		writel(0, rx->ni + 0x95b4);
		writel((profile == 1 ? 12 : 3) << 24, rx->ni + 0x95b8);
		writel(((count * 7 / 8) << 14) | (count * 6 / 8), rx->ni + 0x95bc);
		ret = rtl_qm_indirect(rx, 0x95b0, BIT(31) | BIT(30) | (profile == 2 ? 4 : profile));
		if (ret)
			return ret;
	}
	writel(select, rx->ni + 0x95a0);
	return 0;
}

int rtl_qm_rx_start(struct rtl_qm_rx *rx, unsigned int port)
{
	void __iomem *ni = rx->ni;
	unsigned int i;
	u32 value;
	int ret;
	const struct rtl_dq_io dq = {rx, rtl_dq_read, rtl_dq_write, rtl_dq_indirect};

	rx->deepq_configured = false;
	writel(readl(ni + CB_CTRL) & ~BIT(31), ni + CB_CTRL);
	writel(readl(ni + RMU_CTRL) & ~BIT(31), ni + RMU_CTRL);
	writel(readl(ni + ES_CTRL) & ~BIT(31), ni + ES_CTRL);
	/* Native ownership starts with the same TQM reset as the stock Linux driver. */
	writel(readl(rx->block_reset) | BIT(5), rx->block_reset);
	udelay(1000);
	writel(readl(rx->block_reset) & ~BIT(5), rx->block_reset);
	ret = readl_poll_timeout(ni + 0x6850, value, value & BIT(30), 1, 100000);
	if (ret)
		return ret;
	/* TQM reset invalidates old BIDs; all buffers are loaded anew below. */
	memset(&rx->refill, 0, sizeof(rx->refill));
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
	memset(rx->ring_memory, 0, RTL_QM_RING_BYTES * RTL_QM_VOQS);
	dma_sync_single_for_device(rx->device, rx->memory_dma, RTL_QM_MEMORY_BYTES, DMA_FROM_DEVICE);
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
	/* Reserve separate radio pools before the TQM BID caches start.
	 * CPU2/3 dequeue starts when PE owns the corresponding EPP rings. */
	for (i = 0; i < RTL_QM_PE_POOLS; i++) {
		unsigned int cfg = 0x61e8 + (RTL_QM_PE_EQ + i) * 20;
		writel(1, ni + cfg);
		writel(RTL_QM_PE_BUFFERS << 16 | (RTL_QM_PE_BID_START + i * RTL_QM_PE_BUFFERS), ni + cfg + 4);
		writel(5 | BIT(3) | (24 << 8), ni + cfg + 8);
		writel(0x61008060, ni + cfg + 12);
		writel(2, ni + cfg + 16);
	}
	ret = rtl_qm_deepq_prepare(&dq, lower_32_bits(rx->deepq_dma));
	if (ret)
		return ret;
	writel(EQ_ID | ((EQ_ID + 1) << 4), ni + EQ_PROFILE);
	writel(EQ_ID, ni + 0x6148);
	writel(RTL_QM_PACKET_LAYOUT, ni + 0x61c8);
	writel(0x05102013, ni + 0x66d4);
	writel(0xffff, ni + 0x63a8);
	writel(0, ni + 0x63a8);
	writel(0, ni + 0x66d4);
	/* Stock startup uses separate AXI attributes for EQs and CPU EPPs. */
	for (i = 0; i < 16; i++) {
		ret = rtl_qm_attribute(rx, i, 0);
		if (ret)
			return ret;
	}
	ret = rtl_qm_attribute(rx, 48, 0x0a008060);
	if (ret)
		return ret;
	value = readl(ni + 0x6000);
	writel((value & ~(BIT(11) | BIT(13))) | 0xff, ni + 0x6000);
	writel(readl(ni + 0x68f4) | BIT(2), ni + 0x68f4);
	writel(0, ni + 0x6558);
	/* Stock preserves max_buf_cnt_per_pkt while setting size/coalescing. */
	writel((readl(ni + 0x657c + EQ_ID * 4) & GENMASK(31, 29)) |
	       ((RTL_QM_RING_BYTES / 16) << 8) | 0xf2,
	       ni + 0x657c + EQ_ID * 4);
	for (i = 0; i < RTL_QM_VOQS; i++) {
		writel(EQ_ID, ni + 0x65a4 + i * 4);
		writel(rx->ring_dma + i * RTL_QM_RING_BYTES, ni + EPP_BASE + i * 4);
	}
	for (i = 0; i < RTL_QM_BUFFERS; i++) {
		ret = rtl_qm_push(rx, rx->memory_dma + RTL_QM_RING_BYTES * RTL_QM_VOQS +
				  i * RTL_QM_BUFFER_BYTES, EQ_ID + i / RTL_QM_BUFFERS_PER_EQ);
		if (ret)
			return ret;
	}
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++)
		for (i = 0; i < RTL_QM_PE_BUFFERS - 4; i++) {
			ret = rtl_qm_push(rx, rx->wifi_dma[pool][i / RTL_QM_PE_CHUNK_BUFFERS] +
				(i % RTL_QM_PE_CHUNK_BUFFERS) * RTL_QM_BUFFER_BYTES, RTL_QM_PE_EQ + pool);
			if (ret)
				return ret;
		}
	/* A sleeping radio can retain every buffer in one COS. Its EPP ring
	 * must hold at least the per-port admission limit without a false wrap. */
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++) {
		unsigned int cpu = 3 - pool, profile = 7 - pool;
		writel(RTL_QM_PACKET_LAYOUT, ni + 0x61c8 + cpu * 4);
		memset(rx->wifi_epp[pool], 0, 8 * RTL9617C_WIFI_EPP_BYTES);
		writel((readl(ni + 0x657c + profile * 4) & GENMASK(31, 29)) |
		       (RTL9617C_WIFI_EPP_BYTES / 16) << 8 | 0xf2, ni + 0x657c + profile * 4);
		for (i = 0; i < 8; i++) {
			writel(rx->wifi_epp_dma[pool] + i * RTL9617C_WIFI_EPP_BYTES,
				ni + EPP_BASE + cpu * 32 + i * 4);
			writel(readl(ni + EPP_WRITE + cpu * 32 + i * 4),
				ni + EPP_READ + cpu * 32 + i * 4);
		}
	}
	ret = rtl_qm_cpu_credits(rx);
	if (ret)
		return ret;
	writel(0xff, ni + 0x63c4);
	writel(readl(ni + 0xa1bc) & ~0xffU, ni + 0xa1bc);
	/* All registered LAN sources use the shared CPU0 receive pool. */
	for (i = 0; i < ARRAY_SIZE(rx->lan); i++)
		if (rcu_access_pointer(rx->lan[i])) {
			ret = rtl_qm_ingress(rx, i);
			if (ret)
				return ret;
		}
	ret = rtl_qm_pon_destination_map(rx);
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
	/* Stock aal_l3_te_init starts CB scanning after credit initialization. */
	writel(readl(ni + CB_CTRL) | BIT(31), ni + CB_CTRL);
	/* The CB conditions only govern admission when L3 tail-drop is enabled. */
	writel(readl(ni + 0x9004) | BIT(2), ni + 0x9004);
	/* CRC is stripped by QM; CPU0 dequeue and normal NI input are enabled. */
	/* Keep the reset EPP wait setting, as the vendor RMW sequence does. */
	writel(0x8462ff01, ni + ES_CTRL);
	writel(readl(ni + 0x610c) | 0xffffU, ni + 0x610c);
	writel(readl(ni + RMU_CTRL) | BIT(31), ni + RMU_CTRL);
	rx->deepq_configured = true;
	return 0;
}

void rtl_qm_rx_stop(struct rtl_qm_rx *rx)
{
	rx->deepq_configured = false;
	writel(readl(rx->ni + CB_CTRL) & ~BIT(31), rx->ni + CB_CTRL);
	writel(readl(rx->ni + 0x610c) & ~0xffffU, rx->ni + 0x610c);
	writel(readl(rx->ni + RMU_CTRL) & ~BIT(31), rx->ni + RMU_CTRL);
	writel(readl(rx->ni + ES_CTRL) & ~BIT(31), rx->ni + ES_CTRL);
}

void rtl_qm_rx_free(struct device *dev, struct rtl_qm_rx *rx)
{
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++)
		if (rx->wifi_epp[pool])
			dma_free_coherent(dev, 8 * RTL9617C_WIFI_EPP_BYTES,
				rx->wifi_epp[pool], rx->wifi_epp_dma[pool]);
	for (unsigned int pool = 0; pool < RTL_QM_PE_POOLS; pool++)
		for (unsigned int chunk = 0; chunk < RTL_QM_PE_CHUNKS; chunk++)
			if (rx->wifi_memory[pool][chunk])
				dma_free_coherent(dev, rtl_qm_wifi_chunk_bytes(chunk),
					rx->wifi_memory[pool][chunk], rx->wifi_dma[pool][chunk]);
	dma_free_coherent(dev, RTL_DQ_MEMORY_BYTES, rx->deepq_memory, rx->deepq_dma);
	dma_free_coherent(dev, RTL_QM_RING_BYTES * RTL_QM_VOQS, rx->ring_memory, rx->ring_dma);
	dma_free_noncoherent(dev, RTL_QM_MEMORY_BYTES, rx->memory, rx->memory_dma, DMA_FROM_DEVICE);
}

static void rtl_qm_rx_retire(struct rtl_qm_rx *rx, unsigned int cos,
	u32 command, bool refill)
{
	rx->read[cos] = (rx->read[cos] + RTL_QM_COMMAND_BYTES) & (RTL_QM_RING_BYTES - 1);
	mb();
	writel(rx->read[cos], rx->ni + EPP_READ + cos * 4);
	if (refill) {
		u32 offset = (command & 0xffffff80) - lower_32_bits(rx->memory_dma);
		dma_sync_single_for_device(rx->device, rx->memory_dma + offset,
			RTL_QM_BUFFER_BYTES, DMA_FROM_DEVICE);
		rtl_qm_refill_add(&rx->refill, command);
	}
}

int rtl_qm_rx_poll(struct rtl_qm_rx *rx, struct napi_struct *napi, int budget)
{
	struct net_device *dev = napi->dev;
	int cos;
	int count = 0;
	u64 begin = READ_ONCE(rx->profile) ? ktime_get_ns() : 0;
	bool checksum_enabled = readl(rx->ni + 0x3218) & BIT(16);

	rtl_qm_refill_drain(&rx->refill, rx, rtl_qm_try_push, NAPI_POLL_WEIGHT);
	/* Service PON control traffic before bulk data, matching the stock RX order. */
	for (cos = RTL_QM_VOQS - 1; cos >= 0 && count < budget; cos--) {
		/* Stock ca_ni_rx_napi consumes only the published pointer interval.
		 * Queue status can precede DDR publication and does not republish EPPs. */
		unsigned int wr = readl(rx->ni + EPP_WRITE + cos * 4) & 0xffffff;

		while (rx->read[cos] != wr && count < budget) {
			unsigned int read = rx->read[cos];
			void *entry = rx->ring_memory + cos * RTL_QM_RING_BYTES + read;
			u32 command, length_word, address, offset, h0, h1;
			unsigned int length, header;
			u64 copied = 0;
			struct sk_buff *skb;
			struct net_device *input;
			unsigned int source, wifi_id;
			bool wifi_forwarded;
			bool refill_buffer = false;

			dma_rmb();
			command = get_unaligned_le32(entry);
			length_word = get_unaligned_le32(entry + 4);
			address = command & 0xffffff80;
			if (((command & 15) == EQ_ID || (command & 15) == EQ_ID + 1) &&
			    rx->refill.count[(command & 15) - EQ_ID] == RTL_QM_REFILL_SIZE)
				break;
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
			/* EPP publishes device ownership completion; invalidate only this
			 * payload buffer before reading it through the CPU's cached map. */
			dma_sync_single_for_cpu(rx->device, rx->memory_dma + offset,
				RTL_QM_BUFFER_BYTES, DMA_FROM_DEVICE);
			h0 = get_unaligned_be32(rx->memory + offset + RTL_QM_HEADROOM);
			h1 = get_unaligned_be32(rx->memory + offset + RTL_QM_HEADROOM + 4);
			rx->last_header[0] = h0;
			rx->last_header[1] = h1;
			header = 8 + ((h0 & BIT(31)) ? 8 : 0);
			length = (h1 >> 15) & 0x3fff;
			if (h0 & BIT(31))
				length -= 8;
			wifi_id = rtl_wifi_epp_id(length_word);
			wifi_forwarded = !!wifi_id;
			if (wifi_forwarded) {
				int payload = rtl_wifi_epp_length(length_word, RTL_QM_DATA_BYTES - RTL_WIFI_INTERNAL_BYTES);
				if (payload < 0) { dev->stats.rx_errors++; goto consume; }
				length = payload;
				header = RTL_WIFI_INTERNAL_BYTES;
			}
			if ((command & (BIT(4) | BIT(6))) != (BIT(4) | BIT(6)) ||
			    length < ETH_HLEN || length > RTL_QM_DATA_BYTES - header) {
				dev->stats.rx_errors++;
				goto consume;
			}
			skb = napi_alloc_skb(napi, length);
			if (!skb) {
				dev->stats.rx_dropped++;
				goto consume;
			}
			if (begin) copied = ktime_get_ns();
			memcpy(skb_put(skb, length), rx->memory + offset + RTL_QM_HEADROOM + header, length);
			if (checksum_enabled && rtl_rx_checksum_valid(command, skb->data, length))
				skb->ip_summed = CHECKSUM_UNNECESSARY;
			source = FIELD_GET(GENMASK(14, 9), h1);
			if (!wifi_forwarded && source == RTL_WIFI_SOURCE && (h0 & BIT(31))) {
				u32 metadata = get_unaligned_be32(rx->memory + offset + RTL_QM_HEADROOM + 12);
				/* Full Header CPU reason !=15 identifies an ingress return. */
				if (((metadata >> 8) & 15) != 15) wifi_id = metadata & 255;
			}
			/* Packet bytes and metadata are private now. Return the EPP
			 * credit before GRO or endpoint callbacks can hold up the poll. */
			rtl_qm_rx_retire(rx, cos, command, refill_buffer);
			if (wifi_id) {
				struct rtl9617c_ni_wifi_endpoint *endpoint;
				rcu_read_lock();
				endpoint = wifi_id <= RTL_WIFI_ID_MAX ? rcu_dereference(rx->wifi[wifi_id]) : NULL;
				if (endpoint && netif_running(endpoint->netdev)) {
					u64 dispatch = begin ? ktime_get_ns() : 0;
					if (begin) rx->wifi_copy_ns += dispatch - copied;
					rx->wifi_forwarded += wifi_forwarded;
					rx->wifi_missed += !wifi_forwarded;
					rx->wifi_copy_bytes += length;
					skb->dev = endpoint->netdev;
					skb->priority = h1 & 7;
					endpoint->receive(endpoint, skb, wifi_forwarded);
					if (begin) rx->wifi_dispatch_ns += ktime_get_ns() - dispatch;
				} else {
					dev->stats.rx_dropped++;
					dev_kfree_skb_any(skb);
				}
				rcu_read_unlock();
				continue;
			}
			if (source >= ARRAY_SIZE(rx->lan)) {
				struct rtl9617c_ni_endpoint *endpoint;

				rcu_read_lock();
				endpoint = rcu_dereference(rx->pon);
				if (endpoint && (source == 7 || source >= 32))
					endpoint->receive(endpoint, napi, skb, source |
						(FIELD_GET(GENMASK(26, 18), h0) << 8));
				else {
					dev->stats.rx_dropped++;
					dev_kfree_skb_any(skb);
				}
				rcu_read_unlock();
				continue;
			}
			rcu_read_lock();
			input = rcu_dereference(rx->lan[source]);
			if (!input || !netif_running(input)) {
				dev->stats.rx_dropped++;
				dev_kfree_skb_any(skb);
				rcu_read_unlock();
				continue;
			}
			if (rx->trace_count < ARRAY_SIZE(rx->trace) && length >= 46 &&
			    get_unaligned_be16(skb->data + 12) == ETH_P_IP &&
			    skb->data[23] == IPPROTO_UDP &&
			    get_unaligned_be16(skb->data + 36) == 18081) {
				u32 *trace = rx->trace[rx->trace_count++];

				trace[0] = read; trace[1] = wr;
				trace[2] = command; trace[3] = length_word;
				trace[4] = h0; trace[5] = h1;
				trace[6] = get_unaligned_be32(skb->data + 42);
			}
			skb->protocol = eth_type_trans(skb, input);
			input->stats.rx_packets++;
			input->stats.rx_bytes += length;
			napi_gro_receive(napi, skb);
			rcu_read_unlock();
			continue;
consume:
			rtl_qm_rx_retire(rx, cos, command, refill_buffer);
		}
	}
	/* Every EPP is retired before its BID can return to hardware. */
	rtl_qm_refill_drain(&rx->refill, rx, rtl_qm_try_push, NAPI_POLL_WEIGHT);
	if (begin) {
		rx->host_poll_ns += ktime_get_ns() - begin;
		rx->host_polls++;
	}
	return count;
}

ssize_t rtl_qm_rx_state(struct rtl_qm_rx *rx, char *buf)
{
	u32 drop_info = readl(rx->ni + 0x6814);
	writel(0x82, rx->ni + 0x105c);
	return sysfs_emit(buf, "memory=%pad rmu=%08x es=%08x status=%08x eqreq=%08x pushrdy=%08x epp=%08x wr0=%u rd0=%u last=%08x/%08x hdra=%08x/%08x monitor=%08x ni_demux=%08x rxcount=%u txcount=%u no_buffer=%u last_no_buffer_EQ=%u VOQ=%u fe_drop=%u lenerr=%u td_global=%08x td_port=%08x td_priority=%08x l3te_control=%08x l3te_packets=%u l3te_tail_drop=%u refill_pending=%u refill_retries=%u refill_returned=%u refill_peak=%u\n",
		&rx->memory_dma, readl(rx->ni + RMU_CTRL), readl(rx->ni + ES_CTRL),
		readl(rx->ni + 0x6850), readl(rx->ni + 0x6328 + EQ_ID * 4),
		readl(rx->ni + CPU_READY), readl(rx->ni + 0x659c),
		readl(rx->ni + EPP_WRITE), readl(rx->ni + EPP_READ),
		rx->last_address, rx->last_size, rx->last_header[0], rx->last_header[1],
		readl(rx->ni + 0x1060), readl(rx->ni + 0xa1dc), readl(rx->ni + 0x67d8),
		readl(rx->ni + 0x67ec), readl(rx->ni + 0x6818), drop_info & 15,
		(drop_info >> 16) & 511, readl(rx->ni + 0x681c),
		readl(rx->ni + 0x6824), readl(rx->ni + 0x2d60), readl(rx->ni + 0x2d64),
		readl(rx->ni + 0x2d78), readl(rx->ni + 0x9004),
		readl(rx->ni + 0x9710), readl(rx->ni + 0x971c), READ_ONCE(rx->refill.pending),
		rx->refill.retries, rx->refill.returned, rx->refill.peak);
}

ssize_t rtl_qm_deepq_state(struct rtl_qm_rx *rx, char *buf)
{
	ssize_t length;
	unsigned int eq, cfg;

	length = sysfs_emit(buf, "configured=%u memory=%pad bytes=%u buffers_per_pool=%u map=%08x puc_enable=%08x profile=%08x port16=%08x port31=%08x\n",
		rx->deepq_configured, &rx->deepq_dma, RTL_DQ_MEMORY_BYTES, RTL_DQ_BUFFERS,
		readl(rx->ni + 0x68f8), readl(rx->ni + 0x610c), readl(rx->ni + 0x612c),
		readl(rx->ni + 0x6148 + 16 * 4), readl(rx->ni + 0x6148 + 31 * 4));
	for (eq = RTL_DQ_EQ_PRIVATE; eq <= RTL_DQ_EQ_SHARED; eq++) {
		cfg = 0x61e8 + eq * 20;
		length += scnprintf(buf + length, PAGE_SIZE - length,
			"eq%u=%08x/%08x/%08x/%08x/%08x\n", eq,
			readl(rx->ni + cfg), readl(rx->ni + cfg + 4), readl(rx->ni + cfg + 8),
			readl(rx->ni + cfg + 12), readl(rx->ni + cfg + 16));
	}
	return length;
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
