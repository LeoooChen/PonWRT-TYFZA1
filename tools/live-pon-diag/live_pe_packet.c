// SPDX-License-Identifier: GPL-2.0-only
/* NI VP9 -> private CPU3/EPP24 -> native PE1 -> EQ6 buffer return. */
#include <linux/etherdevice.h>
#include "pe_probe.h"
#include "pe-rx-code.h"

#define EQ 6
#define EQ_CFG (0x61e8 + EQ * 20)
#define VP 9
#define RING 0x6000
#define BUFFER_COUNT 128
#define BID_START 1536
#define PACKETS 2048
static struct pe_probe pe;
static void __iomem *ni, *dma, *vp;
static const unsigned int registers[] = {
	EQ_CFG, EQ_CFG + 4, EQ_CFG + 8, EQ_CFG + 12, EQ_CFG + 16,
	0x6144, 0x6154, 0x61d4, 0x6564, 0x6598, 0x6604, 0x7160, 0x7260, 0x6108,
};
static u32 saved[ARRAY_SIZE(registers)], arb[4], attrs[2][2], vp_saved[5];
static bool ni_active, vp_active;

static int ni_access(unsigned int offset, u32 command)
{
	u32 status;
	writel(command, ni + offset);
	return readl_poll_timeout(ni + offset, status, !(status & BIT(31)), 1, 100000);
}

static int attr_write(unsigned int index, const u32 *value)
{
	writel(value[0], ni + 0x66ac);
	writel(value[1], ni + 0x66a8);
	return ni_access(0x66a4, BIT(31) | BIT(30) | index);
}

static void release(void)
{
	unsigned int i;
	u32 status;

	if (vp_active) {
		writel(0, vp);
		readl_poll_timeout(vp + 0x1c, status, readl(vp + 0x18) == status, 1, 100000);
		writel(vp_saved[1], vp + 8);
		writel(vp_saved[2], vp + 12);
		writel(BIT(31) | BIT(30), vp + 4);
		readl_poll_timeout(vp + 4, status, !(status & BIT(31)), 1, 100000);
		writel(vp_saved[3], vp + 0x64);
		writel(vp_saved[4], vp + 0x78);
		writel((readl(dma + 0x14) & ~(3U << (VP * 2))) |
			(vp_saved[0] & (3U << (VP * 2))), dma + 0x14);
	}
	pe_probe_stop(&pe);
	if (ni_active) {
		for (i = 0; i < ARRAY_SIZE(registers); i++)
			writel(registers[i] == 0x7160 ? readl(ni + 0x7060) : saved[i], ni + registers[i]);
		for (i = 0; i < 4; i++) {
			writel(arb[i], ni + 0x1658);
			ni_access(0x1654, BIT(31) | BIT(30) | (i * 64 + 19));
		}
		attr_write(EQ, attrs[0]);
		attr_write(51, attrs[1]);
	}
	pe_probe_free(&pe);
	if (dma) iounmap(dma);
	if (ni) iounmap(ni);
}

static int prepare_ni(void)
{
	static const u32 attr[] = { 0x0a008060, 0 };
	unsigned int i;
	int ret;

	ni = ioremap(0x4f4300000ULL, 0x10000);
	dma = ioremap(0x4f7001000ULL, 0x1000);
	if (!ni || !dma) return -ENOMEM;
	vp = dma + 0x100 + VP * 0xa0;
	pr_info("native PE packet pre EQ6=%08x/%08x EPP24=%u/%u base=%08x VP9=%08x/%u/%u\n",
		readl(ni + EQ_CFG), readl(ni + EQ_CFG + 4), readl(ni + 0x7160), readl(ni + 0x7060),
		readl(ni + 0x7260), readl(vp), readl(vp + 0x1c), readl(vp + 0x18));
	if (!(readl(ni + EQ_CFG) & 1) || readl(ni + EQ_CFG + 4) != (BUFFER_COUNT << 16 | BID_START) ||
	    readl(ni + 0x7160) != readl(ni + 0x7060) ||
	    readl(vp) || readl(vp + 0x1c) != readl(vp + 0x18)) return -EBUSY;
	for (i = 0; i < ARRAY_SIZE(registers); i++) saved[i] = readl(ni + registers[i]);
	for (i = 0; i < 4; i++) {
		ret = ni_access(0x1654, BIT(31) | (i * 64 + 19));
		if (ret) return ret;
		arb[i] = readl(ni + 0x1658);
	}
	for (i = 0; i < 2; i++) {
		ret = ni_access(0x66a4, BIT(31) | (i ? 51 : EQ));
		if (ret) return ret;
		attrs[i][0] = readl(ni + 0x66ac); attrs[i][1] = readl(ni + 0x66a8);
	}
	ni_active = true;
	writel(EQ | 15 << 4, ni + 0x6144);
	writel(7, ni + 0x6154);
	writel(0x00020002, ni + 0x61d4);
	writel(0, ni + 0x6564);
	writel(64U << 8 | 0xf2, ni + 0x6598);
	writel(7, ni + 0x6604);
	writel(pe.dma + RING, ni + 0x7260);
	writel(readl(ni + 0x7060), ni + 0x7160);
	put_unaligned_le32(pe.dma + RING, pe.memory + PE_PROBE_MAILBOX + 8);
	put_unaligned_le32(readl(ni + 0x7160), pe.memory + PE_PROBE_MAILBOX + 12);
	ret = attr_write(EQ, attr); if (ret) return ret;
	ret = attr_write(51, attr); if (ret) return ret;
	for (i = 0; i < 4; i++) {
		writel(9, ni + 0x1658);
		ret = ni_access(0x1654, BIT(31) | BIT(30) | (i * 64 + 19));
		if (ret) return ret;
	}
	for (i = 0; i < 16; i++) {
		ret = ni_access(0x66b0, BIT(31) | (BID_START + i));
		if (ret) return ret;
		pr_info("native PE packet BID=%u address_field=%08x\n", BID_START + i, readl(ni + 0x66b4));
	}
	writel(readl(ni + 0x6108) | BIT(3), ni + 0x6108);
	return 0;
}

static int __init packet_init(void)
{
	u32 address, status;
	unsigned int i, first, received, sequence;
	int ret = pe_probe_alloc(&pe);

	if (ret) goto out;
	memcpy(pe.memory, pe_rx_program, sizeof(pe_rx_program));
	address = ((u32)pe.dma + PE_PROBE_MAILBOX) | 0xa0000000U;
	put_unaligned_le32(pe_rx_program[0] | address >> 16, pe.memory);
	put_unaligned_le32(pe_rx_program[1] | (address & 0xffff), pe.memory + 4);
	put_unaligned_le32(pe_rx_program[0x380 / 4] | address >> 16, pe.memory + 0x380);
	put_unaligned_le32(pe_rx_program[0x384 / 4] | (address & 0xffff), pe.memory + 0x384);
	put_unaligned_le32(1, pe.memory + PE_PROBE_MAILBOX + 16);
	ret = prepare_ni(); if (ret) goto out;
	dma_wmb();
	ret = pe_probe_start(&pe); if (ret) goto out;
	first = readl(vp + 0x1c) & 63;
	vp_saved[0] = readl(dma + 0x14); vp_saved[1] = readl(vp + 8);
	vp_saved[2] = readl(vp + 12); vp_saved[3] = readl(vp + 0x64); vp_saved[4] = readl(vp + 0x78);
	vp_active = true;
	writel(0, vp + 0x64);
	writel(2, vp + 8); writel((u32)pe.dma + 0x2000 + 6, vp + 12);
	writel(BIT(31) | BIT(30), vp + 4);
	ret = readl_poll_timeout(vp + 4, status, !(status & BIT(31)), 1, 100000);
	if (ret) goto out;
	writel((vp_saved[0] & ~(3U << 18)) | (1U << 18), dma + 0x14);
	writel(BIT(9), vp + 0x78);
	writel(1, vp);
	for (sequence = 0; sequence < PACKETS; sequence += 8) {
		for (i = 0; i < 8; i++) {
			unsigned int slot = (first + sequence + i) & 63;
			u8 *data = pe.memory + 0x3000 + slot * 128;
			put_unaligned_le32(0, data); put_unaligned_le32(60, data + 4);
			put_unaligned_le32(BIT(13), data + 8);
			put_unaligned_le32(60U << 15 | 16U << 9 | 19U << 3 | BIT(29), data + 12);
			memcpy(data + 16, "\x02\x57\x46\x4e\x45\x02\x02\x57\x46\x4e\x45\x01\x88\xb5", 14);
			memcpy(data + 30, "TYFPE-NI", 8);
			put_unaligned_be32(sequence + i, data + 38);
			memset(data + 42, (sequence + i + 0x40) & 255, 34);
			put_unaligned_le32((u32)pe.dma + 0x3000 + slot * 128, pe.memory + 0x2000 + slot * 8);
			put_unaligned_le32(0x61000002U | (76 << 8), pe.memory + 0x2004 + slot * 8);
		}
		dma_wmb();
		writel((first + sequence + 8) & 63, vp + 0x18); writel(1, vp + 0x68);
		ret = readl_poll_timeout(vp + 0x1c, status, (status & 63) == ((first + sequence + 8) & 63), 10, 100000);
		if (ret) break;
		ret = read_poll_timeout(get_unaligned_le32, received, received == sequence + 8,
			10, 500000, false, pe.memory + PE_PROBE_MAILBOX + 20);
		if (ret) break;
		dma_rmb();
		for (i = 0; i < 8; i++) {
			u8 *trace = pe.memory + PE_PROBE_MAILBOX + 256 + i * 128;
			unsigned int header = trace[32] & 0x80 ? 48 : 40;
			unsigned int slot = (first + sequence + i) & 63;
			if (memcmp(trace + header, pe.memory + 0x3000 + slot * 128 + 16, 60)) {
				pr_err("native PE packet payload mismatch sequence=%u\n", sequence + i);
				ret = -EBADMSG; break;
			}
		}
		if (ret) break;
	}
	dma_rmb();
	pr_info("native PE packet result=%d received=%u heartbeat=%u EPP24=%u/%u EQ6_inactive=%u exception=%08x/%08x\n",
		ret, get_unaligned_le32(pe.memory + PE_PROBE_MAILBOX + 20),
		get_unaligned_le32(pe.memory + PE_PROBE_MAILBOX + 4), readl(ni + 0x7160),
		readl(ni + 0x7060), readl(ni + 0x6328 + EQ * 4) & 0x3fff,
		get_unaligned_le32(pe.memory + PE_PROBE_MAILBOX + 40), get_unaligned_le32(pe.memory + PE_PROBE_MAILBOX + 44));
	for (i = 0; i < 8; i++) {
		u8 *trace = pe.memory + PE_PROBE_MAILBOX + 256 + i * 128;
		unsigned int header = trace[32] & 0x80 ? 48 : 40;
		unsigned int slot = (first + sequence - 8 + i) & 63;
		u8 *expected = pe.memory + 0x3000 + slot * 128 + 16;
		bool exact = !memcmp(trace + header, expected, 60);
		unsigned int entry = (saved[11] + (sequence - 8 + i) * 8) & 1023;

		pr_info("native PE packet ARM_EPP=%u command=%08x/%08x\n", i,
			get_unaligned_le32(pe.memory + RING + entry), get_unaligned_le32(pe.memory + RING + entry + 4));

		pr_info("native PE packet trace=%u EPP=%08x/%08x exact=%u header=%*phN frame=%*phN\n",
			i, get_unaligned_le32(trace), get_unaligned_le32(trace + 4), exact,
			header - 32, trace + 32, 60, trace + header);
		if (!exact && !ret) ret = -EBADMSG;
	}
out:
	release();
	return ret;
}
static void __exit packet_exit(void) {}
module_init(packet_init);
module_exit(packet_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 independent NI to PE packet consumer");
