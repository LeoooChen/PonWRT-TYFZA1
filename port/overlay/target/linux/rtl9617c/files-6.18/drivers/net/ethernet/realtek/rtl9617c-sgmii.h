/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Taurus port4 sequence from Realtek's eth_ni_taurus.c, GPL-2.0+. */
#ifndef RTL9617C_SGMII_H
#define RTL9617C_SGMII_H

static inline unsigned int rtl_sgmii_pcs(unsigned int old, unsigned int speed)
{
	unsigned int code = speed == 2500 ? 5 : speed == 1000 ? 2 : speed == 100 ? 1 : 0;
	unsigned int ability = speed == 2500 ? 0x1001 : 0x14000 | code;

	old &= ~((31U << 2) | (0x1ffffU << 9) | (7U << 26));
	return old | (speed == 2500 ? 0x12U : 2U) << 2 | ability << 9 | code << 26 |
		(speed == 2500 ? 1U << 29 : 0);
}

static inline void rtl_sgmii_link(void __iomem *global, void __iomem *analog,
				 unsigned int speed)
{
	static const unsigned int regs[] = {4, 12, 24, 40, 64, 68, 72, 124, 76};
	static const unsigned int masks[] = {63, 62, 31, 0x7000, ~0U, ~0U, ~0U, ~0U, 0xf000};
	static const unsigned int values[] = {0, 56, 31, 0x7000, 0x34aa, 0x88aa, 0x53, 0x8008, 0};
	unsigned int bank = speed == 2500 ? 0x400 : 0x200, i;
	u32 value = readl(global + 0xc4);

	writel(value & ~(3U << 25), global + 0xc4);
	value = readl(global + 0xa0);
	writel(value | BIT(5) | BIT(8), global + 0xa0);
	udelay(10);
	writel(value & ~(BIT(5) | BIT(8)), global + 0xa0);
	udelay(10);
	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		value = i ? values[i] : speed == 2500 ? 0x10 : 0x30;
		writel((readl(analog + bank + regs[i]) & ~masks[i]) | value,
		       analog + bank + regs[i]);
	}
	writel((readl(global + 0xc4) & ~(3U << 25)) |
	       (speed == 2500 ? 2U : 1U) << 25, global + 0xc4);
	writel(rtl_sgmii_pcs(readl(global + 0xd4), speed), global + 0xd4);
}
#endif
