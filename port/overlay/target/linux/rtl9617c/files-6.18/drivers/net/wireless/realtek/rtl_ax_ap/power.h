/* SPDX-License-Identifier: GPL-2.0-only */
/* Native AP power sequencing based on Realtek's GPL PCIe AP procedures.
 * The host adapter supplies Linux MMIO, polling and delay operations. */
#ifndef RTL_AX_AP_POWER_H
#define RTL_AX_AP_POWER_H
#ifdef __KERNEL__
#include <linux/errno.h>
#else
#include <errno.h>
#endif

#define RTL_AX_8852C 0x52
#define RTL_AX_8192XB 0x53
#define RTL_AX_BIT(n) (1U << (n))
#define RTL_AX_ISO 0x0000
#define RTL_AX_FUNC 0x0002
#define RTL_AX_POWER 0x0004
#define RTL_AX_PAD_POWER 0x0018
#define RTL_AX_EFUSE 0x0030
#define RTL_AX_ISO_EXT 0x0080
#define RTL_AX_PLATFORM 0x0088
#define RTL_AX_LPS 0x0090
#define RTL_AX_DEBUG 0x00cc
#define RTL_AX_CONFIG 0x00f0
#define RTL_AX_CHIP 0x00fc
#define RTL_AX_XTAL 0x0270
#define RTL_AX_DMAC 0x8400
#define RTL_AX_CMAC 0xc000

struct rtl_ax_io {
	void *ctx;
	unsigned char (*read8)(void *ctx, unsigned int reg);
	unsigned short (*read16)(void *ctx, unsigned int reg);
	unsigned int (*read32)(void *ctx, unsigned int reg);
	void (*write8)(void *ctx, unsigned int reg, unsigned char value);
	void (*write16)(void *ctx, unsigned int reg, unsigned short value);
	void (*write32)(void *ctx, unsigned int reg, unsigned int value);
	void (*delay_us)(void *ctx, unsigned int usec);
	int (*poll32)(void *ctx, unsigned int reg, unsigned int mask,
		      unsigned int expected, unsigned int delay_us,
		      unsigned int timeout_us, unsigned int *last);
};

static inline void rtl_ax_update8(const struct rtl_ax_io *io, unsigned int reg,
				 unsigned char clear, unsigned char set)
{
	io->write8(io->ctx, reg, (io->read8(io->ctx, reg) & ~clear) | set);
}

static inline void rtl_ax_update16(const struct rtl_ax_io *io, unsigned int reg,
				  unsigned short clear, unsigned short set)
{
	io->write16(io->ctx, reg, (io->read16(io->ctx, reg) & ~clear) | set);
}

static inline void rtl_ax_update32(const struct rtl_ax_io *io, unsigned int reg,
				  unsigned int clear, unsigned int set)
{
	io->write32(io->ctx, reg, (io->read32(io->ctx, reg) & ~clear) | set);
}

static inline int rtl_ax_xtal_write(const struct rtl_ax_io *io,
	unsigned char address, unsigned char value, unsigned char mask)
{
	unsigned int last;

	io->write32(io->ctx, RTL_AX_XTAL, RTL_AX_BIT(31) |
		    (unsigned int)mask << 16 | (unsigned int)value << 8 | address);
	return io->poll32(io->ctx, RTL_AX_XTAL, RTL_AX_BIT(31), 0,
			  50, 50000, &last);
}

/* DDV physical offsets, including the 0x5e9/0x5ea calibration bytes, are
 * in the 1536-byte Wi-Fi bank. They are not offsets in the DAV bank. */
static inline int rtl_ax_efuse_read(const struct rtl_ax_io *io,
	unsigned int address, unsigned char *output)
{
	unsigned int last;
	int ret;

	if (address >= 1536)
		return -EINVAL;
	rtl_ax_update8(io, RTL_AX_DEBUG, 0, RTL_AX_BIT(2));
	rtl_ax_update16(io, RTL_AX_ISO, 0, RTL_AX_BIT(14));
	io->delay_us(io->ctx, 1000);
	rtl_ax_update16(io, RTL_AX_ISO, 0, RTL_AX_BIT(15));
	rtl_ax_update16(io, RTL_AX_ISO, RTL_AX_BIT(8), 0);
	io->write32(io->ctx, RTL_AX_EFUSE, address << 16);
	ret = io->poll32(io->ctx, RTL_AX_EFUSE, RTL_AX_BIT(29), RTL_AX_BIT(29),
			 1, 1000000, &last);
	if (!ret)
		*output = last & 255;
	/* Release the read power window on both completion and timeout. */
	rtl_ax_update16(io, RTL_AX_ISO, 0, RTL_AX_BIT(8));
	rtl_ax_update16(io, RTL_AX_ISO, RTL_AX_BIT(15), 0);
	io->delay_us(io->ctx, 1000);
	rtl_ax_update16(io, RTL_AX_ISO, RTL_AX_BIT(14), 0);
	rtl_ax_update8(io, RTL_AX_DEBUG, RTL_AX_BIT(2), 0);
	return ret;
}

struct rtl_ax_calibration {
	unsigned char tag, process;
};

struct rtl_ax_identity {
	unsigned char hardware_cv, cv, factory;
	unsigned short adie;
};

static inline void rtl_ax_decode_identity(unsigned int chip,
	unsigned char physical_fv, unsigned short adie, struct rtl_ax_identity *identity)
{
	unsigned char cv = physical_fv >> 4;

	/* The board's get_fv_info leaves the register CV unchanged for an all-zero
	 * efuse byte as well as for the unprogrammed CV nibble 0xf. */
	identity->cv = !physical_fv || cv == 15 ? identity->hardware_cv : cv;
	switch (physical_fv & 15) {
	case 15:
		identity->factory = chip == RTL_AX_8852C ? 'U' : 'T';
		break;
	case 14:
		identity->factory = 'S';
		break;
	case 12:
		identity->factory = chip == RTL_AX_8852C ? 'T' : 'U';
		break;
	default:
		identity->factory = '?';
	}
	identity->adie = adie == 0xffff ? (chip == RTL_AX_8852C ? 0x6758 : 0x6816) : adie;
}

static inline int rtl_ax_read_identity(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_calibration *calibration,
	struct rtl_ax_identity *identity)
{
	unsigned char low, high;
	int ret;

	if (chip == RTL_AX_8852C) {
		ret = rtl_ax_efuse_read(io, 0x5e9, &calibration->tag);
		if (ret)
			return ret;
	}
	ret = rtl_ax_efuse_read(io, 0x5ea, &calibration->process);
	if (ret)
		return ret;
	ret = rtl_ax_efuse_read(io, 0x5f0, &low);
	if (ret)
		return ret;
	ret = rtl_ax_efuse_read(io, 0x5f1, &high);
	if (ret)
		return ret;
	rtl_ax_decode_identity(chip, calibration->process, low | (unsigned short)high << 8,
			       identity);
	return 0;
}

static inline int rtl_ax_ap_power_on(const struct rtl_ax_io *io,
	unsigned int chip, struct rtl_ax_calibration *calibration)
{
	unsigned int last, i;
	int ret;
	const unsigned char xtal_mask[] = {4, 8, 16, 1, 2, 128};
	const unsigned char xtal_value[] = {4, 8, 0, 1, 2, 0};

	if (chip != RTL_AX_8852C && chip != RTL_AX_8192XB)
		return -ENODEV;
	if (chip == RTL_AX_8852C)
		rtl_ax_update32(io, 0x0218, 0, RTL_AX_BIT(16));
	rtl_ax_update32(io, RTL_AX_POWER, RTL_AX_BIT(11) | RTL_AX_BIT(12), 0);
	rtl_ax_update32(io, RTL_AX_POWER, 0, RTL_AX_BIT(18));
	rtl_ax_update32(io, RTL_AX_LPS, 0, RTL_AX_BIT(1));
	if (chip == RTL_AX_8192XB)
		rtl_ax_update32(io, 0x0244, 0, RTL_AX_BIT(25));
	rtl_ax_update32(io, RTL_AX_POWER, RTL_AX_BIT(15), 0);
	rtl_ax_update32(io, RTL_AX_POWER, RTL_AX_BIT(10), 0);
	if (chip == RTL_AX_8852C)
		rtl_ax_update32(io, 0x0200, 7U << 13, 7U << 13);
	ret = io->poll32(io->ctx, RTL_AX_POWER, RTL_AX_BIT(17), RTL_AX_BIT(17),
			 1000, 2000000, &last);
	if (ret)
		return ret;
	rtl_ax_update32(io, RTL_AX_POWER, 0, RTL_AX_BIT(16));
	rtl_ax_update32(io, RTL_AX_POWER, 0, RTL_AX_BIT(8));
	ret = io->poll32(io->ctx, RTL_AX_POWER, RTL_AX_BIT(8), 0,
			 1000, 2000000, &last);
	if (ret)
		return ret;
	if (chip == RTL_AX_8192XB) {
		rtl_ax_update8(io, 0x0016, 0, RTL_AX_BIT(2));
		rtl_ax_update8(io, 0x0016, 0, RTL_AX_BIT(3));
		rtl_ax_update32(io, RTL_AX_DEBUG, 0, RTL_AX_BIT(3));
		rtl_ax_update32(io, RTL_AX_ISO_EXT, RTL_AX_BIT(3), 0);
		rtl_ax_update32(io, RTL_AX_ISO_EXT, RTL_AX_BIT(6), 0);
		rtl_ax_update32(io, RTL_AX_DEBUG, RTL_AX_BIT(3), 0);
	}
	for (i = 0; i < 5; i++)
		rtl_ax_update8(io, RTL_AX_PLATFORM, 1, !(i & 1));
	rtl_ax_update32(io, 0x0070, RTL_AX_BIT(12), 0);
	if (chip == RTL_AX_8852C) {
		rtl_ax_update32(io, RTL_AX_ISO_EXT, RTL_AX_BIT(30), 0);
		rtl_ax_update32(io, RTL_AX_ISO_EXT, 0, RTL_AX_BIT(5));
		rtl_ax_update32(io, 0x0024, 31, 0);
	}
	rtl_ax_update32(io, RTL_AX_PAD_POWER, 0, RTL_AX_BIT(6));
	ret = rtl_ax_xtal_write(io, 0x90, 0x40, 0x40);
	if (ret)
		return ret;
	rtl_ax_update32(io, RTL_AX_PAD_POWER, 0, RTL_AX_BIT(5));
	ret = rtl_ax_xtal_write(io, 0x90, 0x20, 0x20);
	if (ret)
		return ret;
	for (i = 0; i < sizeof(xtal_mask); i++) {
		ret = rtl_ax_xtal_write(io, 0x90, xtal_value[i], xtal_mask[i]);
		if (ret)
			return ret;
	}
	ret = rtl_ax_xtal_write(io, 0x24, chip == RTL_AX_8852C ? 0x10 : 0, 0x70);
	if (ret)
		return ret;
	ret = rtl_ax_xtal_write(io, 0x26, 0, 15);
	if (ret)
		return ret;
	rtl_ax_update32(io, RTL_AX_DEBUG, 0, RTL_AX_BIT(2));
	rtl_ax_update32(io, RTL_AX_ISO, 0, RTL_AX_BIT(8));
	rtl_ax_update32(io, RTL_AX_ISO, RTL_AX_BIT(15), 0);
	io->delay_us(io->ctx, 1000);
	rtl_ax_update32(io, RTL_AX_ISO, RTL_AX_BIT(14), 0);
	rtl_ax_update32(io, RTL_AX_DEBUG, RTL_AX_BIT(2), 0);
	if (chip == RTL_AX_8192XB) {
		rtl_ax_update32(io, 0x02e0, RTL_AX_BIT(11), 0);
		rtl_ax_update32(io, 0x02e4, 0, RTL_AX_BIT(30) | RTL_AX_BIT(1) |
			RTL_AX_BIT(11) | RTL_AX_BIT(16) | RTL_AX_BIT(17) | RTL_AX_BIT(18));
	} else {
		rtl_ax_update32(io, 0x02e4, 0, RTL_AX_BIT(3) | RTL_AX_BIT(6) |
			RTL_AX_BIT(16) | RTL_AX_BIT(17) | RTL_AX_BIT(18));
	}
	if (chip == RTL_AX_8192XB) {
		ret = rtl_ax_efuse_read(io, 0x5e9, &calibration->tag);
		if (ret)
			return ret;
		ret = rtl_ax_efuse_read(io, 0x5ea, &calibration->process);
		if (ret)
			return ret;
		if (calibration->tag != 0xaa && calibration->process == 0xff) {
			if (!(io->read32(io->ctx, RTL_AX_CONFIG) & RTL_AX_BIT(9))) {
				rtl_ax_update32(io, 0x0238, 15U << 2, 10U << 2);
				rtl_ax_update32(io, 0x0200, 15, 9);
				rtl_ax_update32(io, 0x0220, 15, 8);
			} else {
				rtl_ax_update32(io, 0x0220, 15, 10);
				rtl_ax_update32(io, 0x0200, 15, 9);
			}
		}
	}
	rtl_ax_update32(io, RTL_AX_DMAC, 0, 0x7fff8000 |
		(chip == RTL_AX_8192XB ? RTL_AX_BIT(14) : 0));
	rtl_ax_update32(io, RTL_AX_CMAC, 0, 0x7000803f);
	return 0;
}

static inline int rtl_ax_ap_power_off(const struct rtl_ax_io *io)
{
	unsigned int i, last;
	int ret;
	const unsigned char address[] = {0x90, 0x90, 0x90, 0x80, 0x81, 0x90, 0x90, 0x90};
	const unsigned char mask[] = {16, 8, 4, 1, 1, 128, 2, 1};
	const unsigned char value[] = {16, 0, 0, 0, 0, 128, 0, 0};

	for (i = 0; i < sizeof(address); i++) {
		ret = rtl_ax_xtal_write(io, address[i], value[i], mask[i]);
		if (ret)
			return ret;
	}
	rtl_ax_update32(io, RTL_AX_POWER, 0, RTL_AX_BIT(16));
	rtl_ax_update32(io, 0x02f0, RTL_AX_BIT(17), 0);
	rtl_ax_update8(io, RTL_AX_FUNC, 3, 0);
	rtl_ax_update32(io, RTL_AX_ISO_EXT, RTL_AX_BIT(16) | RTL_AX_BIT(17), 0);
	rtl_ax_update32(io, RTL_AX_PAD_POWER, RTL_AX_BIT(5), 0);
	ret = rtl_ax_xtal_write(io, 0x90, 0, 0x20);
	if (ret)
		return ret;
	rtl_ax_update32(io, RTL_AX_PAD_POWER, RTL_AX_BIT(6), 0);
	ret = rtl_ax_xtal_write(io, 0x90, 0, 0x40);
	if (ret)
		return ret;
	rtl_ax_update32(io, RTL_AX_POWER, 0, RTL_AX_BIT(9));
	ret = io->poll32(io->ctx, RTL_AX_POWER, RTL_AX_BIT(9), 0,
			 1000, 2000000, &last);
	if (!ret)
		rtl_ax_update32(io, RTL_AX_LPS, RTL_AX_BIT(8), 0);
	return ret;
}
#endif
