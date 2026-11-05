/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_PCIE_SEQUENCE_H
#define RTL9617C_PCIE_SEQUENCE_H

/* Board PCIe has one lane. These are the Taurus GPL calibration operations;
 * callbacks allow the same production sequence to be checked offline. */
struct rtl_pcie_io {
	void *context;
	unsigned int (*read)(void *context, unsigned int reg);
	void (*write)(void *context, unsigned int reg, unsigned int value);
	void (*delay)(void *context);
	int (*wait)(void *context, unsigned int reg, unsigned int mask);
};
static inline void rtl_pcie_serdes_write(const struct rtl_pcie_io *io,
					unsigned int reg, unsigned int value)
{
	io->write(io->context, reg, value);
	io->delay(io->context);
}
static inline void rtl_pcie_serdes_update(const struct rtl_pcie_io *io,
	unsigned int reg, unsigned int clear, unsigned int set)
{
	rtl_pcie_serdes_write(io, reg, (io->read(io->context, reg) & ~clear) | set);
}
static inline void rtl_pcie_serdes_toggle(const struct rtl_pcie_io *io,
	unsigned int low, unsigned int high)
{
	rtl_pcie_serdes_write(io, 0x24, low);
	rtl_pcie_serdes_write(io, 0x24, high);
	rtl_pcie_serdes_write(io, 0x124, low);
	rtl_pcie_serdes_write(io, 0x124, high);
}
static inline int rtl_pcie_serdes_calibrate(const struct rtl_pcie_io *io,
	unsigned int controller, char profile)
{
	unsigned int generation, reg, value;
	int ret;

	if (profile == 'A') {
		rtl_pcie_serdes_toggle(io, 0x500c, 0x520c);
		return 0;
	}
	/* The original controller1 path skips this controller0 calibration. */
	if (controller)
		return 0;
	rtl_pcie_serdes_toggle(io, 0x500c, 0x520c);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x34 + generation * 0x100, 0, 1U << 6);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x08 + generation * 0x100, 0, 1U << 9);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_write(io, 0x40 + generation * 0x100, 0x194);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0xbc + generation * 0x100, 0, 1U << 10);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x6c + generation * 0x100, 3U << 5, 0);
	ret = io->wait(io->context, 0x7c, 1U << 4);
	if (ret)
		return ret;
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x08 + generation * 0x100, 1U << 9, 0);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x6c + generation * 0x100, 3U << 5, 0);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_write(io, 0x40 + generation * 0x100, 0x1a4);
	value = io->read(io->context, 0x7c) & 31;
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x0c + generation * 0x100, 31U << 1, value << 1);
	rtl_pcie_serdes_toggle(io, 0x501c, 0x521c);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x34 + generation * 0x100, 1U << 6, 0);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0xbc + generation * 0x100, 1U << 10, 0);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x6c + generation * 0x100, 3U << 5, 0);
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_write(io, 0x40 + generation * 0x100, 0x0c);
	value = (io->read(io->context, 0x7c) >> 1) & 15;
	for (generation = 0; generation < 2; generation++)
		rtl_pcie_serdes_update(io, 0x2c + generation * 0x100, 15U << 5, value << 5);
	for (generation = 0; generation < 2; generation++) {
		reg = 0x34 + generation * 0x100;
		rtl_pcie_serdes_update(io, reg, 1U << 13, 0);
	}
	return 0;
}

/* SoC coherent ATU is separate from DesignWare iATU. Addresses are64K
 * page numbers. CCI alias4 is from this board's stock coherent-hi-addr. */
static inline int rtl_pcie_atu_ddr(const struct rtl_pcie_io *io,
	unsigned int pages, unsigned int high_alias)
{
	int ret;

	io->write(io->context, 0x04, 0);
	io->write(io->context, 0x08, ~(pages - 1));
	io->write(io->context, 0x0c, high_alias << 16);
	io->write(io->context, 0x10, 0x07ff0022); /* Nonsecure/cache/user override. */
	io->write(io->context, 0x14, 0x80); /* Outer shareable ReadOnce. */
	io->write(io->context, 0x18, 0x80); /* Outer shareable WriteUnique. */
	io->write(io->context, 0, 0x80000001);
	ret = io->wait(io->context, 0, 1U << 31);
	if (ret) return ret;
	/* PE writes RXBDs directly to DDR. A distinct PCIe bus alias bypasses
	 * CCI, whose ReadOnce can otherwise return an old ARM cache line. */
	io->write(io->context, 0x04, 0x8000);
	io->write(io->context, 0x08, ~(pages - 1));
	io->write(io->context, 0x0c, 0);
	io->write(io->context, 0x10, 0x07000022);
	io->write(io->context, 0x14, 0);
	io->write(io->context, 0x18, 0);
	io->write(io->context, 0, 0x81000003);
	return io->wait(io->context, 0, 1U << 31);
}
#endif
